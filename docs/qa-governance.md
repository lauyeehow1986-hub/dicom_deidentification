# QA & governance (Phase 6)

De-identification is not trustworthy until someone can *check* it and *sign for*
it. Phase 6 adds three things: an automated residual-PHI scan on the outputs, a
two-role sign-off gate, and a tamper-evident audit log.

## Residual-PHI scan

`scan_residual` (engine, `deid_engine.core`) re-runs the detectors on an
already-de-identified file and reports what survived. It is deliberately
**independent** of the de-id run:

- It builds the layered text scanner from the profile's `text_detection` block
  but with an **empty header-token layer**, so the pseudonyms already written
  into the output header (`ANON^…`, the salted `PatientID`) are *not* themselves
  counted as PHI.
- The gazetteer + SG recognisers (NRIC/FIN, phone, email) + optional
  Presidio/NER layers then catch any *real* identifier that survived, in every
  text VR (recursing into sequences / SR `ContentSequence`) and, when Tesseract
  is present, in the pixels via OCR.
- The de-identification provenance fields the pipeline writes itself
  (`PatientIdentityRemoved`, `DeidentificationMethod`, …) are skipped — otherwise
  Presidio mistakes the method string for a person name.

Each finding is returned with a **masked preview** (`S•••••••D`), so the QA
report can locate a leak without re-disclosing the identifier in clear text. A
file `passed` when nothing scored at or above `min_score` (default 0.5).
`scan_residual_dir` aggregates a whole output tree into a batch verdict.

The R side (`R/qa.R`) turns per-file reports into the dashboard tables
(`qa_files_df`, `qa_findings_df`) and a batch rollup (`residual_scan_paths`,
with an injectable `scan_fn` so the aggregation is unit-tested without a venv).
A scan can also annotate the Phase 5 manifest: `set_residual` moves any output
row with residual PHI to the `flagged` status the manifest already reserved.

## Two-role sign-off gate

Two roles (`auth.R`): **de-identifier** and **reviewer**. The rule is
segregation of duties — *the person who de-identified a batch can never approve
it*. `can_sign_off(deidentifier, reviewer, reviewer_role)` enforces it: the
signer must hold the reviewer role, must be named, and must not be the batch's
de-identifier (read from the manifest's `created_by`). `record_signoff` runs the
gate and, only on success, writes the sign-off to the audit log — a refused
sign-off writes nothing.

A light salted-hash credential store (`make_user` / `verify_user`) separates the
two roles without a network dependency; `shinymanager` is not available on the
air-gapped box. Harden with an OS/AD login at deployment if required.

## Audit log (append-only, hash-chained)

Every de-identification run, residual scan, and sign-off is appended as one JSON
line under `workspace/audit/audit_log.jsonl` (`audit.R`). Each entry carries the
previous entry's hash, and its own SHA-256 covers that link, so any edit,
reorder, or deletion of an earlier line breaks the chain. `audit_verify` walks
the chain and reports the first broken entry. Tamper-**evident**, not
tamper-proof: the point is that quiet edits cannot pass unnoticed during
governance review. The Audit tab shows the log, verifies integrity, and exports
CSV.

## The app

The **QA / Review** tab (`R/mod_qa.R`) scans an output folder or a registered
batch in a background `callr` process (Shiny never blocks), shows a PASS/FLAG
verdict with per-category counts, a per-file table, and a masked findings table,
then offers the gated reviewer sign-off. The navbar "acting as" control sets the
current role and the audit actor. The **Audit** tab (`R/mod_audit.R`) is the
governance viewer.

## Verification

- Engine (`tests/python/test_residual.py`): clean output passes; leaked
  NRIC/phone/name in text or nested sequences is flagged; pseudonymised headers
  and our own provenance fields are not flagged; previews are masked; the pixel
  scan degrades without Tesseract.
- R (`tests/testthat/test-qa.R`): the hash chain (append/read/verify + tamper
  detection), the sign-off gate (self-approval and non-reviewer refused, a
  distinct reviewer audited), the credential store, residual aggregation, masked
  findings, and manifest flagging.
- End-to-end on the open-source `image-00000.dcm`: its de-identified output
  scans **PASS**; a copy with an injected NRIC + phone scans **FLAG** (2
  findings, masked); driven both from a script and live through the QA tab, with
  the run recorded in the hash-chained audit log.
