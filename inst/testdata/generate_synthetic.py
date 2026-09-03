"""Generate the synthetic DICOM/NIfTI acceptance corpus with PLANTED PHI.

Thin CLI over ``deid_engine.corpus.build_corpus`` (the tested implementation).

    python inst/testdata/generate_synthetic.py --out inst/testdata/synthetic

Emits real fixtures (single-frame, multiframe/cine, RGB, JPEG2000, NIfTI) with
known identifiers planted in structured tags, free-text, private tags, nested
sequences, and burned into the pixels, plus ``planted_manifest.json`` (the
ground truth the QA residual scan checks against). No real patient data.
"""
from __future__ import annotations

import argparse

from deid_engine import corpus


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="inst/testdata/synthetic")
    args = ap.parse_args()
    man = corpus.build_corpus(args.out)
    print(f"Wrote {len(man['fixtures'])} fixtures + planted_manifest.json to {args.out}")
    for f in man["fixtures"]:
        flag = " [burned-in]" if f.get("burned_in") else ""
        print(f"  - {f['rel']:20s} {f['encoding']}{flag}")


if __name__ == "__main__":
    main()
