# Air-gapped installation & packaging

The target is a **locked-down, air-gapped work laptop**: no IT-admin installs, no internet.
Everything is built on a connected dev machine, then **moved as folders** (no installer).

The app has **two runtimes**: R/Shiny (UI + orchestration) and a **Python engine** (DICOM/PHI).
The bundle must carry both.

## 1. The R/Shiny half — `shiny_alcatraz`

Use **[`shiny_alcatraz`](https://github.com/lauyeehow1986-hub/shiny_alcatraz)** `build_portable()`
to bundle **portable R + all R packages + the app** into a copy-over, double-click, zero-admin
folder. This is the recommended path and matches the constraint exactly.

```r
# on the connected build machine
shinyalcatraz::build_portable(app_dir = ".", out = "dist/dicomdeid-portable")
```

- **Do NOT use `build_wasm()`** — the DICOM decoders and the Python engine cannot run in browser
  WebAssembly.
- `build_tauri()` is an option if you want a single `.exe` shell instead of a folder.

### The gap alcatraz does not fill
`shiny_alcatraz` is **R-only** — it does not package Python / reticulate. So it bundles the R side;
the Python engine is carried separately (next section) and wired via an env var.

## 2. The Python engine half — relocatable `uv` venv

Build the relocatable venv on the connected machine and copy it into the bundle:

```powershell
pwsh inst/python/build_venv.ps1 -Phi -Ner     # base + Presidio/OCR + transformer NER
# produces inst/python/.venv  (relocatable)
```

Then place, inside the distributed folder:
- `inst/python/.venv/`  — the relocatable venv
- `inst/models/<name>/` — the transformer-NER model **directory** (gitignored; too big for
  git). Point `text_detection.ner_model` at this path in the profile. The engine loads it
  **local-only** and never reaches the network, so a missing/misconfigured path just
  disables the NER layer (the deterministic layers still run) — see
  [text-detection.md](text-detection.md).
- a **spaCy model** installed into the venv (e.g. `en_core_web_lg`) so the Presidio layer
  can build; without it, Presidio is skipped and the deterministic layers carry text detection.
- the **Tesseract OCR binary** (needed by `presidio-image-redactor`) — a portable copy, no admin

### Wiring R -> Python on the target
`engine_bridge.R` finds the venv via the `DICOMDEID_VENV` environment variable (default
`inst/python/.venv`). Set it in the launcher so both portable runtimes sit in one folder:

```
DICOMDEID_VENV = <bundle>/inst/python/.venv
```

## 3. Assemble the reproducible bundle — `tools/build_bundle.ps1`

Once both runtimes exist (sections 1–2), stage everything into one copy-over folder and
**checksum every file** into `BUNDLE_MANIFEST.json`:

```powershell
pwsh tools/build_bundle.ps1 -Out dist/dicomdeid-portable
# add the NER model + secrets:
pwsh tools/build_bundle.ps1 -Out dist/dicomdeid-portable -Models inst/models -Secrets
```

It stages the R app, `inst/python` (the relocatable venv, caches pruned), profiles, gazetteers,
the corpus generator, and — with `-Models`/`-Dcmtk` — the optional model/binaries, then writes a
launcher (`run_app.ps1`, which sets `DICOMDEID_VENV`) and the manifest. It prints the component
checklist (`bundle_components()`); required parts missing from the staged tree are called out.

### Secrets in the bundle (`-Secrets`)
The **global keystore** (`workspace/keystores/global.json`) and the **Ed25519 signing keys**
(`workspace/signing/`) are what make pseudonyms link across projects/machines and let a
reviewer trust a de-identified study's signature. (`build_bundle.ps1` reads them from the
workspace — override with `-Workspace <path>` — and stages them into the bundle's `workspace/`.) They are **secrets** — they can re-link patients
and sign as the pipeline — so they are **git-ignored and never committed**. `-Secrets` copies them
into the bundle; without it the bundle ships with neither (a new machine then starts its own global
salt/keys, breaking cross-machine linkage). Move a `-Secrets` bundle only over a trusted channel,
and keep the private signing key off any shared drive. The signing **public** key can travel
freely so anyone can verify outputs.

## 4. Verify + validate on the target (offline)

**Bundle integrity** — confirm the copy is complete and untampered:
```powershell
pwsh tools/build_bundle.ps1 -Out dist/dicomdeid-portable -Verify   # re-checksums vs the manifest
```

**Engine + app** — the badge should read “engine: ready”:
```r
shiny::runApp(".")
```
```powershell
$env:DICOMDEID_VENV='inst/python/.venv'
python -c "import deid_engine, json; print(json.dumps(deid_engine.engine_info()))"
```

**Acceptance self-test** — proves the whole pipeline on synthetic *planted-PHI* data with no real
studies: it builds the corpus, de-identifies it, and checks that **nothing planted survives**
(metadata + pixels), outputs stay valid DICOM/NIfTI, the reversibility policy holds, and a rerun
reprocesses nothing. Run it from the **Validation** tab in the app, or headless:
```r
res <- acceptance_run(mode = "reversible");  cat(res$markdown)   # then mode = "irreversible"
```
Both the Validation tab and `bundle_verify()` are pure R, so they work on the locked-down box
without the shell. The synthetic corpus itself is regenerated by
`inst/testdata/generate_synthetic.py` (a thin CLI over `deid_engine.corpus.build_corpus`).

## Notes
- This dev machine has intermittent HTTPS/cert interception; `uv` calls use `--system-certs`.
- No network is used at de-identification time; all models/binaries are local.
- **Never** place real patient data inside the bundle or the repo; the corpus is 100% synthetic.
