# Optional ML Defacing + NIfTI Burned-in OCR — Design

**Date:** 2026-09-04
**Status:** Approved (brainstorming)
**Scope:** Two independent extensions to the Phase-3 pixel / burned-in-PHI layer, closing
the two items deferred from Phase 3 / Phase 3.5:

1. **Optional defacing** of head-inclusive MRI volumes (facial biometric removal).
2. **Burned-in-text OCR on NIfTI arrays** (was out of scope; now in).

Both are independent and can be implemented and committed separately. They are grouped
here because they share the corpus / acceptance / bundle plumbing.

## Guiding constraints (from the project + the environment)

- **Air-gapped, no-admin, locked-down Windows.** No FSL, no admin installers. The standard
  defacer (`pydeface`) needs FSL and is therefore infeasible. Deps must be pip/uv-installable
  into the relocatable venv, or a bundled model file.
- **`torch>=2.2` (CPU) is already in the venv** (for the NER model). An ML defacer therefore
  costs only a small bundled model + an inference module — not a second DL framework. This is
  why the ML route is affordable here.
- **Established codebase pattern:** every optional detector (Presidio, transformer NER, Tesseract
  OCR) is *opt-in-capable and degrades gracefully* — a missing binary/model yields an empty,
  noted result, never a fatal error. Both new features MUST follow this pattern.
- **Cross-cutting invariants** (roadmap): valid viewable DICOM out (NIfTI out for NIfTI in);
  never commit real data — synthetic fixtures only; everything offline; reversible by default.

## Decisions locked during brainstorming

| Question | Decision |
|---|---|
| Defacing approach | **Bundled ML defacer** (CPU PyTorch), weights shipped like the NER model. |
| Defacing modality scope | **MRI only.** Head-inclusive CT is detected and *flagged for manual handling*, not mis-defaced by an off-domain model. |
| Defacing trigger | **Opt-in per run, FOV-gated.** Off by default; when enabled, a head/face field-of-view gate still skips chest-only volumes untouched. |
| NIfTI burned-in OCR behavior | **Match the existing DICOM pixel UX:** propose boxes for reviewer confirmation in the interactive flow; bulk/acceptance auto-redact. |
| NIfTI OCR slice coverage | **Axial-only first** (cost); all-3-orthogonal-planes is a later toggle, not in this spec. |
| Exact deface weights | Pinned at implementation time after a **license check** (candidates: SynthStrip-derived or an Apache/MIT brain-extraction net). Design assumes ~tens of MB, CPU inference. |

---

## Feature A — ML Defacing (MRI · opt-in · FOV-gated)

### Module
`inst/python/deid_engine/deface.py` — new, self-contained. One responsibility: given a volume
array (+ optional modality/orientation hints), decide whether it is a defaceable head-inclusive
MR volume and, if so, return the face-region mask and the defaced array.

### Model
- Weights bundled under `inst/models/deface/` (mirrors `inst/models/` for the NER model).
- CPU PyTorch. Loaded lazily on first use; a `deface_available()` probe mirrors `_ocr_available()`.
- Exact model + license pinned during implementation (see decisions table).

### Per-volume pipeline (only when the run enables defacing)
1. **FOV gate** — `is_head_inclusive(array, hints)`: cheap intensity/geometry heuristic
   (an air-surrounded, head-shaped superior structure), preferring DICOM `BodyPartExamined` /
   `ImageOrientationPatient` when present. Chest-only → return `{defaced: false, reason: "no-head-fov"}`,
   volume untouched.
2. **Modality gate** — MR only. A head-inclusive **non-MR (CT)** volume → skip defacing and set
   `{defaced: false, flagged_for_review: "head-inclusive non-MR"}` so it surfaces in QA for manual
   handling rather than being silently passed or wrongly defaced.
3. **Infer face mask** — model → binary mask of facial soft-tissue surface (eyes / nose / mouth),
   brain **excluded**, with a conservative dilation margin. Deterministic (eval mode, fixed seed).
4. **Apply (destructive)** — zero the masked voxels; brain region untouched. Returns the modified
   array + counts (`voxels_removed`). DICOM writes back through the existing pixel store path;
   NIfTI writes back through the class-preserving `_deidentify_nifti` save.
5. **Record** — `{defaced: true, voxels_removed: N}` into the file's report record.

### Degradation
Model weights absent → `{defaced: false, note: "deface model unavailable"}`, never fatal —
identical to how OCR/NER degrade. A run with defacing enabled on a box without the model
completes normally, with the skip recorded.

### Integration
- **Flag:** `deface: {enabled: bool}` threaded `engine_bridge.R` → `engine_deid_run` → `core`.
- **Interactive UI:** a checkbox in `mod_interactive.R` (or the profile editor):
  "Deface head-inclusive MRI (destructive)". Default off.
- **Bulk:** honored per run via the job's profile/options.
- **Pipeline order:** defacing runs **before** the OCR / pixel-redaction step.

---

## Feature B — NIfTI Burned-in-text OCR (matches DICOM UX)

### Where
Extends `_deidentify_nifti` in `inst/python/deid_engine/core.py`; reuses the OCR helpers already
in `inst/python/deid_engine/pixels.py` (`_frame_to_uint8`, `image_phi_boxes`, `_ocr_available`)
and the same SG-aware text scanner used for DICOM pixels.

### Pipeline
1. After the existing header scrub + extension strip, iterate the volume's slices along the
   **axial** axis.
2. For each slice: `_frame_to_uint8(slice)` → `image_phi_boxes(img, scanner)` — the same
   scanner (NRIC/FIN/phone/email/gazetteer/NER) used elsewhere, so detection is consistent.
3. **Interactive flow:** collect proposed boxes and hand them to the reviewer for confirmation,
   exactly like the DICOM pixel viewer.
   **Bulk / acceptance flow:** auto-redact — zero each confirmed box on its slice within the
   volume array.
4. Save via the existing NIfTI-1/2 class-preserving path.
5. **Record** — `{nifti_ocr_boxes: M, nifti_pixels_redacted: K}` in the file report.

### Degradation
No Tesseract binary → empty box list + note (existing `_ocr_available`), never fatal.

---

## Verification (both features)

### Corpus fixtures (`inst/python/deid_engine/corpus.py`)
- **A-positive:** a synthetic head-inclusive MR NIfTI with a recognizable facial surface →
  after defacing, assert the facial-surface voxels changed **and** a designated brain-region
  block is preserved (defacing must not eat the ROI).
- **A-negative:** a chest-only MR volume → assert it passes through untouched (FOV gate skips it).
- **B:** a NIfTI slice carrying planted burned-in PHI text → assert zero planted survivors after
  OCR-redact.

### pytest units
- `tests/python/test_deface.py`: FOV gate (positive/negative), modality gate (CT → flagged),
  mask-apply (voxels zeroed, brain preserved) against a tiny synthetic array with a stub/mock
  model, and graceful degradation when the model is absent. The real-model efficacy path is
  guarded and skipped when weights are not present.
- `tests/python/test_corpus.py` / NIfTI-OCR unit: box detection on a synthetic slice, redaction
  zeros the box, degradation when Tesseract is absent.

### R acceptance + Validation tab
- `R/acceptance.R` gains checks: defacing plumbing + degradation always verified live; deface
  *efficacy* verified when weights are present; NIfTI-OCR redaction verified via the corpus.
- The in-app **Validation** tab surfaces the new checks (no UI redesign — same table).

### Bundle
- `tools/build_portable_bundle.ps1` copies `inst/models/deface/` into the bundle (like the NER
  model); `BUNDLE_MANIFEST.json` covers the new files; on-target `verify` stays green.

## Docs
- New `docs/defacing.md` (what it does, MRI-only + FOV gate, destructive warning, degradation).
- Update `docs/pixel-redaction.md` (NIfTI burned-in OCR now in scope).
- Update `docs/roadmap.md` (mark the two deferred items done; note CT-deface + all-plane OCR as
  explicit future scope).

## Out of scope (explicit)
- CT defacing (flagged for manual handling instead).
- All-three-orthogonal-plane NIfTI OCR (axial-only this round).
- Any change to the DICOM pixel-redaction core or the reversibility/keystore model.
