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
- `inst/models/`        — the NER model weights (gitignored; too big for git)
- the **Tesseract OCR binary** (needed by `presidio-image-redactor`) — a portable copy, no admin

### Wiring R -> Python on the target
`engine_bridge.R` finds the venv via the `DICOMDEID_VENV` environment variable (default
`inst/python/.venv`). Set it in the launcher so both portable runtimes sit in one folder:

```
DICOMDEID_VENV = <bundle>/inst/python/.venv
```

## 3. Verify on the target (offline)

```r
shiny::runApp(".")            # engine badge should read "engine: ready"
```
```powershell
$env:DICOMDEID_VENV='inst/python/.venv'
python -c "import deid_engine, json; print(json.dumps(deid_engine.engine_info()))"
```

## Notes
- This dev machine has intermittent HTTPS/cert interception; `uv` calls use `--system-certs`.
- No network is used at de-identification time; all models/binaries are local.
- **Never** place real patient data inside the bundle or the repo.
