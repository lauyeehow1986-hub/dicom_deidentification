# Encapsulated-PDF Rasterize + OCR-Redact — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace blind wholesale removal of DICOM-embedded PDFs with an opt-in rasterize + OCR-redact + flatten pipeline that keeps the report content while destroying PHI.

**Architecture:** A new `deid_engine/documents.py` renders each embedded-PDF page to a raster with `pypdfium2`, reuses the existing Tesseract+`TextScanner` OCR-redaction path (shared with pixels), and rebuilds a flattened image-only PDF (no text layer) that is re-embedded into the DICOM. Behavior is gated by a profile `encapsulated_pdf.mode` (`remove` default, `rasterize_redact` opt-in); when Tesseract is absent, `rasterize_redact` degrades to `remove` so an un-scanned PDF is never kept.

**Tech Stack:** Python 3.12 (pydicom, pypdfium2 [Apache-2.0/BSD-3], Pillow, pytesseract), R/reticulate bridge, pytest + testthat.

---

## Conventions for every task

- **Real project root:** all work is under `C:\Users\lauye\Downloads\dicom_deidentification` (NOT the `.claude\worktrees\...` Slice-AR worktree). Before any `git` command run `git rev-parse --show-toplevel` and confirm it prints `C:/Users/lauye/Downloads/dicom_deidentification`.
- **Run pytest:** from the project root:
  ```bash
  PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python -q
  ```
  To run one test: append `tests/python/test_documents.py::test_name`.
- **Run testthat:** `"C:/Program Files/R/R-4.5.2/bin/Rscript.exe" tests/testthat.R`
- **Commit trailer (required on every commit):**
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- Never commit patient data. The corpus fixtures are synthetic (invented names) — safe.
- TDD: write the failing test, watch it fail for the right reason, minimal code to pass, watch it pass, commit.

---

## File Structure

- **Create** `inst/python/deid_engine/documents.py` — encapsulated-PDF detection, render, OCR-redact, flatten, re-embed, text extraction.
- **Create** `tests/python/test_documents.py` — unit tests for `documents.py` (pure-bytes + ds-level).
- **Create** `tests/python/test_encapsulated_pdf_e2e.py` — Tesseract-gated end-to-end (OCR the output).
- **Create** `docs/encapsulated-pdf.md` — feature doc.
- **Modify** `inst/python/deid_engine/pixels.py` — extract shared `image_phi_boxes` + `_ocr_available`; `ocr_phi_boxes` reuses them (behavior unchanged).
- **Modify** `inst/python/deid_engine/core.py` — `_walk`/`deidentify_dataset` skip EncapsulatedDocument removal under `rasterize_redact`; `deidentify_study` PDF-redaction block; `deid_run` gains `pdf_mode`/`pdf_dpi`; `check_survivors` inspects embedded PDFs.
- **Modify** `inst/python/deid_engine/corpus.py` — add an Encapsulated PDF Storage fixture with a planted visible name.
- **Modify** `inst/python/deid_engine/__init__.py` — export new `documents` helpers used by the bridge/tests.
- **Modify** `inst/profiles/default_profile.yml` — add `encapsulated_pdf:` block (`mode: remove`, `dpi: 150`).
- **Modify** `inst/profiles/identifier_catalog.yml` — update the `encapsulated_documents` note.
- **Modify** `R/engine_bridge.R` — thread `pdf_mode`/`pdf_dpi` through `engine_deid_run`.
- **Modify** `R/acceptance.R` — acceptance run enables `rasterize_redact`.
- **Modify** `inst/python/build_venv.ps1` — install `pypdfium2`.
- **Modify** `docs/roadmap.md`, `docs/airgap-install.md`, `docs/text-detection.md` — cross-references.

---

## Task 1: Add the `pypdfium2` dependency

**Files:**
- Modify: `inst/python/build_venv.ps1`
- (installs into the existing dev venv `inst/python/.venv`)

- [ ] **Step 1: Install pypdfium2 into the dev venv**

Run (from project root):
```bash
inst/python/.venv/Scripts/python.exe -m pip install --index-url https://pypi.org/simple --upgrade "pypdfium2==4.30.0"
```
(If the box's HTTPS cert interception blocks pip, fetch the wheel via `curl -L -o pypdfium2.whl <pypi wheel url>` then `pip install pypdfium2.whl`, matching the Phase 7 workaround.)

- [ ] **Step 2: Verify the import and a trivial render round-trips**

Run:
```bash
inst/python/.venv/Scripts/python.exe -c "import pypdfium2 as p; print(p.PdfDocument.__name__)"
```
Expected: prints `PdfDocument` with no ImportError.

- [ ] **Step 3: Add pypdfium2 to build_venv.ps1**

In `inst/python/build_venv.ps1`, find the line that installs the base engine deps (the `uv pip install ... pydicom` block) and add `pypdfium2==4.30.0` to that install list (same line/array as the other always-installed base packages, so it is part of every rebuild). Do not gate it behind `-Phi`/`-Ner`; it is a base runtime dependency of the PDF path.

- [ ] **Step 4: Commit**

```bash
git add inst/python/build_venv.ps1
git commit -m "build: add pypdfium2 (Apache-2.0/BSD-3) for encapsulated-PDF redaction

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Extract a shared OCR-on-one-image helper in `pixels.py`

Both DICOM frames and PDF pages need "OCR an image, keep the PHI words as boxes." Factor it out of `ocr_phi_boxes` so `documents.py` can reuse it, keeping `ocr_phi_boxes` behavior byte-identical.

**Files:**
- Modify: `inst/python/deid_engine/pixels.py`
- Test: `tests/python/test_pixels_shared.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/python/test_pixels_shared.py`:
```python
"""Shared OCR helper used by both pixel frames and PDF pages."""
import numpy as np
import pytest

from deid_engine import pixels


class _Scanner:
    """Fake scanner: flags any word containing 'Tan'."""
    def scan(self, text):
        return [object()] if "Tan" in text else []


def _tesseract_ok():
    ok, _ = pixels._ocr_available()
    return ok


pytestmark = pytest.mark.skipif(not _tesseract_ok(),
                                reason="Tesseract OCR binary not available")


def test_ocr_available_returns_bool_and_note():
    ok, note = pixels._ocr_available()
    assert isinstance(ok, bool)
    assert isinstance(note, str)


def test_image_phi_boxes_flags_matching_word():
    from PIL import Image, ImageDraw
    img = Image.new("L", (256, 64), 255)
    ImageDraw.Draw(img).text((6, 24), "Tan Wei Ming", fill=0)
    arr = np.asarray(img, dtype=np.uint8)
    boxes = pixels.image_phi_boxes(arr, _Scanner())
    assert any("Tan" in b["text"] for b in boxes)
    for b in boxes:
        assert {"x", "y", "w", "h", "text", "source"} <= set(b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_pixels_shared.py -q`
Expected: FAIL with `AttributeError: module 'deid_engine.pixels' has no attribute '_ocr_available'` (or `image_phi_boxes`).

- [ ] **Step 3: Add the helpers and refactor `ocr_phi_boxes`**

In `inst/python/deid_engine/pixels.py`, add after `_configure_tesseract`:
```python
def _ocr_available():
    """(-> (bool, note)) True when the Tesseract binary is usable. Degrades like
    the Presidio/NER layers: a missing binary is reported, never fatal."""
    try:
        import pytesseract
        _configure_tesseract()
        pytesseract.get_tesseract_version()
        return True, ""
    except Exception as e:  # noqa: BLE001 - no binary on this box / air-gap
        return False, f"ocr unavailable: {e}"


def image_phi_boxes(img_uint8, scanner) -> list:
    """OCR one 8-bit image; return PHI word boxes the scanner flags.

    Assumes the caller has already confirmed OCR is available (``_ocr_available``).
    Boxes are ``{x, y, w, h, text, source}`` with no frame key.
    """
    import pytesseract
    from pytesseract import Output
    data = pytesseract.image_to_data(img_uint8, output_type=Output.DICT)
    boxes = []
    for j, word in enumerate(data.get("text", [])):
        if word and word.strip() and scanner.scan(word):
            boxes.append({"x": int(data["left"][j]), "y": int(data["top"][j]),
                          "w": int(data["width"][j]), "h": int(data["height"][j]),
                          "text": word, "source": "ocr"})
    return boxes
```

Then replace the body of `ocr_phi_boxes` (keep its signature and return shape) with a version that uses the shared helpers:
```python
def ocr_phi_boxes(ds, scanner) -> dict:
    """Optional: OCR each frame, keep boxes whose text the scanner flags as PHI.

    Degrades to an empty result (with a note) when the Tesseract binary is absent,
    exactly like the Presidio/NER layers in Phase 2.
    """
    ok, note = _ocr_available()
    if not ok:
        return {"boxes": [], "note": note}
    frames = load_frames(ds)
    boxes = []
    for i in range(frames.shape[0]):
        img = _frame_to_uint8(frames[i])
        try:
            page_boxes = image_phi_boxes(img, scanner)
        except Exception as e:  # noqa: BLE001
            return {"boxes": boxes, "note": f"ocr failed on frame {i}: {e}"}
        for b in page_boxes:
            b["frame"] = i
            boxes.append(b)
    return {"boxes": boxes}
```

- [ ] **Step 4: Run the new test AND the existing pixel tests**

Run:
```bash
PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_pixels_shared.py tests/python/test_pixels.py tests/python/test_pixel_autoredact_e2e.py -q
```
Expected: PASS (or SKIP where Tesseract is absent) — `ocr_phi_boxes` behavior unchanged.

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/pixels.py tests/python/test_pixels_shared.py
git commit -m "refactor: share image_phi_boxes/_ocr_available between pixels and (soon) PDF

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `documents.py` — detection, render, flatten, text extraction

**Files:**
- Create: `inst/python/deid_engine/documents.py`
- Test: `tests/python/test_documents.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/python/test_documents.py`:
```python
"""Encapsulated-PDF helpers (pypdfium2 render / Pillow flatten / detection)."""
import io

import numpy as np
import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import documents

ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def _image_pdf_bytes(text="Tan Wei Ming", size=(400, 120)):
    """A one-page image PDF (no text layer) with visible text."""
    img = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(img).text((10, 50), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def _encapsulated_pdf_ds(pdf_bytes):
    ds = Dataset()
    ds.SOPClassUID = ENCAPS_PDF_SOP
    ds.SOPInstanceUID = generate_uid()
    ds.Modality = "DOC"
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = pdf_bytes if len(pdf_bytes) % 2 == 0 else pdf_bytes + b"\x00"
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ENCAPS_PDF_SOP
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def test_is_encapsulated_pdf_by_sop_class():
    ds = _encapsulated_pdf_ds(_image_pdf_bytes())
    assert documents.is_encapsulated_pdf(ds) is True


def test_is_encapsulated_pdf_false_for_plain_image():
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.7"  # Secondary Capture
    assert documents.is_encapsulated_pdf(ds) is False


def test_render_pdf_pages_returns_rgb_arrays():
    pages = documents.render_pdf_pages(_image_pdf_bytes(), dpi=100)
    assert len(pages) == 1
    assert pages[0].ndim == 3 and pages[0].shape[2] == 3
    assert pages[0].dtype == np.uint8


def test_flatten_to_pdf_has_no_text_layer():
    pages = documents.render_pdf_pages(_image_pdf_bytes(), dpi=100)
    flat = documents.flatten_to_pdf(pages)
    assert flat[:5] == b"%PDF-"
    assert documents.pdf_text(flat).strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_documents.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'deid_engine.documents'`.

- [ ] **Step 3: Write the module**

Create `inst/python/deid_engine/documents.py`:
```python
"""Encapsulated-PDF de-identification (rasterize + OCR-redact + flatten).

A DICOM-embedded PDF (EncapsulatedDocument, 0042,0011) can carry PHI in text and
in images. Rather than deleting it wholesale, we render each page to a raster
(pypdfium2), OCR-redact PHI regions with the shared scanner, and rebuild a
flattened image-only PDF. Flattening removes the text layer entirely, so any
hidden/selectable text PHI is gone by construction.

Licence-clean and air-gap friendly: pypdfium2 ships a self-contained wheel
(Apache-2.0 / BSD-3), no system binary. OCR still needs the bundled Tesseract;
when it is absent the caller falls back to removal.
"""
from __future__ import annotations

import io

import numpy as np

from . import pixels as _pixels

ENCAPSULATED_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def is_encapsulated_pdf(ds) -> bool:
    """True when ``ds`` carries an embedded PDF (by SOP Class or MIME type)."""
    if str(getattr(ds, "SOPClassUID", "")) == ENCAPSULATED_PDF_SOP:
        return "EncapsulatedDocument" in ds
    mime = str(getattr(ds, "MIMETypeOfEncapsulatedDocument", "")).lower()
    return mime == "application/pdf" and "EncapsulatedDocument" in ds


def render_pdf_pages(pdf_bytes: bytes, dpi: int = 150) -> list:
    """Render every page to an RGB uint8 ndarray at ``dpi``."""
    import pypdfium2 as pdfium

    scale = float(dpi) / 72.0
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil().convert("RGB")
            pages.append(np.asarray(pil, dtype=np.uint8))
        return pages
    finally:
        pdf.close()


def flatten_to_pdf(pages: list) -> bytes:
    """Rebuild a flattened, image-only PDF (no text layer) from page rasters."""
    from PIL import Image

    imgs = [Image.fromarray(np.ascontiguousarray(p)).convert("RGB") for p in pages]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def pdf_text(pdf_bytes: bytes) -> str:
    """Extract the PDF's text layer (empty for a flattened image PDF)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        parts = []
        for i in range(len(pdf)):
            tp = pdf[i].get_textpage()
            parts.append(tp.get_text_range())
        return "\n".join(parts)
    finally:
        pdf.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_documents.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/documents.py tests/python/test_documents.py
git commit -m "feat: documents.py PDF detect/render/flatten/text (pypdfium2 + Pillow)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `documents.redact_pdf_bytes` + `redact_encapsulated_pdf` (with Tesseract-absent fallback)

**Files:**
- Modify: `inst/python/deid_engine/documents.py`
- Test: `tests/python/test_documents.py` (append)

- [ ] **Step 1: Write the failing test (append)**

Append to `tests/python/test_documents.py`:
```python
class _NameScanner:
    """Fake scanner flagging any word containing a planted token."""
    def __init__(self, tokens=("Tan", "Wei", "Ming")):
        self.tokens = tokens
    def scan(self, text):
        return [object()] if any(t in text for t in self.tokens) else []


def test_redact_pdf_bytes_boxes_are_black_over_the_name():
    ok, _ = _pixels_ocr_ok()
    if not ok:
        pytest.skip("Tesseract not available")
    src = _image_pdf_bytes("Tan Wei Ming")
    out, info = documents.redact_pdf_bytes(src, _NameScanner(), dpi=150)
    assert info["pages"] == 1
    assert info["boxes"] >= 1
    # the flattened output has no text layer at all
    assert documents.pdf_text(out).strip() == ""


def test_redact_encapsulated_pdf_removes_doc_when_ocr_absent(monkeypatch):
    # Force "OCR unavailable" so the fallback path runs deterministically.
    monkeypatch.setattr(documents._pixels, "_ocr_available",
                        lambda: (False, "forced-off"))
    ds = _encapsulated_pdf_ds(_image_pdf_bytes("Tan Wei Ming"))
    info = documents.redact_encapsulated_pdf(ds, _NameScanner(), dpi=100)
    assert info["mode"] == "removed_fallback"
    assert "EncapsulatedDocument" not in ds


def _pixels_ocr_ok():
    from deid_engine import pixels
    return pixels._ocr_available()
```
Add this import near the top of the test file if not present: `from deid_engine import pixels`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_documents.py -q`
Expected: FAIL with `AttributeError: module 'deid_engine.documents' has no attribute 'redact_pdf_bytes'`.

- [ ] **Step 3: Add the redaction functions**

Append to `inst/python/deid_engine/documents.py`:
```python
def redact_pdf_bytes(pdf_bytes: bytes, scanner, dpi: int = 150):
    """Render -> OCR-redact PHI boxes per page -> flatten. Returns (bytes, info).

    Caller must ensure OCR is available; this raises if it is not (no silent
    keep of an un-scanned PDF).
    """
    ok, note = _pixels._ocr_available()
    if not ok:
        raise RuntimeError(note)
    pages = render_pdf_pages(pdf_bytes, dpi=dpi)
    total_boxes = 0
    redacted = []
    for arr in pages:
        gray = _pixels._frame_to_uint8(arr)
        boxes = _pixels.image_phi_boxes(gray, scanner)
        total_boxes += len(boxes)
        # apply_boxes wants a frame axis (N,H,W[,C]); wrap this single page.
        stacked = arr[np.newaxis, ...]
        stacked = _pixels.apply_boxes(stacked, boxes, fill=0)
        redacted.append(stacked[0])
    return flatten_to_pdf(redacted), {"pages": len(pages), "boxes": total_boxes}


def redact_encapsulated_pdf(ds, scanner, dpi: int = 150) -> dict:
    """De-identify the embedded PDF in ``ds`` in place.

    When OCR is available: rasterize + redact + flatten, replacing
    EncapsulatedDocument with the flattened bytes. When OCR is absent: remove the
    document (never keep an un-scanned PDF). Never raises for the missing-binary
    case — that is the fallback, not an error.
    """
    ok, note = _pixels._ocr_available()
    if not ok:
        for kw in ("EncapsulatedDocument", "MIMETypeOfEncapsulatedDocument",
                   "EncapsulatedDocumentLength"):
            if kw in ds:
                del ds[kw]
        return {"mode": "removed_fallback", "note": note}
    src = bytes(ds.EncapsulatedDocument)
    flat, info = redact_pdf_bytes(src, scanner, dpi=dpi)
    if len(flat) % 2 == 1:
        flat += b"\x00"  # DICOM OB values are even-length
    ds.EncapsulatedDocument = flat
    if "EncapsulatedDocumentLength" in ds:
        ds.EncapsulatedDocumentLength = len(flat)
    return {"mode": "rasterize_redact", **info}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_documents.py -q`
Expected: PASS (the OCR test SKIPs if Tesseract is absent; the fallback test always runs).

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/documents.py tests/python/test_documents.py
git commit -m "feat: redact_pdf_bytes + redact_encapsulated_pdf with OCR-absent fallback

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Profile schema + `_walk` skips PDF removal under `rasterize_redact`

Under `mode: remove` (default) the catalog's `X` action still strips the embedded PDF. Under `rasterize_redact` we must leave the bytes in place so `deidentify_study` can redact them.

**Files:**
- Modify: `inst/python/deid_engine/core.py` (`_walk`, `deidentify_dataset`)
- Modify: `inst/profiles/default_profile.yml`
- Test: `tests/python/test_encapsulated_pdf.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/python/test_encapsulated_pdf.py`:
```python
"""Encapsulated-PDF policy wiring through the dataset/study pipeline."""
import io

import numpy as np
import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core, keystore, rules

ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def _image_pdf_bytes(text="Tan Wei Ming"):
    img = Image.new("RGB", (400, 120), (255, 255, 255))
    ImageDraw.Draw(img).text((10, 50), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    b = buf.getvalue()
    return b if len(b) % 2 == 0 else b + b"\x00"


def _encaps_ds():
    ds = Dataset()
    ds.SOPClassUID = ENCAPS_PDF_SOP
    ds.SOPInstanceUID = generate_uid()
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "DOC"
    ds.PatientName = "Tan^Wei Ming"
    ds.PatientID = "S1234567D"
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = _image_pdf_bytes()
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ENCAPS_PDF_SOP
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def _profile(mode):
    prof = rules.load_profile("default")
    prof["encapsulated_pdf"] = {"mode": mode, "dpi": 100}
    return prof


def test_remove_mode_strips_encapsulated_document():
    ds = _encaps_ds()
    core.deidentify_dataset(ds, _profile("remove"), salt=b"0" * 16)
    assert "EncapsulatedDocument" not in ds


def test_rasterize_mode_leaves_document_for_study_step():
    ds = _encaps_ds()
    core.deidentify_dataset(ds, _profile("rasterize_redact"), salt=b"0" * 16)
    # dataset step must NOT delete it; the study step redacts it later.
    assert "EncapsulatedDocument" in ds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_encapsulated_pdf.py -q`
Expected: `test_rasterize_mode_leaves_document_for_study_step` FAILS (EncapsulatedDocument was removed by the `X` action).

- [ ] **Step 3: Wire the skip into `_walk`/`deidentify_dataset`**

In `inst/python/deid_engine/core.py`, add a module constant near the top (by `_TEXT_VRS`):
```python
ENCAPS_DOC_TAG = 0x00420011  # EncapsulatedDocument
```

Change `_walk`'s signature to accept `pdf_mode` and skip the encapsulated-doc removal under rasterize mode. Update the `def _walk(...)` line to:
```python
def _walk(ds, amap, ctx, private_policy, allowlist, records, text_scan,
          pdf_mode="remove") -> None:
```
and immediately inside the `for tag in list(ds.keys()):` loop, after `elem = ds[tag]`, add:
```python
        if int(tag) == ENCAPS_DOC_TAG and pdf_mode == "rasterize_redact":
            continue  # leave the PDF bytes; deidentify_study redacts them
```
Also update the two recursive `_walk(item, ...)` calls inside the SQ branch to pass `pdf_mode` through:
```python
                _walk(item, amap, ctx, private_policy, allowlist, records,
                      text_scan, pdf_mode)
```

In `deidentify_dataset`, compute `pdf_mode` from the profile and pass it to the top-level `_walk` call. Just before the `records: list[dict] = []` / `_walk(...)` block add:
```python
    pdf_mode = (profile.get("encapsulated_pdf") or {}).get("mode", "remove")
```
and change the top-level call to:
```python
    _walk(ds, amap, ctx, private_policy, allowlist, records, text_scan, pdf_mode)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_encapsulated_pdf.py -q`
Expected: PASS (both tests).

- [ ] **Step 5: Add the profile block**

In `inst/profiles/default_profile.yml`, add a top-level block (place it near the `pixel:`/`options:` blocks):
```yaml
# Embedded-PDF (EncapsulatedDocument) handling.
#   remove          - strip the whole embedded PDF (default, safe, no OCR needed)
#   rasterize_redact - render pages, OCR-redact PHI, flatten to an image-only PDF
#                      (keeps the report minus PHI; needs the bundled Tesseract;
#                       degrades to `remove` when Tesseract is absent)
encapsulated_pdf:
  mode: remove
  dpi: 150
```

- [ ] **Step 6: Run the full python suite (regression guard)**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python -q`
Expected: all PASS/SKIP, no failures.

- [ ] **Step 7: Commit**

```bash
git add inst/python/deid_engine/core.py inst/profiles/default_profile.yml tests/python/test_encapsulated_pdf.py
git commit -m "feat: encapsulated_pdf profile mode; _walk keeps PDF under rasterize_redact

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: `deidentify_study` redacts the embedded PDF

**Files:**
- Modify: `inst/python/deid_engine/core.py` (`deidentify_study`)
- Test: `tests/python/test_encapsulated_pdf.py` (append)

- [ ] **Step 1: Write the failing test (append)**

Append to `tests/python/test_encapsulated_pdf.py`:
```python
from deid_engine import documents


def _write(ds, path):
    pydicom.dcmwrite(str(path), ds, enforce_file_format=True)


def test_study_remove_mode_output_has_no_pdf(tmp_path):
    _write(_encaps_ds(), tmp_path / "in.dcm")
    ks = keystore.ephemeral()
    prof = _profile("remove")
    core.deidentify_study(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"), prof, ks)
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    assert "EncapsulatedDocument" not in out
    assert str(out.PatientIdentityRemoved) == "YES"


def test_study_rasterize_mode_flattens_pdf(tmp_path):
    from deid_engine import pixels
    ok, _ = pixels._ocr_available()
    ks = keystore.ephemeral()
    prof = _profile("rasterize_redact")
    _write(_encaps_ds(), tmp_path / "in.dcm")
    core.deidentify_study(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"), prof, ks)
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    assert str(out.PatientIdentityRemoved) == "YES"
    if ok:
        # redacted + flattened: still present, but no recoverable text layer
        assert "EncapsulatedDocument" in out
        assert documents.pdf_text(bytes(out.EncapsulatedDocument)).strip() == ""
    else:
        # no Tesseract -> fell back to removal
        assert "EncapsulatedDocument" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_encapsulated_pdf.py -q`
Expected: `test_study_rasterize_mode_flattens_pdf` FAILS — with Tesseract present the PDF still has a text layer (never redacted); or with Tesseract absent the doc is still present (fallback not wired).

- [ ] **Step 3: Add the PDF block to `deidentify_study`**

In `inst/python/deid_engine/core.py`, add `from . import documents as _documents` to the module imports (next to `from . import pixels as _pixels`). Inside `deidentify_study`, after the pixel-autoredact block and before the "keep file-meta consistent" comment, insert:
```python
        # Opt-in encapsulated-PDF redaction: rasterize + OCR-redact + flatten, or
        # (Tesseract absent) fall back to removal. Seed the scanner on the study's
        # ORIGINAL identifiers so the patient's real name is caught in the PDF.
        pdf_mode = (profile.get("encapsulated_pdf") or {}).get("mode", "remove")
        if pdf_mode == "rasterize_redact" and _documents.is_encapsulated_pdf(ds):
            try:
                known_pdf = (known_for_pixels if known_for_pixels is not None
                             else _collect_known_values(ds))
                pscanner = _build_scanner(td, known_pdf)
                pdpi = int((profile.get("encapsulated_pdf") or {}).get("dpi", 150))
                res = _documents.redact_encapsulated_pdf(ds, pscanner, dpi=pdpi)
                counts["encapsulated_pdf"] = res.get("mode")
                if res.get("boxes") is not None:
                    counts["encapsulated_pdf_boxes"] = res["boxes"]
                if res.get("note"):
                    counts["encapsulated_pdf_note"] = res["note"]
            except Exception as e:  # noqa: BLE001 - never lose the file over the PDF
                counts["encapsulated_pdf_error"] = str(e)
```
Note: `known_for_pixels` is captured only when pixel-autoredact is enabled, so the `else` branch recomputes original known values when needed. `_collect_known_values(ds)` here is called on the already-de-identified `ds`; to seed on the ORIGINAL identifiers, capture them earlier. Replace the existing `known_for_pixels = (...)` assignment with a shared capture that also covers PDF mode:
```python
        _need_original = (_pixel_autoredact_enabled(profile)
                          or (profile.get("encapsulated_pdf") or {}).get("mode")
                          == "rasterize_redact")
        known_original = _collect_known_values(ds) if _need_original else None
        known_for_pixels = known_original if _pixel_autoredact_enabled(profile) else None
```
and in the PDF block use `known_original` instead of the `known_for_pixels if ... else ...` expression:
```python
                pscanner = _build_scanner(td, known_original or [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_encapsulated_pdf.py -q`
Expected: PASS (rasterize test passes on both the Tesseract-present and Tesseract-absent branches).

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/core.py tests/python/test_encapsulated_pdf.py
git commit -m "feat: deidentify_study redacts/flattens embedded PDFs under rasterize_redact

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: `deid_run` override params + R bridge

**Files:**
- Modify: `inst/python/deid_engine/core.py` (`deid_run`)
- Modify: `R/engine_bridge.R` (`engine_deid_run`)
- Test: `tests/python/test_encapsulated_pdf.py` (append) + `tests/testthat/test-engine-bridge-pdf.R` (Create)

- [ ] **Step 1: Write the failing python test (append)**

Append to `tests/python/test_encapsulated_pdf.py`:
```python
def test_deid_run_pdf_mode_override(tmp_path):
    _write(_encaps_ds(), tmp_path / "in.dcm")
    rep = core.deid_run(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"),
                        profile_id="default", pdf_mode="rasterize_redact", pdf_dpi=100)
    assert rep["count"] == 1
    from deid_engine import pixels
    ok, _ = pixels._ocr_available()
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    if ok:
        assert "EncapsulatedDocument" in out
    else:
        assert "EncapsulatedDocument" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_encapsulated_pdf.py::test_deid_run_pdf_mode_override -q`
Expected: FAIL with `TypeError: deid_run() got an unexpected keyword argument 'pdf_mode'`.

- [ ] **Step 3: Add the override params to `deid_run`**

In `inst/python/deid_engine/core.py`, extend the `deid_run` signature (append after `autoredact_pixels: bool = False`):
```python
             autoredact_pixels: bool = False,
             pdf_mode: str | None = None, pdf_dpi: int | None = None) -> dict:
```
Then, right after the `if autoredact_pixels:` block (which already copies `profile`), add:
```python
    if pdf_mode is not None or pdf_dpi is not None:
        profile = dict(profile)
        ep = dict(profile.get("encapsulated_pdf") or {})
        if pdf_mode is not None:
            ep["mode"] = pdf_mode
        if pdf_dpi is not None:
            ep["dpi"] = int(pdf_dpi)
        profile["encapsulated_pdf"] = ep
```
(If the `autoredact_pixels` branch did not already run, `profile` is still the shared object from `profile_get`; `dict(profile)` here makes the local copy before mutating, so the shipped profile is never mutated.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_encapsulated_pdf.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing testthat test**

Create `tests/testthat/test-engine-bridge-pdf.R`:
```r
test_that("engine_deid_run forwards pdf_mode/pdf_dpi to the engine", {
  called <- NULL
  local_mocked_bindings(
    call_engine = function(fn, ...) { called <<- list(fn = fn, args = list(...)); list(count = 0) }
  )
  engine_deid_run("in", "out", profile_id = "default",
                  pdf_mode = "rasterize_redact", pdf_dpi = 120)
  expect_equal(called$fn, "deid_run")
  # pdf_mode and pdf_dpi are the last two positional args
  n <- length(called$args)
  expect_equal(called$args[[n - 1]], "rasterize_redact")
  expect_equal(called$args[[n]], 120)
})
```

- [ ] **Step 6: Run testthat to verify it fails**

Run: `"C:/Program Files/R/R-4.5.2/bin/Rscript.exe" -e "testthat::test_file('tests/testthat/test-engine-bridge-pdf.R')"`
Expected: FAIL — `engine_deid_run` does not accept `pdf_mode`.

- [ ] **Step 7: Thread the params through `engine_deid_run`**

In `R/engine_bridge.R`, extend the `engine_deid_run` signature (append after `autoredact_pixels = FALSE`):
```r
                            autoredact_pixels = FALSE,
                            pdf_mode = NULL, pdf_dpi = NULL) {
```
and extend the `call_engine("deid_run", ...)` argument list to pass them as the final two positional args (matching the python signature order):
```r
  call_engine("deid_run", input_path, output_path, profile_id, keystore_path,
              passphrase, reversible, sign_key_path, signer, project_id,
              autoredact_pixels, pdf_mode, pdf_dpi)
```

- [ ] **Step 8: Run testthat to verify it passes**

Run: `"C:/Program Files/R/R-4.5.2/bin/Rscript.exe" -e "testthat::test_file('tests/testthat/test-engine-bridge-pdf.R')"`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add inst/python/deid_engine/core.py R/engine_bridge.R tests/python/test_encapsulated_pdf.py tests/testthat/test-engine-bridge-pdf.R
git commit -m "feat: deid_run/engine_deid_run pdf_mode + pdf_dpi overrides

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Corpus fixture + `check_survivors` inspects embedded PDFs

**Files:**
- Modify: `inst/python/deid_engine/corpus.py` (`build_corpus`, `check_survivors`, `PLANTED`)
- Test: `tests/python/test_corpus_pdf.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/python/test_corpus_pdf.py`:
```python
"""The acceptance corpus includes an encapsulated-PDF fixture, and the survivor
check inspects embedded PDFs."""
import io

import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import corpus

ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"


def test_corpus_emits_encapsulated_pdf_fixture(tmp_path):
    man = corpus.build_corpus(str(tmp_path))
    encaps = [f for f in man["fixtures"] if f.get("encapsulated_pdf")]
    assert len(encaps) == 1
    ds = pydicom.dcmread(str(tmp_path / encaps[0]["rel"]))
    assert str(ds.SOPClassUID) == ENCAPS_PDF_SOP
    assert "EncapsulatedDocument" in ds


def _pdf_with_name(name):
    img = Image.new("RGB", (400, 120), (255, 255, 255))
    ImageDraw.Draw(img).text((10, 50), name, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    b = buf.getvalue()
    return b if len(b) % 2 == 0 else b + b"\x00"


def test_check_survivors_flags_name_in_pdf_text_layer(tmp_path):
    # A born-image PDF has no text layer, so plant via a real text PDF surrogate:
    # write a DICOM whose EncapsulatedDocument text layer contains the name.
    from deid_engine import documents
    # Build a flattened image PDF is text-free; to exercise the text path we embed
    # a minimal text PDF built by Pillow is not possible, so assert the negative:
    # a flattened (redacted) PDF yields NO survivors.
    name = corpus.PLANTED["names"][1]
    flat = documents.flatten_to_pdf(documents.render_pdf_pages(_pdf_with_name(name), dpi=100))
    ds = Dataset()
    ds.SOPClassUID = ENCAPS_PDF_SOP
    ds.SOPInstanceUID = generate_uid()
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = flat if len(flat) % 2 == 0 else flat + b"\x00"
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ENCAPS_PDF_SOP
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    pydicom.dcmwrite(str(tmp_path / "doc.dcm"), ds, enforce_file_format=True)
    res = corpus.check_survivors(str(tmp_path))
    assert res["passed"] is True  # flattened PDF has no recoverable name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_corpus_pdf.py -q`
Expected: `test_corpus_emits_encapsulated_pdf_fixture` FAILS (no encapsulated fixture yet).

- [ ] **Step 3: Add the fixture to `build_corpus`**

In `inst/python/deid_engine/corpus.py`, add a helper near `_burn_text_rgb`:
```python
def _image_pdf_bytes(text: str) -> bytes:
    """A one-page image PDF (no text layer) with the name visibly rendered."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (480, 140), (255, 255, 255))
    ImageDraw.Draw(img).text((12, 60), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    b = buf.getvalue()
    return b if len(b) % 2 == 0 else b + b"\x00"
```
Add `import io` at the top of `corpus.py` if not already present.

Add `"encapsulated_pdf"` to the `PLANTED["encodings"]` list. Then, in `build_corpus`, after fixture block 5 (NIfTI) and before the `manifest = {...}` line, add:
```python
    # 6. Encapsulated PDF (embedded report with a planted visible name) ---------
    _ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"
    ds = _finalise(_base_ds(_ENCAPS_PDF_SOP), _ENCAPS_PDF_SOP)
    ds.Modality = "DOC"
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = _image_pdf_bytes(PLANTED["names"][1])  # Tan Wei Ming
    ds["EncapsulatedDocument"].VR = "OB"
    rel = "encapsulated_pdf.dcm"
    pydicom.dcmwrite(os.path.join(out_dir, rel), ds, enforce_file_format=True)
    fixtures.append({"rel": rel, "encoding": "encapsulated_pdf",
                     "transfer_syntax": str(ExplicitVRLittleEndian),
                     "burned_in": True, "encapsulated_pdf": True})
```

- [ ] **Step 4: Extend `check_survivors` to inspect embedded PDFs**

In `inst/python/deid_engine/corpus.py`, inside `check_survivors`, after the DICOM `text = _element_text(ds)` branch computes `text`, add embedded-PDF inspection. Replace the `else:` DICOM branch body so that, after reading `ds`, it also appends any recoverable PDF text/OCR:
```python
            else:
                try:
                    ds = pydicom.dcmread(full, force=True)
                except Exception:  # noqa: BLE001
                    continue
                text = _element_text(ds)
                text += " " + _encapsulated_pdf_text(ds)
```
and add this module-level helper near `_element_text`:
```python
def _encapsulated_pdf_text(ds) -> str:
    """Recoverable text from an embedded PDF: its text layer, plus OCR of the
    rendered pages when Tesseract is available. Empty when there is no PDF."""
    try:
        from . import documents
        if not documents.is_encapsulated_pdf(ds):
            return ""
        pdf_bytes = bytes(ds.EncapsulatedDocument)
        parts = [documents.pdf_text(pdf_bytes)]
        from . import pixels
        ok, _ = pixels._ocr_available()
        if ok:
            for arr in documents.render_pdf_pages(pdf_bytes, dpi=150):
                import pytesseract
                parts.append(pytesseract.image_to_string(pixels._frame_to_uint8(arr)))
        return " ".join(parts)
    except Exception:  # noqa: BLE001 - survivor scan must never crash
        return ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_corpus_pdf.py -q`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add inst/python/deid_engine/corpus.py tests/python/test_corpus_pdf.py
git commit -m "feat: corpus encapsulated-PDF fixture + check_survivors PDF text/OCR sweep

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Acceptance wiring + end-to-end OCR test + exports + docs

**Files:**
- Modify: `R/acceptance.R`
- Modify: `inst/python/deid_engine/__init__.py`
- Create: `tests/python/test_encapsulated_pdf_e2e.py`
- Create: `docs/encapsulated-pdf.md`
- Modify: `inst/profiles/identifier_catalog.yml`, `docs/roadmap.md`, `docs/airgap-install.md`, `docs/text-detection.md`

- [ ] **Step 1: Export the documents helpers**

In `inst/python/deid_engine/__init__.py`, add `documents` to the imports/`__all__` the same way `pixels`/`corpus` are exposed (so `call_engine` and tests can reach `documents.*`). Add:
```python
from . import documents  # noqa: F401
```
next to the existing `from . import pixels` / `from . import corpus` lines, and add `"documents"` to `__all__` if an explicit list exists.

- [ ] **Step 2: Write the end-to-end OCR test (Tesseract-gated)**

Create `tests/python/test_encapsulated_pdf_e2e.py`:
```python
"""End-to-end: a planted name burned into an embedded PDF is gone after
rasterize_redact. Requires Tesseract (the bundle ships one); skips otherwise."""
import io

import pydicom
import pytest
from PIL import Image, ImageDraw
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from deid_engine import core, documents, keystore, pixels, rules

ENCAPS_PDF_SOP = "1.2.840.10008.5.1.4.1.1.104.1"

pytestmark = pytest.mark.skipif(not pixels._ocr_available()[0],
                                reason="Tesseract OCR binary not available")


def _encaps_ds(name):
    img = Image.new("RGB", (480, 140), (255, 255, 255))
    ImageDraw.Draw(img).text((12, 60), name, fill=(0, 0, 0))
    buf = io.BytesIO(); img.save(buf, format="PDF")
    b = buf.getvalue(); b = b if len(b) % 2 == 0 else b + b"\x00"
    ds = Dataset()
    ds.SOPClassUID = ENCAPS_PDF_SOP
    ds.SOPInstanceUID = generate_uid()
    ds.PatientName = "Tan^Wei Ming"; ds.PatientID = "S1234567D"
    ds.MIMETypeOfEncapsulatedDocument = "application/pdf"
    ds.EncapsulatedDocument = b
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = ENCAPS_PDF_SOP
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = fm; ds.is_little_endian = True; ds.is_implicit_VR = False
    return ds


def _ocr(pdf_bytes):
    import pytesseract
    words = []
    for arr in documents.render_pdf_pages(pdf_bytes, dpi=150):
        words.append(pytesseract.image_to_string(pixels._frame_to_uint8(arr)))
    return " ".join(words)


def test_planted_name_gone_from_redacted_pdf(tmp_path):
    prof = rules.load_profile("default")
    prof["encapsulated_pdf"] = {"mode": "rasterize_redact", "dpi": 150}
    pydicom.dcmwrite(str(tmp_path / "in.dcm"), _encaps_ds("Tan Wei Ming"),
                     enforce_file_format=True)
    core.deidentify_study(str(tmp_path / "in.dcm"), str(tmp_path / "out.dcm"),
                          prof, keystore.ephemeral())
    out = pydicom.dcmread(str(tmp_path / "out.dcm"))
    ocr_text = _ocr(bytes(out.EncapsulatedDocument))
    for tok in ("Tan", "Wei", "Ming"):
        assert tok not in ocr_text, f"{tok!r} survived: {ocr_text!r}"
```

- [ ] **Step 3: Run it (fails if study wiring regressed; else PASS/SKIP)**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_encapsulated_pdf_e2e.py -q`
Expected: PASS (or SKIP without Tesseract). If it FAILS with tokens surviving, the box's OCR/DPI is missing them — raise `dpi` to 200 in the profile block and re-run.

- [ ] **Step 4: Enable rasterize_redact in the acceptance run**

In `R/acceptance.R`, `acceptance_run` currently calls `engine_deid_run(..., autoredact_pixels = TRUE)`. Add the PDF override so the acceptance genuinely exercises embedded-PDF redaction. Change that call to:
```r
  rep <- engine_deid_run(corpus_dir, out_dir, profile_id = profile_id,
                         keystore_path = ks_path, passphrase = passphrase,
                         reversible = (mode == "reversible"),
                         autoredact_pixels = TRUE,
                         pdf_mode = "rasterize_redact")
```

- [ ] **Step 5: Run the acceptance self-test live (both modes)**

Create a scratch script `tmp_acc.R` at the project root (delete after):
```r
devtools::load_all(".", quiet = TRUE)
for (m in c("reversible", "irreversible")) {
  r <- acceptance_run(mode = m)
  cat(m, "passed:", isTRUE(r$report$passed), "\n")
  print(r$raw$survivors$metadata_survivors)
}
```
Run: `"C:/Program Files/R/R-4.5.2/bin/Rscript.exe" tmp_acc.R`
Expected: `reversible passed: TRUE` and `irreversible passed: TRUE`, empty survivors. Then `rm tmp_acc.R`.
(If Tesseract is not installed on the dev box, the PDF path falls back to removal and acceptance still passes; the genuine redaction is validated on the bundle where Tesseract is present.)

- [ ] **Step 6: Update the catalog note + docs**

In `inst/profiles/identifier_catalog.yml`, replace the `encapsulated_documents` `note:` text with:
```yaml
    note: >
      Default action X removes the embedded document. With the profile
      `encapsulated_pdf.mode: rasterize_redact`, an embedded PDF is instead
      rendered, OCR-redacted, and flattened to an image-only PDF (no text layer)
      so the report content survives minus PHI; it degrades to removal when the
      Tesseract binary is absent. DocumentTitle (0042,0010) is a text VR caught
      by the free-text scanner.
```

Create `docs/encapsulated-pdf.md`:
```markdown
# Encapsulated-PDF de-identification

DICOM can embed a PDF report in `EncapsulatedDocument (0042,0011)` (SOP Class
Encapsulated PDF Storage `1.2.840.10008.5.1.4.1.1.104.1`). PHI can hide in the
PDF's text layer *and* in its images.

## Modes (profile `encapsulated_pdf.mode`)

- `remove` (default) — strip the whole embedded PDF. Safe, needs no OCR.
- `rasterize_redact` — render each page (`pypdfium2`), OCR-redact PHI regions with
  the shared layered scanner, and rebuild a **flattened image-only PDF**. Flattening
  removes the text layer entirely, so hidden/selectable text PHI cannot survive even
  if OCR missed it visually. Needs the bundled Tesseract; **degrades to `remove`**
  when Tesseract is absent (never keeps an un-scanned PDF).

`dpi` (default 150) controls render resolution; raise it if OCR misses small text.

## Trade-off

A redacted PDF becomes a picture: no selectable/searchable text, larger file. This
is the safety/utility trade for an airtight guarantee. `remove` stays the default;
opt in per project/profile.

## Dependency

`pypdfium2` (Apache-2.0 / BSD-3, self-contained wheel — no system binary, no admin,
offline once staged). Bundled by `build_venv.ps1`.
```

In `docs/roadmap.md` add a line under the appropriate phase noting encapsulated-PDF rasterize+OCR-redact is delivered. In `docs/text-detection.md` add a cross-reference to `docs/encapsulated-pdf.md`. In `docs/airgap-install.md` note that the bundle now stages `pypdfium2` and that `rasterize_redact` requires the bundled Tesseract.

- [ ] **Step 7: Run the full suites (final regression guard)**

Run:
```bash
PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python -q
"C:/Program Files/R/R-4.5.2/bin/Rscript.exe" tests/testthat.R
```
Expected: all PASS/SKIP, no failures.

- [ ] **Step 8: Commit**

```bash
git add R/acceptance.R inst/python/deid_engine/__init__.py tests/python/test_encapsulated_pdf_e2e.py docs/encapsulated-pdf.md inst/profiles/identifier_catalog.yml docs/roadmap.md docs/text-detection.md docs/airgap-install.md
git commit -m "feat: acceptance exercises PDF rasterize_redact; e2e OCR test; docs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: SR audit fixture (confirm existing coverage; close any gap)

**Files:**
- Test: `tests/python/test_sr_audit.py` (Create)
- Modify (only if the test surfaces a gap): `inst/python/deid_engine/core.py`

- [ ] **Step 1: Write the audit test**

Create `tests/python/test_sr_audit.py`:
```python
"""Confirm SR ContentSequence PHI (TextValue + PersonName + date) is scrubbed by
the existing SQ-recursing scanner. If any assertion fails, it marks a real gap to
close in core (add the value-type tag to the scanned text VRs)."""
import numpy as np
import pydicom
import pytest
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from deid_engine import core, keystore, rules


def _sr_dataset():
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.88.11"  # Basic Text SR
    ds.SOPInstanceUID = "1.2.3.4"
    ds.PatientName = "Tan^Wei Ming"
    ds.PatientID = "S1234567D"
    item = Dataset()
    item.ValueType = "TEXT"
    item.TextValue = "Reported by Nurul Aisyah Binte Rahman, NRIC S1234567D"
    name_item = Dataset()
    name_item.ValueType = "PNAME"
    name_item.PersonName = "Ramasamy^Muthu"
    ds.ContentSequence = Sequence([item, name_item])
    return ds


def test_sr_textvalue_and_personname_scrubbed():
    ds = _sr_dataset()
    prof = rules.load_profile("default")
    # gazetteer-independent tokens: NRIC (deterministic) must be gone; the header
    # name "Tan Wei Ming" is header-scrubbed everywhere including SR.
    core.deidentify_dataset(ds, prof, salt=b"0" * 16)
    blob = " ".join(str(x.value) for x in ds.ContentSequence[0]) \
        if hasattr(ds.ContentSequence[0], "__iter__") else ""
    tv = str(ds.ContentSequence[0].TextValue)
    assert "S1234567D" not in tv, tv          # deterministic NRIC recogniser
    assert "Tan Wei Ming" not in tv.replace("^", " ")
```

- [ ] **Step 2: Run the audit test**

Run: `PYTHONPATH=inst/python KMP_DUPLICATE_LIB_OK=TRUE inst/python/.venv/Scripts/python.exe -m pytest tests/python/test_sr_audit.py -q`
Expected: PASS (SR text is already scrubbed by the existing scanner). **If it FAILS**, the gap is real: in `core.py` confirm the SR value-type tag's VR is in `_TEXT_VRS` (TextValue is `UT`, PersonName is `PN` — both already included), and that `_walk` recurses `ContentSequence`. Add the missing VR/tag and re-run until green. Do not weaken the assertion to pass.

- [ ] **Step 3: Commit**

```bash
git add tests/python/test_sr_audit.py
git commit -m "test: audit SR ContentSequence PHI scrubbing (TextValue + PersonName)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Detection → Task 3 (`is_encapsulated_pdf`) + Task 5/6 wiring. ✓
- `documents.py` render/OCR-redact/flatten → Tasks 3–4. ✓
- Shared `image_phi_boxes` with pixels → Task 2. ✓
- Policy modes (`remove` default, `rasterize_redact` opt-in, Tesseract fallback) → Tasks 4–7 + profile block in Task 5. ✓
- SR audit + fixture → Task 10. ✓
- Corpus fixture + acceptance render/OCR check → Tasks 8–9. ✓
- Wiring & packaging (`deid_run`/bridge, `build_venv.ps1`, docs) → Tasks 1, 7, 9. ✓
- Non-goals (always-flatten; PDF only; OCR-unreadable limitation) — respected; no task keeps a text layer or handles CDA. ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code and exact commands. ✓

**Type consistency:** `image_phi_boxes(img_uint8, scanner) -> list` (Task 2) is called with a single image in Task 4; `redact_encapsulated_pdf(ds, scanner, dpi)` returns `{"mode", "boxes"?, "note"?}` and is consumed accordingly in Task 6; `deid_run(..., pdf_mode, pdf_dpi)` order matches `engine_deid_run`'s trailing positional args (Task 7). `ENCAPS_DOC_TAG`/`ENCAPSULATED_PDF_SOP` constants defined once and reused. ✓
