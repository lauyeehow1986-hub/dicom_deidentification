# Design — Encapsulated-PDF rasterize + OCR-redact (and SR audit)

**Date:** 2026-09-03
**Status:** Approved (design); implementation pending
**Area:** `inst/python/deid_engine/` engine + R bridge + profiles + corpus/acceptance

## Problem

Encapsulated PDFs embedded in DICOM (`EncapsulatedDocument (0042,0011)`, SOP Class
`1.2.840.10008.5.1.4.1.1.104.1` *Encapsulated PDF Storage*) are currently removed
**wholesale** via PS3.15 action `X`. A clinical report with no PHI is deleted just the
same as one full of PHI — lossy and blunt. There is no PDF library in the venv and no PDF
fixture in the synthetic corpus, so the pathway is entirely unexercised.

Structured Reports (SR) are a *separate* concern and are **already** largely handled: the
`core._walk` scanner recurses into every `SQ` and auto-scans text VRs, so
`ContentSequence > TextValue (0040,A160)` and `PersonName (0040,A123)` are scrubbed today.
This design's SR scope is a targeted **audit + fixture**, not a rebuild.

## Goal

Replace blind PDF removal with a **rasterize + OCR-redact + flatten** pipeline that keeps
the report content while destroying PHI, with a strong, uniform guarantee — behind an
**opt-in profile mode** so current behavior is unchanged by default.

## Chosen approach & key decision: the renderer

**`pypdfium2`** (wraps Google's PDFium):

- **License:** Apache-2.0 / BSD-3-Clause — permissive; clean against the MIT core and fine
  for SingHealth internal use. (Rejected alternatives: PyMuPDF = AGPL; poppler = GPL
  binary — both a licensing/air-gap problem.)
- Ships **prebuilt wheels with the PDFium binary bundled** — no system binary, no admin,
  `pip`/`uv`-installable, fully offline once the wheel is staged.
- Renders pages to raster (→ PIL/NumPy) and can extract the text layer.

## Architecture

### 1. Detection (`core.deidentify_study`)
An instance is an encapsulated PDF when SOP Class is Encapsulated PDF Storage **or**
`MimeTypeOfEncapsulatedDocument (0042,0012) == "application/pdf"` with bytes present in
`EncapsulatedDocument (0042,0011)`. Detection is independent of the DICOM extension so a
non-`.dcm` encapsulated instance is still caught.

### 2. New module `deid_engine/documents.py`
Given the PDF bytes + the study's already-built `TextScanner` + Tesseract config:

- `pypdfium2` renders each page → PIL image at configurable DPI (**default 150**).
- OCR each page raster and filter words through the **existing layered `TextScanner`**
  (same detectors used everywhere) → PHI word boxes → black-box redaction via
  `pixels.apply_boxes`. This reuses/refactors the `pixels.ocr_phi_boxes` logic into a
  shared helper `image_phi_boxes(img_uint8, scanner)` so PDF pages and DICOM frames share
  one code path.
- **Flatten:** rebuild an image-only PDF from the redacted page rasters via Pillow
  (`Image.save(..., save_all=True, append_images=...)`, already in the venv). Flattening
  **removes the text layer by construction**, so any hidden/selectable text PHI is gone
  even if OCR did not read it visually.
- Re-embed the flattened bytes into `EncapsulatedDocument`; refresh
  related length/encoding as needed and keep the DICOM wrapper valid.

### 3. Policy — profile `encapsulated_pdf.mode` (backward-compatible)
- `remove` — **default, unchanged**. Whole-document strip (current action `X`). Zero
  regression risk.
- `rasterize_redact` — the new path. **If Tesseract is absent it degrades to `remove`**
  (never keeps an un-scanned PDF) and records a note — mirroring the pixel auto-redact
  safety philosophy.
- In `rasterize_redact` mode we **always** rasterize + flatten every encapsulated PDF (no
  "is it clean?" branch) so the guarantee is uniform and the residual scan stays simple.
  Utility cost: the report loses its selectable text layer and grows in size — accepted for
  a de-id tool; refinable later.

Default and sample profiles keep `mode: remove`. `rasterize_redact` is opted into per
project/profile.

### 4. SR audit
Add an SR corpus fixture (ContentSequence with a planted PHI `TextValue` + `PersonName` +
a date value type) and confirm the existing scanner scrubs them. Close any gap the fixture
surfaces (expected small — e.g. an SR-specific value-type tag not in `_TEXT_VRS`).

### 5. Corpus + acceptance (surface the gap, then close it)
- `corpus.build_corpus` gains an **Encapsulated PDF Storage** fixture whose embedded PDF
  has a **planted PHI name visibly rendered** (built with Pillow → image PDF; no new build
  dep). Mark it in the manifest (`encapsulated_pdf: true`).
- `check_survivors` / `scan_residual` learn to **render + OCR (and text-extract)** an output
  encapsulated PDF so the acceptance gate actually proves the planted name is gone.
- `acceptance_run` exercises `rasterize_redact` so PASS genuinely covers embedded-PDF PHI —
  same rigor as the Phase 7 Tesseract discovery.

### 6. Wiring & packaging
- Params flow `deid_run(..., pdf_mode=None, pdf_dpi=None)` → `engine_deid_run` (R bridge).
  The profile's `encapsulated_pdf` block is the primary source; the explicit params, when
  not `None`, override it for scripted runs.
- `inst/python/build_venv.ps1` gains a `pypdfium2` install (new `-Pdf` extra, or folded into
  base). The air-gap bundle stages the wheel; `docs/airgap-install.md` updated.
- Code + TDD tests run on the **connected dev box** (install `pypdfium2` into the dev venv);
  the venv/bundle rebuild is the same network-bound packaging step used for prior phases.

## Testing (strict TDD)

pytest (engine) + testthat (R bridge). One behavior per test; watch each fail first.

- `documents.py`: page render count; `image_phi_boxes` finds a planted box; flatten output
  has **no extractable text** (assert via pypdfium2 textpage on the result); OCR of the
  flattened pages does **not** contain the planted tokens; a clean PDF round-trips with its
  visible non-PHI text still OCR-readable.
- Degrade path: `rasterize_redact` with Tesseract absent → falls back to `remove` + note,
  never emits an un-scanned PDF.
- `core.deidentify_study`: an encapsulated-PDF instance under `mode: remove` still strips
  (regression guard); under `rasterize_redact` produces a valid DICOM whose
  `EncapsulatedDocument` is the flattened, redacted PDF and `PatientIdentityRemoved == YES`.
- SR fixture: planted TextValue/PersonName/date scrubbed.
- Acceptance: `acceptance_run` PASS 6/6 in both modes with the PDF fixture present and
  `rasterize_redact` enabled.
- R bridge: `engine_deid_run` threads `pdf_mode`/`pdf_dpi`; smoke via a fake engine where
  network/venv is unavailable.

## Non-goals (v1)

- Preserving a searchable text layer in redacted PDFs (always-flatten).
- Vector/graphics-aware redaction; layout-faithful text rewriting.
- Redacting PHI that is visually present but OCR-unreadable (same limitation as pixel
  redaction — documented).
- CDA/XML encapsulated documents (only `application/pdf` in v1; others keep `remove`).

## Risks

- **OCR recall** on low-DPI or stylized text — mitigated by flatten (kills text layer) +
  a sensible default DPI; DPI is configurable.
- **Dependency footprint** — pypdfium2 wheel adds ~a few MB to the bundle; acceptable.
- **PDF re-embed validity** — covered by a reload-and-validate test on the output DICOM.
