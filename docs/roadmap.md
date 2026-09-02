# Roadmap

Phased delivery. Each phase is independently testable; the planted-PHI synthetic corpus is the
running acceptance test from Phase 1 onward.

| Phase | Theme | Key deliverables |
|------|-------|------------------|
| **0** | Reset & scaffold | Standalone repo; R app shell (all tabs); identifier catalog + default profile; engine bridge + relocatable `uv` venv build script; docs; test scaffold. **(done)** |
| **1** | Metadata de-id core | pydicom rules engine applying PS3.15 action codes recursing into SQ; private-tag policy; salted-SHA-256 pseudonymisation; UID remap; interval-preserving date-shift; encrypted keystore + crosswalk; interactive one-folder flow with before/after metadata diff. |
| **2** | Text PHI detection | Presidio + SG custom recognisers (NRIC/FIN checksum, postal/phone/email/passport) + gazetteer + header-token scrub + transformer NER over free-text/private tags, SR, encapsulated PDF. |
| **3** | Pixel / burned-in PHI | Frame/cine/RGB viewer; OCR+NER auto-redaction + manual boxes; re-encode valid DICOM incl. compressed (JPEG2000); audio/waveform strip; optional defacing. |
| **4** | Profiles & self-improvement | Rules/profile editor; per-project profiles; tag-a-miss capture -> gazetteer/regex + labeled-example store; NER fine-tuning hook. |
| **5** | Bulk engine | SQLite manifest job queue; mirai/future parallel workers; resumable/checkpointed; ~2 TB streaming; progress dashboard. |
| **6** | QA & governance | Automated residual-PHI scan on outputs; sampling review; pass/fail report; two-role auth (de-identifier -> reviewer) with gated sign-off + audit log. |
| **7** | Packaging & air-gap | Reproducible bundle (R library + relocatable venv + NER model + optional dcmtk); offline install doc; synthetic acceptance corpus; validation. |

## Cross-cutting invariants
- Output is **valid, viewable DICOM** (NIfTI out for NIfTI in).
- **Never** commit real data; synthetic fixtures only.
- Everything runs **offline**; no network at de-id time.
- Reversible pseudonymisation is the default; irreversible is an explicit per-run option.
