# dicom-deidentification

An **R Shiny** application for de-identifying medical images and their metadata — DICOM and
NIfTI, all cardiovascular modalities — with a configurable rule set, a self-improvement tagging
loop, a queued/resumable bulk engine, and a two-role quality-assurance workflow. Built to run
**offline on an air-gapped laptop**.

> **Not a diagnostic or clinical-decision tool.** Research/educational de-identification support.
> **Never commit real patient data or real DICOM studies** — only synthetic, planted-PHI fixtures.

📖 **[Run guide + screenshots + acceptance self-test (GitHub Pages)](https://lauyeehow1986-hub.github.io/dicom_deidentification/)**

## What it does

- **De-identifies DICOM and NIfTI** across all cardiovascular modalities — echo (ultrasound
  cine/multiframe, colour-Doppler RGB), cardiac CT/MR (3D/4D), secondary capture — including
  **compressed transfer syntaxes** (JPEG2000 lossy/lossless, JPEG-LS, RLE), and re-emits
  **valid, viewable DICOM** (NIfTI out for NIfTI in).
- Removes the **15 SingHealth direct-identifier categories** wherever they hide: nested
  **sequences**, **private/vendor** tags, **free-text** fields, **structured reports /
  encapsulated PDFs**, NIfTI header extensions + sidecars, and **burned into the pixels**.
- **Names** — the hardest identifier for Singapore's multiracial + foreigner population — are
  caught with a layered ensemble anchored on *scrubbing the real patient-name tokens* read from
  the DICOM header, backed by a gazetteer, Presidio SG recognisers, and a transformer NER.
- **Reversible pseudonymisation** by default (salted SHA-256, consistent UID remap,
  interval-preserving date-shift, encrypted keystore) with an **irreversible** option.
- **Burned-in pixel PHI**: OCR-proposed auto-redaction plus manual redaction boxes; audio/waveform
  stripping; optional FOV-gated **defacing** for head-inclusive MRI.
- **Review UI** with configurable replacement rules, a **self-improvement tagging loop**
  (tag-a-miss → project gazetteer/regex + labeled-example store), a **queued / resumable /
  parallel bulk engine** (batches up to ~2 TB), and a **two-role QA workflow**
  (de-identifier → reviewer) with automated residual scanning, pass/fail reporting, Ed25519
  output signatures, and a hash-chained audit log.

## Architecture (at a glance)

R Shiny owns the UI, orchestration, security, and QA. A **relocatable Python engine**
(`reticulate`) does the DICOM/PHI heavy lifting (pydicom + gdcm + pylibjpeg + Presidio +
presidio-image-redactor + Tesseract). The R side reaches it through a thin `engine_bridge.R`;
a SQLite manifest drives resumable bulk jobs across `mirai`/`future` workers.

See the [documentation site](https://lauyeehow1986-hub.github.io/dicom_deidentification/) or the
[`docs/`](docs/) folder — [roadmap](docs/roadmap.md), [security model](docs/security-model.md),
[PS3.15 mapping](docs/ps315-mapping.md), [text detection](docs/text-detection.md),
[air-gap install](docs/airgap-install.md) — and [`CLAUDE.md`](CLAUDE.md) for the full design.

## Status

**All phases complete** (metadata core → text PHI → pixel PHI → NIfTI hardening →
profiles/self-improvement → bulk engine → QA/governance → projects & signing → air-gap
packaging). The acceptance self-test — the whole pipeline over a synthetic, planted-PHI corpus —
comes out **PASS 6/6** (zero residual identifiers, valid re-readable output, reversible
round-trip, resumable rerun) in both reversible and irreversible mode. See the
[roadmap](docs/roadmap.md).

## Running

### Portable (recommended for a locked-down / air-gapped box — no install)

The whole app — portable R, the Python engine, the NER model, and Tesseract — ships inside one
folder built by [`tools/build_portable_bundle.ps1`](tools/build_portable_bundle.ps1). On the
target machine:

1. Copy `dicomdeid-portable` over (keep the path short, e.g. `C:\dicomdeid\`).
2. *(Optional)* double-click `verify.bat` — re-checksums every file against the shipped manifest.
3. Double-click `run.bat` (or `run.vbs` for no console window).
4. Your browser opens at `http://127.0.0.1:8973`; everything runs locally, no network.

Full offline build & packaging steps: [`docs/airgap-install.md`](docs/airgap-install.md).

### Developer mode

```r
# once, on a connected build machine — build the relocatable Python engine venv:
#   pwsh inst/python/build_venv.ps1 -Phi -Ner
# then, from the project root:
shiny::runApp(".")
```

The R side finds the engine via the `DICOMDEID_VENV` environment variable (default
`inst/python/.venv`).

## Layout

```
R/               Shiny modules (mod_*.R) + engine bridge, job manager, keystore, auth, acceptance
inst/python/     relocatable Python de-identification engine + uv build script
inst/profiles/   identifier catalog + default PS3.15 profile
inst/testdata/   synthetic planted-PHI fixtures (generated; no real data)
tests/           testthat (R) + pytest (engine)
tools/           bundle builders (relocatable air-gap package + on-target verifier)
docs/            documentation site (GitHub Pages) + design docs; air-gap install, security,
                 PS3.15 mapping, text/pixel detection, QA/governance, projects & signing, roadmap
app.R            entrypoint
```

## Verification

Acceptance test = the **planted-PHI synthetic corpus** (`deid_engine.corpus`, all encodings incl.
JPEG2000 and NIfTI) must come out with **zero** residual identifiers and as valid DICOM. Run it
in-app on the **Validation** tab, or via `R/acceptance.R`; the same checks back the automated
`testthat` (R) + `pytest` (engine) suites.
