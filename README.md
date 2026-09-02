# dicom-deidentification

An **R Shiny** application for de-identifying medical images and their metadata — built for a
research/admin workflow at scale, with a review + quality-assurance loop.

> **Not a diagnostic or clinical-decision tool.** Educational/research de-identification support.
> **Never commit real patient data or real DICOM studies** — only synthetic fixtures.

## What it does

- **De-identifies DICOM and NIfTI** across all cardiovascular modalities — echo (ultrasound
  cine/multiframe, colour-Doppler RGB), cardiac CT/MR (3D/4D), secondary capture — including
  **compressed transfer syntaxes** (JPEG2000 lossy/lossless, JPEG-LS, RLE), and re-emits
  **valid, viewable DICOM**.
- Removes the **15 SingHealth direct-identifier categories** wherever they hide: nested
  **sequences**, **private/vendor** tags, **free-text** fields, **structured reports /
  encapsulated PDFs**, and **burned into the pixels**.
- **Names** — the hardest identifier for Singapore's multiracial + foreigner population — are
  caught with a layered ensemble anchored on *scrubbing the real patient-name tokens* read from
  the DICOM header, backed by a gazetteer, Presidio recognisers, and a transformer NER.
- **Reversible pseudonymisation** by default (salted SHA-256, consistent UID remap,
  interval-preserving date-shift, encrypted keystore) with an **irreversible** option.
- **Review UI** with configurable replacement rules, a **self-improvement tagging loop**, a
  **queued/resumable/parallel bulk engine** (batches up to ~2 TB), and a **two-role QA workflow**
  (de-identifier -> reviewer) with automated residual scanning and pass/fail reporting.

## Architecture (at a glance)

R Shiny owns the UI, orchestration, security, and QA. A **relocatable Python engine**
(`reticulate`) does the DICOM/PHI heavy lifting (pydicom + gdcm + pylibjpeg + Presidio +
presidio-image-redactor). See [`docs/roadmap.md`](docs/roadmap.md) and
[`CLAUDE.md`](CLAUDE.md) for the full design.

## Status

**Phase 0 — scaffold.** Structure, identifier catalog, default profile, and engine/venv
bootstrap are in place; feature phases (metadata core -> text PHI -> pixel PHI -> profiles/
self-improvement -> bulk -> QA/governance -> air-gap packaging) follow. See the roadmap.

## Running (dev)

```r
# from the project root, once the Python engine venv is built (see docs/airgap-install.md)
shiny::runApp(".")
```

## Layout

```
R/               Shiny modules + engine bridge, job manager, auth
inst/python/     relocatable Python de-identification engine + uv build script
inst/profiles/   identifier catalog + default PS3.15 profile
inst/testdata/   synthetic planted-PHI fixtures (generated; no real data)
tests/           testthat (R) + pytest (engine)
docs/            air-gap install, security model, PS3.15 mapping, roadmap
```
