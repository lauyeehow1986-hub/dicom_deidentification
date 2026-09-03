# Synthetic test data — PLANTED PHI

**No real patient data ever lives here.** Everything under `synthetic/` is generated
programmatically by `generate_synthetic.py` with *known* identifiers planted in known
locations, so the acceptance test can assert they are all gone from the output.

Planted identifiers cover:

- **Names** across the four Singapore races + foreigners (Malay, Chinese, Indian, Eurasian,
  Western/other) — in structured name tags, in free-text descriptions/comments, and **burned
  into the pixels**.
- **NRIC / FIN / passport**, phone, email — in `PatientID`, private tags, and free-text.
- **Nested sequences (SQ)** and **private/vendor tags** carrying identifiers.
- **Modalities/encodings:** single-frame, **multiframe/cine**, **RGB** (colour-Doppler-like),
  and **compressed** (JPEG2000) samples; plus a **NIfTI** variant.

The QA residual scan (Phase 6) runs against outputs and must find **zero** planted identifiers.

`generate_synthetic.py` is a thin CLI over `deid_engine.corpus.build_corpus`, which is the tested
implementation (`tests/python/test_corpus.py`). The Phase-7 acceptance runner (`R/acceptance.R`,
and the app's **Validation** tab) builds this corpus, de-identifies it, and grades the result —
including `corpus.check_survivors`, an exact-literal sweep that fails if any planted value survives
in output metadata.
