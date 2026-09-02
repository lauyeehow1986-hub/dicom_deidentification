# Roadmap

Phased delivery. Each phase is independently testable; the planted-PHI synthetic corpus is the
running acceptance test from Phase 1 onward.

| Phase | Theme | Key deliverables |
|------|-------|------------------|
| **0** | Reset & scaffold | Standalone repo; R app shell (all tabs); identifier catalog + default profile; engine bridge + relocatable `uv` venv build script; docs; test scaffold. **(done)** |
| **1** | Metadata de-id core | pydicom rules engine applying PS3.15 action codes recursing into SQ; private-tag policy; salted-SHA-256 pseudonymisation; UID remap; interval-preserving date-shift; encrypted keystore + crosswalk; interactive one-folder flow with before/after metadata diff. **(done)** |
| **2** | Text PHI detection | Layered scanner ([docs/text-detection.md](text-detection.md)): header-token scrub + gazetteer + SG recognisers (NRIC/FIN checksum, phone, email) always-on; Presidio + transformer NER optional/graceful. Runs over every text VR (incl. nested SR ContentSequence); encapsulated PDF removed. **(done)** |
| **3** | Pixel / burned-in PHI | Frame/RGB/multiframe decode (incl. JPEG2000/RLE); Pixels viewer with OCR-proposed + manual redaction boxes; decompress→redact→write valid uncompressed DICOM; audio/waveform strip. **(done)** — OCR degrades gracefully w/o Tesseract; optional defacing deferred. |
| **4** | Profiles & self-improvement | Rules/profile editor + per-project profiles (writable workspace overrides shipped defaults); tag-a-miss capture -> project gazetteer/custom-regex + labeled-example store; NER fine-tuning export hook. See [docs/profiles-self-improvement.md](profiles-self-improvement.md). **(done)** |
| **5** | Bulk engine | SQLite manifest job queue; mirai/future parallel workers; resumable/checkpointed; ~2 TB streaming; progress dashboard. See [docs/bulk-engine.md](bulk-engine.md). **(done)** |
| **6** | QA & governance | Automated residual-PHI scan on outputs; sampling review; pass/fail report; two-role auth (de-identifier -> reviewer) with gated sign-off + audit log. |
| **7** | Packaging & air-gap | Reproducible bundle (R library + relocatable venv + NER model + optional dcmtk); offline install doc; synthetic acceptance corpus; validation. |

## Cross-cutting invariants
- Output is **valid, viewable DICOM** (NIfTI out for NIfTI in).
- **Never** commit real data; synthetic fixtures only.
- Everything runs **offline**; no network at de-id time.
- Reversible pseudonymisation is the default; irreversible is an explicit per-run option.
