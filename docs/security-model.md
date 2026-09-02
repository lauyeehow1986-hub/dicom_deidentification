# Security & privacy model

## Principles
- **Offline only.** No network calls at de-identification time. The app is designed to run on an
  air-gapped machine.
- **No real data in git.** Only programmatically-generated synthetic fixtures are committed; all
  real-data paths are gitignored.
- **Least linkage.** De-identified outputs contain no direct identifiers and no original UIDs; any
  re-identification capability lives only in the encrypted keystore.

## Reversibility & the keystore
- **Reversible mode (default):** an encrypted keystore holds the `orig <-> pseudonym` crosswalk,
  per-patient salts, and per-patient date offsets. Encryption reuses the AEAD + Argon2 pattern from
  the author's `shinyEncrypt` work. The keystore is stored **outside** the repo and bundle.
- **Irreversible mode:** salts are generated per run and never persisted; no keystore is written.
  Pseudonyms remain internally consistent within the run but cannot be reversed.

## Pseudonymisation
- Direct-ID values (MRN, case/accession, visit, national-ID) -> **salted SHA-256** (deterministic,
  so the same value maps to the same pseudonym across a run/patient).
- UIDs -> **deterministic remap** (referential integrity preserved; link to originals broken).
- Dates -> **per-patient offset** preserving intervals (or removed, per profile).

## Roles & audit (Phase 6)
- Two roles: **de-identifier** and **reviewer**. The person who de-identifies a study **cannot**
  sign it off — a reviewer must approve.
- All de-identification runs and reviewer sign-offs are written to an **append-only, hash-chained
  audit log**.

## Residual-risk QA (Phase 6)
- Every output is re-scanned by all detectors; any residual identifier is reported per category.
- Acceptance bar: the planted-PHI synthetic corpus must yield **zero** residual identifiers.
