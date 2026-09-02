# CLAUDE.md — dicom-deidentification

## What this is
An **R Shiny** app for de-identifying medical images + metadata for research use. An admin
("de-identifier") self-de-identifies studies, reviews them with configurable replacement rules,
runs a **self-improvement tagging loop**, executes **bulk** de-identification, and a second role
("reviewer") drives a **quality-assurance** workflow. Educational/research tool — **not for
diagnosis or clinical decisions** (state this in-app).

## Hard requirements (what makes this non-trivial)
- PHI hides in **nested sequences**, **private/vendor tags**, **free-text**, **structured reports /
  encapsulated PDFs**, and **burned into pixels**.
- Data: **DICOM + NIfTI**, all cardiovascular modalities — echo (US cine/multiframe, colour-Doppler
  RGB), cardiac CT/MR (3D/4D), secondary capture — across **compressed transfer syntaxes**
  (JPEG2000 lossy/lossless, JPEG-LS, RLE). Output must be **valid, viewable DICOM**.
- **Names** are the hardest identifier: Singapore's four races + foreigners, frequently non-Western.
- Cover the **15 SingHealth direct-identifier categories** (see `inst/profiles/identifier_catalog.yml`).

## Architecture
R owns UI/orchestration/security/QA; **Python (reticulate) owns DICOM+PHI**.
- **UI (R, bslib):** role-gated modules — interactive de-id, rules/profile editor, tagging &
  self-improvement, bulk job manager, QA/review, keystore, audit.
- **Orchestration (R):** SQLite manifest for **resumable** jobs; `mirai`/`future`+`callr` **parallel
  workers** for ~2 TB batches; Shiny polls progress.
- **Engine (Python, relocatable `uv` venv via reticulate):** `pydicom`+`gdcm`+`pylibjpeg`
  (decode/encode incl. JPEG2000, valid DICOM); PS3.15 Annex E action codes (D/Z/X/K/C/U + H hash,
  S date-shift) recursing into SQ; private-tag policy; `presidio-analyzer` (+ SG custom recognisers)
  and `presidio-image-redactor` (+ Tesseract OCR); consistent pseudonymisation.
- **Keystore (R):** encrypted crosswalk + salts + per-patient date offsets (reuse the
  `shinyEncrypt` AEAD/Argon2 pattern). Reversible mode persists it; irreversible never writes it.
- **Profiles:** per-project YAML (per-tag actions, regex, gazetteer, redaction templates); the
  self-improvement loop appends flagged misses to the gazetteer/regex + a labeled-example store.

## Name-detection ensemble (strongest config)
1. **Header-token scrub (primary)** — read the real PatientName, hunt those tokens (+fuzzy)
   across free-text, private tags, and pixels.  2. **Gazetteer** (user's SG name dictionary).
   3. **Presidio** SG recognisers (NRIC/FIN checksum, postal/phone/email/passport).
   4. **Transformer NER** (multilingual XLM-R-class, **CPU-only**, ~2-4 GB RAM, no GPU).

## Conventions
- C#… no — this is **R**: PascalCase for exported R functions is not required; use snake_case for R,
  one Shiny module per file (`mod_*.R`), and keep Python engine code importable/pytest-able.
- Keep the Python engine a **clean library** callable from R via a thin `engine_bridge.R`; don't
  scatter reticulate calls through the modules.
- **Never commit patient data or real DICOM** — only programmatically-generated synthetic fixtures
  under `inst/testdata/synthetic/`. Real data paths are gitignored.
- No network calls at de-id time; everything runs **offline / air-gapped**.

## Environment constraints (target = air-gapped work laptop)
- No IT-admin installs. **R packages** (incl. compiled binaries) can be moved into the user library;
  the **Python engine is a relocatable `uv` venv** copied in — see `docs/airgap-install.md`.
- NER/OCR model weights are moved onto the box separately (gitignored, too big for git).
- This dev machine has intermittent HTTPS/cert interception; `uv` needs `--system-certs`.

## Build & run
```r
shiny::runApp(".")          # after the engine venv is built (docs/airgap-install.md)
```
Tests: `testthat` (R) + `pytest` (engine). Acceptance test = the **planted-PHI synthetic corpus**
must come out with **zero** residual identifiers and as valid DICOM.

## Roadmap
Phase 0 scaffold -> 1 metadata core -> 2 text PHI -> 3 pixel PHI -> 4 profiles/self-improvement ->
5 bulk engine -> 6 QA/governance -> 7 air-gap packaging. See [docs/roadmap.md](docs/roadmap.md).

## Key references
- DICOM PS3.15 Annex E (confidentiality profiles) — mapped in `docs/ps315-mapping.md`
- pydicom / gdcm / pylibjpeg ; Presidio (`presidio-analyzer`, `presidio-image-redactor`)
- RSNA CTP (conceptual reference for tag actions)

## Git / machine note
This project has its **own** standalone repo at the project root
(`github.com/lauyeehow1986-hub/dicom_deidentification`). It is deliberately **decoupled** from the
stray `C:\Users\lauye\.git` home repo that (mis)captures the other projects — do **not** repoint that
shared remote. Always confirm `git rev-parse --show-toplevel` is the project root before committing.
```
