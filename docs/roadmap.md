# Roadmap

Phased delivery. Each phase is independently testable; the planted-PHI synthetic corpus is the
running acceptance test from Phase 1 onward.

| Phase | Theme | Key deliverables |
|------|-------|------------------|
| **0** | Reset & scaffold | Standalone repo; R app shell (all tabs); identifier catalog + default profile; engine bridge + relocatable `uv` venv build script; docs; test scaffold. **(done)** |
| **1** | Metadata de-id core | pydicom rules engine applying PS3.15 action codes recursing into SQ; private-tag policy; salted-SHA-256 pseudonymisation; UID remap; interval-preserving date-shift; encrypted keystore + crosswalk; interactive one-folder flow with before/after metadata diff. **(done)** |
| **2** | Text PHI detection | Layered scanner ([docs/text-detection.md](text-detection.md)): header-token scrub + gazetteer + SG recognisers (NRIC/FIN checksum, phone, email) always-on; Presidio + transformer NER optional/graceful. Runs over every text VR (incl. nested SR ContentSequence); encapsulated PDF removed by default, with an opt-in `rasterize_redact` mode that renders, OCR-redacts and flattens the embedded PDF instead — see [docs/encapsulated-pdf.md](encapsulated-pdf.md). **(done)** |
| **3** | Pixel / burned-in PHI | Frame/RGB/multiframe decode (incl. JPEG2000/RLE); Pixels viewer with OCR-proposed + manual redaction boxes; decompress→redact→write valid uncompressed DICOM; audio/waveform strip. **(done)** — OCR degrades gracefully w/o Tesseract; optional defacing deferred. |
| **4** | Profiles & self-improvement | Rules/profile editor + per-project profiles (writable workspace overrides shipped defaults); tag-a-miss capture -> project gazetteer/custom-regex + labeled-example store; NER fine-tuning export hook. See [docs/profiles-self-improvement.md](profiles-self-improvement.md). **(done)** |
| **5** | Bulk engine | SQLite manifest job queue; mirai/future parallel workers; resumable/checkpointed; ~2 TB streaming; progress dashboard. See [docs/bulk-engine.md](bulk-engine.md). **(done)** |
| **6** | QA & governance | Automated residual-PHI scan on outputs; sampling review; pass/fail report; two-role auth (de-identifier -> reviewer) with gated sign-off + audit log. See [docs/qa-governance.md](qa-governance.md). **(done)** |
| **6.5** | Projects, integrity & signing | Portable project bundles (copyable settings, rebindable roots); global vs project-scoped hashing (persisted salt, reversible/irreversible); disk-swap-safe resume; before/after checksums + processing log; Ed25519 sidecar signatures; reviewer metadata view; per-project audit log. See [docs/projects-integrity-signing.md](projects-integrity-signing.md). **(done)** |
| **7** | Packaging & air-gap | Reproducible bundle builder + checksummed `BUNDLE_MANIFEST.json` + on-target verifier (`tools/build_bundle.ps1`, `R/bundle.R`); staged R app + relocatable venv + NER model + optional dcmtk + **global keystore & signing keys** (opt-in secrets); finalised offline install doc; real synthetic planted-PHI corpus (`deid_engine.corpus`, all encodings incl. JPEG2000/NIfTI); acceptance runner (`R/acceptance.R`) + in-app **Validation** tab. Surfaced+fixed two engine gaps: NIfTI inputs were skipped (now de-identified — header text scrub) and the SG phone recogniser false-flagged hashed pseudonyms (now alphanumeric-boundary-aware). See [docs/airgap-install.md](airgap-install.md). **(done)** |

## Cross-cutting invariants
- Output is **valid, viewable DICOM** (NIfTI out for NIfTI in).
- **Never** commit real data; synthetic fixtures only.
- Everything runs **offline**; no network at de-id time.
- Reversible pseudonymisation is the default; irreversible is an explicit per-run option.
