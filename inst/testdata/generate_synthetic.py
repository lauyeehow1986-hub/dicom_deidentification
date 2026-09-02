"""Generate synthetic DICOM/NIfTI fixtures with PLANTED PHI (scaffold stub).

Run (once the base engine venv exists):
    python inst/testdata/generate_synthetic.py --out inst/testdata/synthetic

Phase 0 defines the planted-PHI manifest below (the ground truth the QA residual
scan checks against). The actual writers (single/multiframe/RGB/JPEG2000/NIfTI)
are filled in alongside Phase 1/3 so they exercise the real read/write paths.
"""

from __future__ import annotations
import argparse
import json
import os

# Ground-truth identifiers planted into the fixtures. Keep in sync with the QA scan.
PLANTED = {
    "names": [
        "Nurul Aisyah Binte Rahman",   # Malay
        "Tan Wei Ming",                 # Chinese
        "Ramasamy Muthu",               # Indian
        "Bernadette Pereira",           # Eurasian
        "Michael O'Sullivan",           # Western/foreigner
    ],
    "nric_fin": ["S1234567D", "G9876543N"],
    "passport": ["E12345678"],
    "phone": ["+65 9123 4567", "62345678"],
    "email": ["patient@example.sg"],
    "mrn": ["MRN0099887"],
    "accession": ["ACC-2024-000123"],
    "address": ["Blk 123 Bishan St 12 #08-45", "Singapore 570123"],
    "locations": {
        "structured_tags": True,
        "free_text": True,
        "burned_in_pixels": True,
        "private_tags": True,
        "nested_sequence": True,
    },
    "encodings": ["single_frame", "multiframe_cine", "rgb", "jpeg2000", "nifti"],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="inst/testdata/synthetic")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "planted_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(PLANTED, fh, indent=2)
    print(f"Wrote planted-PHI manifest to {args.out}/planted_manifest.json")
    print("TODO(Phase 1/3): emit the actual DICOM/NIfTI fixtures with these values.")


if __name__ == "__main__":
    main()
