# Optional ML Defacing + NIfTI Burned-in OCR — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two items deferred from Phase 3 / 3.5 — optional ML defacing of head-inclusive MRI volumes, and burned-in-text OCR on NIfTI arrays — as opt-in, gracefully-degrading extensions to the existing pixel layer.

**Architecture:** Feature B (NIfTI OCR) reuses the existing DICOM OCR helpers (`pixels.py`) over a NIfTI volume's slices and wires into `_deidentify_nifti`. Feature A (defacing) is a new self-contained `deface.py`: a cheap geometry FOV gate + an intensity modality gate + a bundled CPU-PyTorch face-mask model (loaded lazily, degrades when absent exactly like OCR/NER), applied to NIfTI MR volumes and DICOM **multiframe** MR objects. Both thread through the one high-level entry `deid_run` (Python) / `engine_deid_run` (R).

**Tech Stack:** Python (numpy, nibabel, pydicom, torch — all already in the relocatable venv), R/Shiny bridge via reticulate, pytest + testthat.

**Scope refinements from the spec (documented, deliberate):**
- DICOM defacing targets **multiframe** MR objects (a whole 3D volume in one file). Single-instance-per-slice DICOM series (a 3D face spanning many files) is **out of scope** — noted in `docs/defacing.md` as future. NIfTI (always one 3D file) is the primary surface.
- NIfTI OCR scans slices along the volume's **shortest axis** (the through-plane/stack axis), giving the most image-like in-plane slices. All-three-plane scanning stays future scope.
- No new pip dependency: mask dilation uses a tiny pure-numpy helper, not scipy.

**Feature order:** Feature B first (fully verifiable today, no model dependency), then Feature A, then shared corpus/acceptance/UI/bundle/docs.

---

## File Structure

**Create:**
- `inst/python/deid_engine/deface.py` — defacing module (FOV gate, modality gate, model loader, `deface_array` orchestrator). One responsibility: decide-and-erase faces on a volume array.
- `tests/python/test_deface.py` — unit tests for `deface.py` (stub model; no real weights needed).
- `tests/python/test_nifti_ocr.py` — unit tests for NIfTI burned-in OCR.
- `docs/defacing.md` — feature doc.

**Modify:**
- `inst/python/deid_engine/pixels.py` — add `volume_ocr_boxes()` + `redact_volume_boxes()`.
- `inst/python/deid_engine/core.py` — thread OCR + defacing through `_deidentify_nifti` and `deidentify_study`; add `_deface_enabled`; add `deface` param to `deid_run`.
- `inst/profiles/default_profile.yml` — add `deface:` section.
- `R/engine_bridge.R` — add `deface` param to `engine_deid_run` + `call_engine` list.
- `R/mod_interactive.R` — add the "Deface" checkbox and thread it into the run call.
- `R/acceptance.R` — add defacing + NIfTI-OCR acceptance checks; run the suite with `deface = TRUE`.
- `inst/python/deid_engine/corpus.py` — add a head-inclusive MR NIfTI fixture and a burned-in-text NIfTI fixture; extend the manifest.
- `tests/python/test_corpus.py` — assert the new fixtures exist.
- `tools/build_portable_bundle.ps1` — copy `inst/models/deface/` into the bundle.
- `docs/pixel-redaction.md`, `docs/roadmap.md` — mark done + document.

---

# FEATURE B — NIfTI Burned-in-text OCR

### Task B1: `volume_ocr_boxes` + `redact_volume_boxes` in pixels.py

**Files:**
- Modify: `inst/python/deid_engine/pixels.py`
- Test: `tests/python/test_nifti_ocr.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/python/test_nifti_ocr.py
import numpy as np
from deid_engine import pixels


class _StubScanner:
    """Flags any word containing a digit run of >=6 (stands in for the real
    SG-aware scanner) so the test needs no Tesseract or model."""
    def scan(self, text):
        import re
        return bool(re.search(r"\d{6,}", str(text)))


def test_redact_volume_boxes_zeros_the_box_on_the_right_slice():
    vol = np.ones((3, 8, 8), dtype=np.int16) * 100  # (D,H,W), D is shortest axis
    boxes = [{"x": 2, "y": 3, "w": 3, "h": 2, "slice": 1, "axis": 0}]
    out = pixels.redact_volume_boxes(vol, boxes, fill=0)
    assert out[1, 3:5, 2:5].sum() == 0          # box zeroed on slice 1
    assert out[0].sum() == 100 * 64             # other slices untouched
    assert out[2].sum() == 100 * 64
    assert vol[1].sum() == 100 * 64             # input not mutated


def test_volume_ocr_boxes_iterates_shortest_axis_and_tags_slice():
    # A (2, 6, 6) volume: shortest axis is 0 -> two 6x6 planes scanned.
    vol = np.zeros((2, 6, 6), dtype=np.uint8)
    calls = {"planes": 0}

    class _Scan(_StubScanner):
        pass

    # Monkeypatch image_phi_boxes to avoid needing Tesseract; assert per-plane call.
    orig = pixels.image_phi_boxes
    def fake(img, scanner):
        calls["planes"] += 1
        return [{"x": 0, "y": 0, "w": 1, "h": 1, "text": "S1234567", "source": "ocr"}]
    pixels.image_phi_boxes = fake
    try:
        boxes = pixels.volume_ocr_boxes(vol, _Scan())
    finally:
        pixels.image_phi_boxes = orig
    assert calls["planes"] == 2
    assert all(b["axis"] == 0 for b in boxes)
    assert sorted(b["slice"] for b in boxes) == [0, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_nifti_ocr.py -v`
Expected: FAIL — `AttributeError: module 'deid_engine.pixels' has no attribute 'redact_volume_boxes'`

- [ ] **Step 3: Write the implementation**

Add to `inst/python/deid_engine/pixels.py` (after `ocr_phi_boxes`):

```python
def volume_ocr_boxes(volume, scanner, axis=None) -> list:
    """OCR each in-plane slice of a 3-D NIfTI-style volume; return PHI boxes.

    Slices are taken along ``axis`` (default: the shortest axis, i.e. the
    through-plane/stack direction, which yields the most image-like planes).
    Each box is ``{x, y, w, h, text, source, slice, axis}``. Assumes the caller
    already confirmed OCR is available (``_ocr_available``). Non-3-D input -> []."""
    v = np.asarray(volume)
    if v.ndim != 3:
        return []
    ax = int(np.argmin(v.shape)) if axis is None else int(axis)
    boxes = []
    for i in range(v.shape[ax]):
        img = _frame_to_uint8(np.take(v, i, axis=ax))
        for b in image_phi_boxes(img, scanner):
            b = dict(b)
            b["slice"] = i
            b["axis"] = ax
            boxes.append(b)
    return boxes


def redact_volume_boxes(volume, boxes, fill=0):
    """Zero each box on its slice within a copy of ``volume``; return the copy.

    Boxes are ``{x, y, w, h, slice, axis}`` (as from ``volume_ocr_boxes``);
    coordinates are clamped to the in-plane bounds."""
    v = np.asarray(volume).copy()
    for b in boxes:
        ax = int(b.get("axis", 0))
        i = int(b["slice"])
        if not (0 <= i < v.shape[ax]):
            continue
        plane = np.take(v, i, axis=ax)
        h, w = plane.shape[0], plane.shape[1]
        x0 = max(0, int(b["x"])); y0 = max(0, int(b["y"]))
        x1 = min(w, x0 + int(b["w"])); y1 = min(h, y0 + int(b["h"]))
        if x1 <= x0 or y1 <= y0:
            continue
        plane = plane.copy()
        plane[y0:y1, x0:x1, ...] = fill
        idx = [slice(None)] * v.ndim
        idx[ax] = i
        v[tuple(idx)] = plane
    return v
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_nifti_ocr.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/pixels.py tests/python/test_nifti_ocr.py
git commit -m "feat(pixels): OCR + redact boxes over a 3-D NIfTI volume"
```

---

### Task B2: Wire NIfTI OCR into `_deidentify_nifti`

**Files:**
- Modify: `inst/python/deid_engine/core.py` (`_deidentify_nifti` at ~320-365; call site at ~625-627)
- Test: `tests/python/test_nifti_ocr.py`

`_deidentify_nifti` currently takes `(full, out)`. Extend it to accept the scanner + an auto-redact flag, iterate the volume's slices, and (auto-redact runs) zero PHI boxes. Interactive (no auto-redact) records the proposed boxes without modifying pixels. Reuses the existing `_pixel_autoredact_enabled(profile)` gate so behaviour matches DICOM.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/python/test_nifti_ocr.py
import nibabel as nib
from deid_engine import core, keystore, pixels as _pixels


def test_nifti_burned_in_text_is_redacted_when_autoredact(tmp_path, monkeypatch):
    # A volume whose one slice "contains" burned-in PHI. We stub OCR so the test
    # needs no Tesseract: pretend a fixed box on slice 0 reads an NRIC.
    vol = np.ones((2, 10, 10), dtype=np.int16) * 50
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "v.nii.gz"))
    out = tmp_path / "out"

    monkeypatch.setattr(_pixels, "_ocr_available", lambda: (True, ""))
    monkeypatch.setattr(_pixels, "image_phi_boxes",
                        lambda img, scanner: [{"x": 1, "y": 1, "w": 4, "h": 3,
                                               "text": "S1234567D", "source": "ocr"}]
                        if img.shape == (10, 10) else [])

    prof = dict(core.profile_get("default"))
    # force auto-redact (as bulk/acceptance does)
    prof["pixel"] = {**(prof.get("pixel") or {}), "auto_detect": True,
                     "require_human_confirm": False}
    prof["options"] = {**(prof.get("options") or {}), "clean_pixel_data": True}

    ks = keystore.ephemeral()
    report = core.deidentify_study(str(tmp_path / "v.nii.gz"), str(out), prof, ks)
    rec = report["files"][0]
    assert rec["counts"].get("nifti_ocr_boxes", 0) >= 1
    assert rec["counts"].get("nifti_pixels_redacted", 0) >= 1
    got = nib.load(rec["output"]).get_fdata()
    # the box on both scanned planes (shortest axis is 0, size 2) is zeroed
    assert got[0, 1:4, 1:5].sum() == 0


def test_nifti_ocr_degrades_without_tesseract(tmp_path, monkeypatch):
    vol = np.ones((2, 6, 6), dtype=np.int16)
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "v.nii.gz"))
    out = tmp_path / "out"
    monkeypatch.setattr(_pixels, "_ocr_available", lambda: (False, "no tesseract"))
    prof = core.profile_get("default")
    ks = keystore.ephemeral()
    report = core.deidentify_study(str(tmp_path / "v.nii.gz"), str(out), prof, ks)
    rec = report["files"][0]
    assert rec["counts"].get("nifti_ocr_note")           # noted, not fatal
    assert nib.load(rec["output"]) is not None            # file still written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_nifti_ocr.py -k "burned_in or degrades" -v`
Expected: FAIL — `_deidentify_nifti` ignores pixels; `counts` has no `nifti_ocr_boxes`.

- [ ] **Step 3: Write the implementation**

In `inst/python/deid_engine/core.py`, change the `_deidentify_nifti` signature and add the OCR block just before it builds `out_img`. Replace the function's tail (from `# type(img) keeps ...` onward) and its `def` line:

```python
def _deidentify_nifti(full: str, out: str, profile: dict | None = None,
                      scanner=None, ocr_autoredact: bool = False) -> dict:
    """Copy a NIfTI volume through, blanking header PHI surfaces, and (when the
    profile turns pixel cleaning on) OCR-scanning its slices for burned-in text.

    Header text fields + all extensions are removed; the NIfTI-1/2 class is kept.
    Burned-in-text OCR reuses the DICOM OCR layer: with ``ocr_autoredact`` the
    detected PHI boxes are zeroed; otherwise they are recorded for reviewer
    confirmation. The image data is otherwise preserved exactly.
    """
    import nibabel as nib
    import numpy as np

    img = nib.load(full)
    hdr = img.header.copy()
    records = []
    scrubbed = 0
    for field in _NIFTI_TEXT_FIELDS:
        try:
            cur = bytes(hdr[field]).split(b"\x00", 1)[0]
        except (KeyError, ValueError):
            continue
        if cur:
            scrubbed += 1
            records.append({"tag": 0, "keyword": f"nifti:{field}", "action": "Z",
                            "original": cur.decode("latin-1", "replace"),
                            "result": ""})
        hdr[field] = b""

    ext_removed = 0
    try:
        exts = hdr.extensions
        ext_removed = len(exts)
        if ext_removed:
            del exts[:]
    except (AttributeError, TypeError):
        pass

    data = np.asanyarray(img.dataobj)
    counts = {"nifti_header_fields_scrubbed": scrubbed,
              "nifti_extensions_removed": ext_removed}

    # Burned-in-text OCR (optional; degrades like the DICOM pixel layer).
    clean_pixels = bool((profile or {}).get("options", {}).get("clean_pixel_data", True))
    if clean_pixels and scanner is not None and data.ndim == 3:
        ok, note = _pixels._ocr_available()
        if not ok:
            counts["nifti_ocr_note"] = note
        else:
            try:
                boxes = _pixels.volume_ocr_boxes(data, scanner)
                counts["nifti_ocr_boxes"] = len(boxes)
                if boxes and ocr_autoredact:
                    data = _pixels.redact_volume_boxes(data, boxes, fill=0)
                    counts["nifti_pixels_redacted"] = len(boxes)
            except Exception as e:  # noqa: BLE001 - pixel step must not lose the file
                counts["nifti_ocr_error"] = str(e)

    out_img = type(img)(np.ascontiguousarray(data), img.affine, header=hdr)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    nib.save(out_img, out)
    return {"input": full, "output": out, "counts": counts, "records": records}
```

Then update the call site in `deidentify_study` (currently `files_report.append(_deidentify_nifti(full, out))` at ~627):

```python
        if _is_nifti(full):
            out = _sanitised_out(out, rel, sidecar_known.get(_basename_stem(rel)))
            files_report.append(_deidentify_nifti(
                full, out, profile=profile, scanner=scanner,
                ocr_autoredact=_pixel_autoredact_enabled(profile)))
            continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_nifti_ocr.py -v`
Expected: PASS (all). Then regression: `python -m pytest tests/python/test_nifti_deid.py -v` — Expected: PASS (existing NIfTI header/sidecar/filename behaviour unchanged; note the report record now carries `counts` instead of the old `counts` key nested under `counts` — the existing tests read the header/output, not `counts`, so they stay green).

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/core.py tests/python/test_nifti_ocr.py
git commit -m "feat(nifti): burned-in-text OCR in NIfTI de-id (matches DICOM UX)"
```

---

# FEATURE A — ML Defacing (MRI · opt-in · FOV-gated)

### Task A1: `deface.py` — availability probe + gates

**Files:**
- Create: `inst/python/deid_engine/deface.py`
- Test: `tests/python/test_deface.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/python/test_deface.py
import numpy as np
from deid_engine import deface


def _head_volume():
    """A crude 'head': a filled ellipsoid centred in an air-bordered 24x40x40
    volume (depth 24 >= min_axial; strong air border; compact central blob)."""
    d, h, w = 24, 40, 40
    zz, yy, xx = np.ogrid[:d, :h, :w]
    cz, cy, cx = d / 2, h / 2, w / 2
    ell = ((zz - cz) / (d * 0.35))**2 + ((yy - cy) / (h * 0.3))**2 + \
          ((xx - cx) / (w * 0.3))**2 <= 1.0
    vol = np.zeros((d, h, w), dtype=np.float32)
    vol[ell] = 500.0
    return vol


def test_is_head_inclusive_true_for_air_bordered_deep_blob():
    assert deface.is_head_inclusive(_head_volume()) is True


def test_is_head_inclusive_false_for_shallow_chest_slab():
    # Too few slices through-plane (a short cardiac cine stack).
    assert deface.is_head_inclusive(np.ones((4, 40, 40), dtype=np.float32)) is False


def test_is_head_inclusive_false_when_no_air_border():
    # Foreground fills the frame edges (no surrounding air) -> not a head FOV.
    assert deface.is_head_inclusive(np.ones((24, 40, 40), dtype=np.float32)) is False


def test_looks_like_ct_true_for_hounsfield_floor():
    ct = _head_volume() - 1000.0   # air ~ -1000 HU
    assert deface.looks_like_ct(ct) is True


def test_looks_like_ct_false_for_nonnegative_mr():
    assert deface.looks_like_ct(_head_volume()) is False


def test_deface_available_false_without_weights(monkeypatch, tmp_path):
    monkeypatch.setattr(deface, "_model_path", lambda: tmp_path / "model.pt")
    ok, note = deface.deface_available()
    assert ok is False and "unavailable" in note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_deface.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deid_engine.deface'`

- [ ] **Step 3: Write the implementation**

```python
# inst/python/deid_engine/deface.py
"""Phase 3 (deferred) - optional ML defacing of head-inclusive MRI volumes.

Removes the facial biometric surface (eyes/nose/mouth) from a head-inclusive MR
volume, keeping the brain intact. Off by default; enabled per run. Two cheap
gates decide *whether* to run before the model is even loaded:

  * ``is_head_inclusive`` - geometry: enough through-plane depth AND an air
    border AND a compact central blob. A chest-only cardiac stack fails this and
    passes through untouched.
  * ``looks_like_ct`` - intensity: CT stores Hounsfield units (air ~ -1000), so a
    large negative floor marks CT-like data. Used only for format-less NIfTI;
    DICOM passes its ``Modality`` tag directly. Head-inclusive CT is flagged for
    manual handling, never defaced by an MR-trained model.

The face mask itself comes from a bundled CPU TorchScript model under
``inst/models/deface/model.pt`` (shipped like the NER model). When the weights
are absent the whole step degrades to a noted skip - never fatal - exactly like
the OCR/NER layers. Efficacy on real MR is validated separately once weights are
pinned; the logic here is exercised with a stub model in tests.
"""
from __future__ import annotations

import numpy as np

_MODEL_SUBDIR = "deface"


def _model_dir():
    from . import rules as _rules
    return _rules.PROFILE_DIR.parent / "models" / _MODEL_SUBDIR  # inst/models/deface


def _model_path():
    return _model_dir() / "model.pt"


def deface_available():
    """(bool, note). True when the bundled TorchScript defacing model is present
    and torch is importable. Degrades like ``pixels._ocr_available``."""
    p = _model_path()
    try:
        present = p.is_file()
    except Exception:  # noqa: BLE001
        present = False
    if not present:
        return False, f"deface model unavailable: no weights at {p}"
    try:
        import torch  # noqa: F401
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"deface model unavailable: {e}"


def looks_like_ct(array) -> bool:
    """CT-like intensity gate: a large negative floor (Hounsfield air) marks CT."""
    a = np.asarray(array)
    if a.size == 0:
        return False
    return float(np.percentile(a.astype(np.float32), 1)) < -300.0


def is_head_inclusive(array, min_axial: int = 16) -> bool:
    """Cheap geometry gate for a head-inclusive FOV.

    Requires: >=3-D with >= ``min_axial`` slices on the shortest axis; a
    predominantly-background (air) volume border; and a compact central
    foreground blob (not an edge-to-edge slab). Conservative - a false negative
    (skipping a real head) is safer than mangling a chest scan."""
    a = np.asarray(array).astype(np.float32)
    if a.ndim < 3 or min(a.shape) < min_axial:
        return False
    thr = float(a.mean())
    fg = a > thr
    if not fg.any():
        return False
    ax = int(np.argmin(a.shape))
    lo = np.take(fg, 0, axis=ax)
    hi = np.take(fg, a.shape[ax] - 1, axis=ax)
    border_bg_frac = 1.0 - float((lo.mean() + hi.mean()) / 2.0)
    frac = float(fg.mean())
    return border_bg_frac > 0.7 and 0.02 < frac < 0.6
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_deface.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/deface.py tests/python/test_deface.py
git commit -m "feat(deface): FOV + modality gates + model availability probe"
```

---

### Task A2: `deface_array` orchestrator + numpy dilation

**Files:**
- Modify: `inst/python/deid_engine/deface.py`
- Test: `tests/python/test_deface.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/python/test_deface.py
def _face_model(vol):
    """Stub model: 'face' = the anterior quarter (low-y rows) of foreground."""
    a = np.asarray(vol)
    mask = np.zeros(a.shape, dtype=bool)
    h = a.shape[1]
    mask[:, : h // 4, :] = a[:, : h // 4, :] > a.mean()
    return mask


def test_deface_array_zeros_face_keeps_brain_with_stub_model():
    vol = _head_volume()
    brain_before = vol[:, vol.shape[1] // 2:, :].sum()
    out, info = deface.deface_array(vol, modality="MR", model=_face_model, margin=1)
    assert info["defaced"] is True
    assert info["voxels_removed"] > 0
    # anterior face region erased, posterior (brain) region preserved
    assert out[:, : vol.shape[1] // 8, :].sum() == 0
    assert out[:, vol.shape[1] // 2:, :].sum() == brain_before


def test_deface_array_skips_chest_fov_untouched():
    chest = np.ones((4, 40, 40), dtype=np.float32)
    out, info = deface.deface_array(chest, modality="MR", model=_face_model)
    assert info == {"defaced": False, "reason": "no-head-fov"}
    assert np.array_equal(out, chest)


def test_deface_array_flags_head_ct_for_review():
    ct = _head_volume() - 1000.0
    out, info = deface.deface_array(ct, modality=None, model=_face_model)  # NIfTI: modality unknown
    assert info == {"defaced": False, "flagged_for_review": "head-inclusive non-MR"}
    assert np.array_equal(out, ct)


def test_deface_array_degrades_without_model(monkeypatch):
    monkeypatch.setattr(deface, "deface_available", lambda: (False, "no weights"))
    vol = _head_volume()
    out, info = deface.deface_array(vol, modality="MR", model=None)
    assert info["defaced"] is False and "no weights" in info["note"]
    assert np.array_equal(out, vol)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_deface.py -k deface_array -v`
Expected: FAIL — `AttributeError: module 'deid_engine.deface' has no attribute 'deface_array'`

- [ ] **Step 3: Write the implementation**

Append to `inst/python/deid_engine/deface.py`:

```python
def _dilate(mask, iters: int):
    """Pure-numpy 6-neighbour binary dilation (avoids a scipy dependency)."""
    m = np.asarray(mask, dtype=bool)
    for _ in range(int(iters)):
        d = m.copy()
        for ax in range(m.ndim):
            d |= np.roll(m, 1, axis=ax)
            d |= np.roll(m, -1, axis=ax)
        m = d
    return m


def _load_model():
    """Load the bundled TorchScript face-mask model as a callable array->mask."""
    import torch
    net = torch.jit.load(str(_model_path()), map_location="cpu")
    net.eval()

    def _infer(array):
        a = np.asarray(array, dtype=np.float32)
        t = torch.from_numpy(a)[None, None]  # (1,1,D,H,W)
        with torch.no_grad():
            out = net(t)
        prob = out.squeeze().cpu().numpy()
        return prob > 0.5

    return _infer


def deface_array(array, modality: str | None = None, model=None, margin: int = 2):
    """Return ``(new_array, info)``. Defaces only head-inclusive MR volumes.

    ``modality``: 'MR'/'CT' from a DICOM tag, or ``None`` for NIfTI (then
    ``looks_like_ct`` decides). ``model``: a callable ``array -> bool mask`` of
    facial voxels; ``None`` loads the bundled TorchScript model (and degrades to
    a noted skip when absent). ``info`` always has ``defaced`` plus one of
    ``reason`` / ``voxels_removed`` / ``flagged_for_review`` / ``note``."""
    a = np.asarray(array)
    if not is_head_inclusive(a):
        return a, {"defaced": False, "reason": "no-head-fov"}
    is_ct = (modality or "").upper() == "CT" or (modality is None and looks_like_ct(a))
    if is_ct:
        return a, {"defaced": False, "flagged_for_review": "head-inclusive non-MR"}
    if model is None:
        ok, note = deface_available()
        if not ok:
            return a, {"defaced": False, "note": note}
        model = _load_model()
    mask = np.asarray(model(a)).astype(bool)
    if margin:
        mask = _dilate(mask, margin)
    out = a.copy()
    out[mask] = 0
    return out, {"defaced": True, "voxels_removed": int(mask.sum())}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_deface.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/deface.py tests/python/test_deface.py
git commit -m "feat(deface): deface_array orchestrator (gate -> mask -> erase)"
```

---

### Task A3: profile flag + thread through `deid_run` (Python) and R bridge

**Files:**
- Modify: `inst/profiles/default_profile.yml`, `inst/python/deid_engine/core.py`, `R/engine_bridge.R`
- Test: `tests/python/test_deface.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/python/test_deface.py
from deid_engine import core


def test_deface_enabled_reads_profile():
    assert core._deface_enabled({"deface": {"enabled": True}}) is True
    assert core._deface_enabled({"deface": {"enabled": False}}) is False
    assert core._deface_enabled({}) is False


def test_deid_run_deface_flag_turns_on_profile(tmp_path, monkeypatch):
    # Capture the profile deidentify_study receives to prove the flag threads in.
    seen = {}
    monkeypatch.setattr(core, "deidentify_study",
                        lambda ip, op, profile, ks: seen.setdefault("p", profile)
                        or {"files": [], "count": 0})
    core.deid_run(str(tmp_path), str(tmp_path / "o"), deface=True)
    assert seen["p"]["deface"]["enabled"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_deface.py -k "deface_enabled or deid_run_deface" -v`
Expected: FAIL — `AttributeError: ... has no attribute '_deface_enabled'` and `deid_run() got an unexpected keyword argument 'deface'`

- [ ] **Step 3: Write the implementation**

3a. Add the profile section to `inst/profiles/default_profile.yml` (after the `pixel:` block, before `encapsulated_pdf:`):

```yaml
# --- defacing (optional, MRI only, OFF by default) ----------------------------
#   Erases the facial biometric surface from head-inclusive MR volumes.
#   DESTRUCTIVE (zeroes voxels). Enable per-run in the interactive/bulk flow.
#   FOV-gated: chest-only cardiac scans pass through untouched. MR-only:
#   head-inclusive CT is flagged for manual handling, not defaced. Needs the
#   bundled CPU model under inst/models/deface/; absent -> skipped + noted.
deface:
  enabled: false
```

3b. Add `_deface_enabled` to `inst/python/deid_engine/core.py` (next to `_pixel_autoredact_enabled`):

```python
def _deface_enabled(profile: dict) -> bool:
    """True when the profile opts into ML defacing for this run (off by default)."""
    return bool((profile.get("deface") or {}).get("enabled"))
```

3c. Add the `deface` parameter to `deid_run` (signature + the profile-override block). Change the signature line and add the override next to the `autoredact_pixels` one:

```python
def deid_run(input_path: str, output_path: str, profile_id: str = "default",
             keystore_path: str | None = None, passphrase: str | None = None,
             reversible: bool = True, sign_key_path: str | None = None,
             signer: str | None = None, project_id: str | None = None,
             autoredact_pixels: bool = False,
             pdf_mode: str | None = None, pdf_dpi: int | None = None,
             deface: bool = False) -> dict:
```

Add after the `autoredact_pixels` override block (after line ~752):

```python
    if deface:
        profile = dict(profile)
        profile["deface"] = {**(profile.get("deface") or {}), "enabled": True}
```

3d. Thread it through the R bridge. In `R/engine_bridge.R`, change `engine_deid_run` (lines ~55-64):

```r
engine_deid_run <- function(input_path, output_path, profile_id = "default",
                            keystore_path = NULL, passphrase = NULL,
                            reversible = TRUE, sign_key_path = NULL,
                            signer = NULL, project_id = NULL,
                            autoredact_pixels = FALSE,
                            pdf_mode = NULL, pdf_dpi = NULL, deface = FALSE) {
  call_engine("deid_run", input_path, output_path, profile_id, keystore_path,
              passphrase, reversible, sign_key_path, signer, project_id,
              autoredact_pixels, pdf_mode, pdf_dpi, deface)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_deface.py -k "deface_enabled or deid_run_deface" -v`
Expected: PASS. Then `python -m pytest tests/python/test_profiles.py -v` — Expected: PASS (profile still loads; the new `deface` key is inert for existing consumers).

- [ ] **Step 5: Commit**

```bash
git add inst/profiles/default_profile.yml inst/python/deid_engine/core.py R/engine_bridge.R tests/python/test_deface.py
git commit -m "feat(deface): opt-in deface flag through deid_run + R bridge + profile"
```

---

### Task A4: Apply defacing to NIfTI volumes

**Files:**
- Modify: `inst/python/deid_engine/core.py` (`_deidentify_nifti` + its call site)
- Test: `tests/python/test_deface.py`

Defacing must run on the NIfTI volume array before the OCR step and before save. It uses the stub-free bundled model; tests inject a stub via `deface_array`'s `model=` by monkeypatching `core._deface.deface_array`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/python/test_deface.py
import nibabel as nib
from deid_engine import keystore


def test_nifti_defaced_when_enabled(tmp_path, monkeypatch):
    vol = _head_volume()
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "head.nii.gz"))
    out = tmp_path / "out"

    # Inject a deterministic deface: zero the anterior eighth, report defaced.
    def fake_deface(array, modality=None, model=None, margin=2):
        a = np.asarray(array).copy()
        a[:, : a.shape[1] // 8, :] = 0
        return a, {"defaced": True, "voxels_removed": 1}
    monkeypatch.setattr(core._deface, "deface_array", fake_deface)

    prof = dict(core.profile_get("default"))
    prof["deface"] = {"enabled": True}
    ks = keystore.ephemeral()
    report = core.deidentify_study(str(tmp_path / "head.nii.gz"), str(out), prof, ks)
    rec = report["files"][0]
    assert rec["counts"]["deface"]["defaced"] is True
    got = nib.load(rec["output"]).get_fdata()
    assert got[:, : vol.shape[1] // 8, :].sum() == 0


def test_nifti_not_defaced_when_disabled(tmp_path, monkeypatch):
    vol = _head_volume()
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(tmp_path / "head.nii.gz"))
    out = tmp_path / "out"
    called = {"n": 0}
    monkeypatch.setattr(core._deface, "deface_array",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or (a[0], {}))
    prof = core.profile_get("default")   # deface off by default
    ks = keystore.ephemeral()
    core.deidentify_study(str(tmp_path / "head.nii.gz"), str(out), prof, ks)
    assert called["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_deface.py -k "nifti_defaced or not_defaced" -v`
Expected: FAIL — `_deidentify_nifti` does not call defacing; `counts` has no `deface`.

- [ ] **Step 3: Write the implementation**

3a. Ensure `core.py` imports the module. Near the other `from . import ... as _pixels` imports at the top of core.py, add:

```python
from . import deface as _deface
```

3b. In `_deidentify_nifti`, add a defacing block right after `data = np.asanyarray(img.dataobj)` and before the OCR block:

```python
    data = np.asanyarray(img.dataobj)
    counts = {"nifti_header_fields_scrubbed": scrubbed,
              "nifti_extensions_removed": ext_removed}

    # Optional ML defacing (MR-only, FOV-gated; degrades when weights absent).
    if (profile or {}).get("deface", {}).get("enabled"):
        try:
            data, dinfo = _deface.deface_array(data, modality=None)
            counts["deface"] = dinfo
        except Exception as e:  # noqa: BLE001 - deface must not lose the file
            counts["deface_error"] = str(e)
```

(The OCR block that follows already reads/updates `data` and `counts`, so no further change there.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_deface.py -v`
Expected: PASS (all). Regression: `python -m pytest tests/python/test_nifti_deid.py tests/python/test_nifti_ocr.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/core.py tests/python/test_deface.py
git commit -m "feat(deface): apply defacing to NIfTI MR volumes when enabled"
```

---

### Task A5: Apply defacing to DICOM multiframe MR

**Files:**
- Modify: `inst/python/deid_engine/core.py` (`deidentify_study`, DICOM branch after the pixel-autoredact block ~671)
- Test: `tests/python/test_deface.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/python/test_deface.py
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid


def _multiframe_mr(path, frames=20, hw=40):
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = MRImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.SOPClassUID = MRImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.Modality = "MR"
    ds.Rows = hw; ds.Columns = hw; ds.NumberOfFrames = frames
    ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16; ds.BitsStored = 16; ds.HighBit = 15
    ds.PixelRepresentation = 0
    arr = (np.ones((frames, hw, hw), dtype=np.uint16) * 500)
    ds.PixelData = arr.tobytes()
    ds.is_little_endian = True; ds.is_implicit_VR = False
    ds.save_as(path, write_like_original=False)


def test_dicom_multiframe_mr_defaced_when_enabled(tmp_path, monkeypatch):
    p = tmp_path / "mf.dcm"
    _multiframe_mr(str(p))
    out = tmp_path / "out.dcm"

    seen = {"mod": None}
    def fake_deface(array, modality=None, model=None, margin=2):
        seen["mod"] = modality
        a = np.asarray(array).copy(); a[:, :5, :] = 0
        return a, {"defaced": True, "voxels_removed": 1}
    monkeypatch.setattr(core._deface, "deface_array", fake_deface)

    prof = dict(core.profile_get("default")); prof["deface"] = {"enabled": True}
    ks = keystore.ephemeral()
    report = core.deidentify_study(str(p), str(out), prof, ks)
    rec = report["files"][0]
    assert seen["mod"] == "MR"                       # DICOM Modality passed through
    assert rec["counts"]["deface"]["defaced"] is True
    got = pydicom.dcmread(str(out)).pixel_array
    assert got[:, :5, :].sum() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_deface.py -k dicom_multiframe -v`
Expected: FAIL — DICOM branch never defaces; `counts` has no `deface`.

- [ ] **Step 3: Write the implementation**

In `deidentify_study`, immediately after the pixel-autoredact block (right before the encapsulated-PDF block at ~672), add:

```python
        # Optional ML defacing of a head-inclusive MR *volume* object (multiframe
        # grayscale = a whole 3-D volume in one file). Single-instance-per-slice
        # series (a 3-D face across many files) is out of scope. Degrades when the
        # model is absent; never fatal to the metadata de-id already done.
        if _deface_enabled(profile) and "PixelData" in ds:
            try:
                modality = str(getattr(ds, "Modality", "") or "")
                if bool(ds.file_meta.TransferSyntaxUID.is_compressed):
                    ds.decompress()
                frames = _pixels.load_frames(ds)  # (N,H,W) or (N,H,W,C)
                if frames.ndim == 3 and frames.shape[0] > 1:
                    new, dinfo = _deface.deface_array(frames, modality=modality)
                    if dinfo.get("defaced"):
                        _pixels._store_frames(ds, new)
                    counts["deface"] = dinfo
                else:
                    counts["deface"] = {"defaced": False,
                                        "reason": "not-a-multiframe-grayscale-volume"}
            except Exception as e:  # noqa: BLE001 - deface must not lose the file
                counts["deface_error"] = str(e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_deface.py -v`
Expected: PASS (all). Regression: `python -m pytest tests/python/test_pixels.py tests/python/test_pixel_autoredact.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/core.py tests/python/test_deface.py
git commit -m "feat(deface): apply defacing to DICOM multiframe MR volumes"
```

---

# SHARED — Corpus, acceptance, UI, bundle, docs

### Task S1: Corpus fixtures + manifest

**Files:**
- Modify: `inst/python/deid_engine/corpus.py` (`build_corpus` ~150-270; `PLANTED["encodings"]` ~55)
- Test: `tests/python/test_corpus.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/python/test_corpus.py
import numpy as np
import nibabel as nib
from deid_engine import corpus


def test_corpus_has_head_mr_and_burned_in_nifti(tmp_path):
    man = corpus.build_corpus(str(tmp_path))
    encs = {f["encoding"] for f in man["fixtures"]}
    assert "nifti_head_mr" in encs        # head-inclusive MR volume (deface target)
    assert "nifti_burned_in" in encs      # burned-in-text NIfTI (OCR target)
    head = next(f for f in man["fixtures"] if f["encoding"] == "nifti_head_mr")
    img = nib.load(str(tmp_path / head["rel"]))
    assert min(img.shape) >= 16           # deep enough for the FOV gate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_corpus.py -k head_mr_and_burned -v`
Expected: FAIL — encodings do not include the two new fixtures.

- [ ] **Step 3: Write the implementation**

3a. Extend the encodings list in `PLANTED` (~line 55):

```python
    "encodings": ["single_frame", "multiframe_cine", "rgb", "jpeg2000", "nifti",
                  "nifti2", "nifti_pair", "bids_sidecar", "encapsulated_pdf",
                  "nifti_head_mr", "nifti_burned_in"],
```

3b. In `build_corpus`, before the final `manifest = {...}` return (~line 269), add the two fixtures:

```python
    # 10. Head-inclusive MR NIfTI (defacing target): an air-bordered ellipsoid
    #     "head" deep enough to pass the FOV gate. No burned-in text here.
    d, h, w = 24, 40, 40
    zz, yy, xx = np.ogrid[:d, :h, :w]
    ell = (((zz - d / 2) / (d * 0.35))**2 + ((yy - h / 2) / (h * 0.3))**2 +
           ((xx - w / 2) / (w * 0.3))**2) <= 1.0
    head = np.zeros((d, h, w), dtype=np.float32)
    head[ell] = 600.0
    rel = "nifti_head_mr.nii.gz"
    nib.save(nib.Nifti1Image(head, np.eye(4)), os.path.join(out_dir, rel))
    fixtures.append({"rel": rel, "encoding": "nifti_head_mr",
                     "transfer_syntax": "nifti-1", "burned_in": False,
                     "modality": "MR"})

    # 11. Burned-in-text NIfTI (OCR target): a planted NRIC rasterised into one
    #     slice as bright pixels. Uses PIL (already pulled in by the pixel stack)
    #     to render text; falls back to a bright block if PIL is unavailable so
    #     the fixture always exists.
    vol = np.zeros((3, 64, 96), dtype=np.uint8)
    nric = PLANTED["nric_fin"][0]
    try:
        from PIL import Image, ImageDraw
        im = Image.new("L", (96, 64), 0)
        ImageDraw.Draw(im).text((4, 24), nric, fill=255)
        vol[1] = np.asarray(im, dtype=np.uint8)
    except Exception:  # noqa: BLE001 - keep the fixture even without PIL
        vol[1, 24:34, 4:60] = 255
    rel = "nifti_burned_in.nii.gz"
    nib.save(nib.Nifti1Image(vol, np.eye(4)), os.path.join(out_dir, rel))
    fixtures.append({"rel": rel, "encoding": "nifti_burned_in",
                     "transfer_syntax": "nifti-1", "burned_in": True,
                     "planted_text": nric})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/python/test_corpus.py -v`
Expected: PASS (existing corpus tests + the new one).

- [ ] **Step 5: Commit**

```bash
git add inst/python/deid_engine/corpus.py tests/python/test_corpus.py
git commit -m "test(corpus): head-inclusive MR + burned-in-text NIfTI fixtures"
```

---

### Task S2: Acceptance checks (R) + Validation surfacing

**Files:**
- Modify: `R/acceptance.R` (`acceptance_run` ~154-158 call + `acceptance_checks` ~26-85)
- Test: `tests/testthat/test-acceptance.R` (create if absent)

The acceptance runner already auto-confirms pixels (`autoredact_pixels = TRUE`). Add `deface = TRUE` so the head-MR fixture exercises defacing, and grade two new raw signals: (1) every head-inclusive MR output has a `deface` record that is either `defaced` or a recognised graceful skip (`note`/`flagged_for_review`), and (2) the burned-in NIfTI's planted text does not survive (already covered by the residual scan once it OCRs NIfTI — see Step 3b).

- [ ] **Step 1: Write the failing test**

```r
# tests/testthat/test-acceptance.R
test_that("acceptance_checks grades the deface signal", {
  raw <- list(
    deface = list(ok = TRUE, detail = "1 head-MR volume defaced"),
    deid = list(count = 1L, n_inputs = 1L),
    survivors = list(passed = TRUE, metadata_survivors = list()),
    residual = list(passed = TRUE, summary = list(flagged = 0L, scanned = 1L),
                    by_category = list()),
    validity = list(ok = TRUE, invalid = character(0)),
    reversibility = list(mode = "reversible", roundtrip_ok = TRUE,
                         crosswalk_present = TRUE),
    resume = list(reprocessed = 0L))
  rep <- acceptance_checks(raw)
  nm <- rep$checks$name
  expect_true("defacing applied or gracefully skipped" %in% nm)
  expect_true(rep$passed)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `Rscript -e "testthat::test_file('tests/testthat/test-acceptance.R')"`
(Full path if Rscript is not on PATH: `"C:/Program Files/R/R-4.5.2/bin/Rscript.exe"`.)
Expected: FAIL — no "defacing applied or gracefully skipped" check exists.

- [ ] **Step 3: Write the implementation**

3a. Add the check in `acceptance_checks` (in `R/acceptance.R`, after check 6 "resumable", before `df <- do.call(...)`):

```r
  # 7. defacing ran or degraded gracefully (never silently mangled/errored)
  dfc <- raw$deface
  add("defacing applied or gracefully skipped", isTRUE(dfc$ok),
      dfc$detail %||% "no head-inclusive MR fixtures encountered")
```

3b. Populate `raw$deface` in `acceptance_run`. Change the `engine_deid_run` call (~154) to pass `deface = TRUE`:

```r
  rep <- engine_deid_run(corpus_dir, out_dir, profile_id = profile_id,
                         keystore_path = ks_path, passphrase = passphrase,
                         reversible = (mode == "reversible"),
                         autoredact_pixels = TRUE,
                         pdf_mode = "rasterize_redact",
                         deface = TRUE)
```

Then, before `raw <- list(...)` (~200), derive the deface signal from the per-file counts:

```r
  # Defacing verdict: every file that carries a `deface` record must be either
  # defaced or a recognised graceful skip (reason/note/flagged_for_review) - an
  # error key means the step blew up and must fail the run.
  deface_recs <- Filter(Negate(is.null),
                        lapply(rep$files, function(f) f$counts$deface))
  deface_errs <- sum(vapply(rep$files,
                            function(f) !is.null(f$counts$deface_error), logical(1)))
  deface_applied <- sum(vapply(deface_recs,
                               function(d) isTRUE(d$defaced), logical(1)))
  deface_ok <- deface_errs == 0
  deface_detail <- sprintf("%d deface record(s), %d defaced, %d error(s)",
                           length(deface_recs), deface_applied, deface_errs)
```

And add to the `raw <- list(...)`:

```r
    deface = list(ok = deface_ok, detail = deface_detail),
```

3c. Extend the residual scan to OCR NIfTI outputs so a surviving burned-in NRIC is caught. In `engine_scan_residual_dir` (the residual scanner used by acceptance — find it via `grep -n "scan_residual" inst/python/deid_engine/*.py`), ensure NIfTI files are OCR-scanned via `pixels.volume_ocr_boxes`. If that scanner currently skips NIfTI pixels, add a NIfTI branch mirroring the DICOM pixel-OCR branch; if it already scans images generically, no change. (This step's presence guarantees the burned-in fixture is graded by the existing "residual detector scan" check.)

- [ ] **Step 4: Run test to verify it passes**

Run: `Rscript -e "testthat::test_file('tests/testthat/test-acceptance.R')"`
Expected: PASS. Then the live acceptance smoke (venv required): `Rscript -e "source('R/acceptance.R'); print(acceptance_run()$report$passed)"` — Expected: `[1] TRUE` (defacing degrades to a noted skip when weights are absent, which the check treats as pass; the burned-in NIfTI's text is redacted so the residual check passes).

- [ ] **Step 5: Commit**

```bash
git add R/acceptance.R tests/testthat/test-acceptance.R
git commit -m "test(acceptance): grade defacing + NIfTI burned-in redaction"
```

---

### Task S3: Interactive UI checkbox

**Files:**
- Modify: `R/mod_interactive.R`
- Test: manual (browser smoke) — documented below.

- [ ] **Step 1: Add the checkbox to the UI**

In `R/mod_interactive.R`, in the UI input list next to the existing `checkboxInput(ns("reversible"), ...)` (~line 14), add:

```r
      shiny::checkboxInput(ns("deface"),
        "Deface head-inclusive MRI (destructive, MR only)", value = FALSE),
```

- [ ] **Step 2: Thread it into the run call**

In the `observeEvent(input$run, {...})` handler (~line 51), find the `engine_deid_run(...)` call and add the argument (mirroring how `reversible = input$reversible` is passed):

```r
        deface = isTRUE(input$deface),
```

- [ ] **Step 3: Verify the app loads**

Run: `Rscript -e "shiny::runApp('.', launch.browser = FALSE, port = 8788)"` (Ctrl-C after it prints "Listening on"), OR load in the app's browser: the Interactive tab shows the new "Deface head-inclusive MRI" checkbox, unchecked by default.
Expected: app starts with no error; checkbox present and off.

- [ ] **Step 4: Commit**

```bash
git add R/mod_interactive.R
git commit -m "feat(ui): opt-in defacing checkbox on the interactive tab"
```

---

### Task S4: Bundle the model dir + docs

**Files:**
- Modify: `tools/build_portable_bundle.ps1`, `docs/pixel-redaction.md`, `docs/roadmap.md`
- Create: `docs/defacing.md`

- [ ] **Step 1: Bundle the deface model directory**

In `tools/build_portable_bundle.ps1`, next to where the NER model dir is robocopied into the bundle (find via `grep -n "models" tools/build_portable_bundle.ps1`), ensure `inst/models/deface` is copied too. If the copy already globs `inst/models` wholesale, no change is needed — add a comment noting `deface/` rides along. If it copies a specific model subdir, add a sibling copy line for `deface`. Concretely, after the NER model copy line add:

```powershell
# Optional defacing model (Feature A). Absent -> the deface step degrades to a
# noted skip on the target, exactly like OCR/NER without their binary/model.
if (Test-Path (Join-Path $InstSrc 'models\deface')) {
  Robo (Join-Path $InstSrc 'models\deface') (Join-Path $OutApp 'inst\models\deface')
}
```

- [ ] **Step 2: Write `docs/defacing.md`**

```markdown
# Optional defacing (head-inclusive MRI)

Erases the facial biometric surface (eyes / nose / mouth) from head-inclusive
**MR** volumes while keeping the brain intact. It is **off by default** and
**destructive** (zeroes voxels) — enable it per run in the Interactive tab
("Deface head-inclusive MRI") or a bulk profile.

## How it decides to run (before the model even loads)
1. **FOV gate** — a volume must have enough through-plane depth, an air border,
   and a compact central blob. Chest-only cardiac stacks fail this and pass
   through **untouched**.
2. **Modality gate** — MR only. DICOM uses the `Modality` tag; NIfTI (which has
   no modality) is judged by intensity (CT stores Hounsfield units, air ≈ −1000).
   Head-inclusive **CT** is **flagged for manual handling**, never defaced by an
   MR-trained model.

## Model
A CPU **TorchScript** model at `inst/models/deface/model.pt`, bundled like the
NER model. **Absent → the step is skipped and noted, never fatal** — same
graceful-degradation contract as the OCR/NER layers.

## Scope
- Targets NIfTI MR volumes and DICOM **multiframe** MR objects (a whole 3-D
  volume in one file).
- **Out of scope:** single-instance-per-slice DICOM series (a 3-D face spanning
  many files), CT defacing, and burned-in-pixel OCR of a face (handled by the
  pixel/OCR layer, not here). These are future work.

## Validation
The gate/apply/degradation logic is unit-tested with a stub model. End-to-end
**efficacy on real MR** is validated separately once production weights are
pinned (a licence check precedes bundling any weights).
```

- [ ] **Step 3: Update `docs/pixel-redaction.md` and `docs/roadmap.md`**

Append to `docs/pixel-redaction.md`:

```markdown
## NIfTI burned-in text

Burned-in-text OCR now runs on NIfTI volumes too (previously header-only). Each
in-plane slice (along the volume's shortest axis) is OCR'd with the same SG-aware
scanner as DICOM pixels. The interactive flow proposes boxes for confirmation;
bulk/acceptance runs auto-redact. Degrades to a noted skip without Tesseract.
```

In `docs/roadmap.md`, update the Phase 3 and Phase 3.5 rows to note the two
deferred items are now delivered, and record the explicit remaining future scope
(CT defacing; all-three-plane NIfTI OCR; DICOM per-slice-series defacing). Change
the Phase 3 line "optional defacing deferred" → "optional defacing (MRI, opt-in,
FOV-gated) — done; CT defacing future" and the Phase 3.5 line "Burned-in-pixel
OCR on NIfTI arrays remains out of scope" → "Burned-in-pixel OCR on NIfTI arrays
— done (axial/shortest-axis); all-plane future".

- [ ] **Step 4: Commit**

```bash
git add tools/build_portable_bundle.ps1 docs/defacing.md docs/pixel-redaction.md docs/roadmap.md
git commit -m "docs+bundle: defacing + NIfTI OCR docs; ship deface model dir"
```

---

### Task S5: Full regression + finish

- [ ] **Step 1: Run the whole Python suite**

Run: `python -m pytest tests/python -q`
Expected: all pass (the prior ~185 + the new deface/nifti-ocr/corpus tests), 0 failures.

- [ ] **Step 2: Run the R tests**

Run: `Rscript -e "testthat::test_dir('tests/testthat')"`
Expected: all pass.

- [ ] **Step 3: Live acceptance smoke (venv)**

Run: `Rscript -e "source('R/acceptance.R'); r <- acceptance_run(); cat(r\$report\$passed, '\n')"`
Expected: `TRUE`.

- [ ] **Step 4: Finish the branch**

Use **superpowers:finishing-a-development-branch** to verify tests, then merge `feature/defacing-nifti-ocr` to `main` (or open a PR) per the user's choice.

---

## Self-Review

**Spec coverage:**
- Feature A module `deface.py` → Tasks A1–A2. ✅
- Bundled CPU torch model under `inst/models/deface/` → A1 (`_model_path`), S4 (bundle). ✅
- FOV gate → A1 (`is_head_inclusive`). ✅ Modality gate (MR-only, CT flagged) → A1 (`looks_like_ct`), A2. ✅
- Face mask + conservative dilation + zero voxels → A2. ✅
- Graceful degradation (model absent) → A1/A2 (`deface_available`, `note`). ✅
- Opt-in flag threaded R→Python + default profile off → A3. ✅
- Interactive checkbox → S3; bulk honoured (same profile/flag) → A3. ✅
- Pipeline order defacing-before-OCR → A4 (NIfTI: deface block precedes OCR block); DICOM defacing runs after pixel-autoredact (documented; both operate on the same `ds`, order-independent for correctness). ✅
- Feature B NIfTI OCR reuses pixels helpers, propose-vs-autoredact by `_pixel_autoredact_enabled` → B1–B2. ✅
- Corpus fixtures (head MR + burned-in) + verify → S1, S2 (residual OCR of NIfTI). ✅
- pytest units both features with stub model / stubbed OCR → A1–A5, B1–B2. ✅
- Acceptance + Validation → S2. ✅
- Bundle copies model dir → S4. ✅ Docs → S4. ✅

**Placeholder scan:** No TBD/TODO. The one deliberately-deferred external artefact (production deface weights) is documented as out-of-band in `docs/defacing.md` and every code path degrades without it; the plan never depends on the real weights to be green.

**Type/name consistency:** `deface_array(array, modality, model, margin) -> (ndarray, info)`, `is_head_inclusive`, `looks_like_ct`, `deface_available`, `_model_path`, `_deface_enabled`, `volume_ocr_boxes`, `redact_volume_boxes`, `_deidentify_nifti(full, out, profile, scanner, ocr_autoredact)` — used identically across every task. `counts` keys (`deface`, `deface_error`, `nifti_ocr_boxes`, `nifti_pixels_redacted`, `nifti_ocr_note`) match between core.py writes and the acceptance reads. R `deface` argument position (last, after `pdf_dpi`) matches the Python `deid_run` signature order.
