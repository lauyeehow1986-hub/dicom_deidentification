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
