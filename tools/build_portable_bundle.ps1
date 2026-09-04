<#
Build the FULL air-gap copy-over bundle on a connected machine, then move the
whole folder to the locked-down box - no install, no admin, no network there.

Unlike tools/build_bundle.ps1 (which assumes R is already on the target), this
composes a *portable R* runtime in, so the target needs nothing pre-installed:

  1. shinyalcatraz::build_portable() -> portable R + the app's R packages + the
     app source, copied under <Out>/app, with a run.bat launcher.
  2. The relocatable Python engine venv (inst/python, incl. .venv) is copied in.
  3. The transformer NER model (inst/models) is copied in.
  4. The portable Tesseract OCR runtime (vendor/tesseract) is copied to app/bin.
  5. The Ed25519 signing keys (workspace/signing) are copied in unless
     -SkipSecrets. (There is NO pre-made global keystore: set its passphrase on
     the target on first run - see docs/airgap-install.md.)
  6. run_app.R is patched to point the engine at all of the above (venv, engine
     sys.path, Tesseract binary, workspace) via env vars the launcher inherits.
  7. BUNDLE_MANIFEST.json checksums every file so the target can verify the copy
     (tools/build_bundle.ps1 -Out <Out> -Verify).

  pwsh tools/build_portable_bundle.ps1
  pwsh tools/build_portable_bundle.ps1 -Out dist/dicomdeid-portable -RVersion 4.5.1 -Snapshot 2026-09-01
  pwsh tools/build_portable_bundle.ps1 -SkipSecrets
#>
param(
    [string]$Out = "dist/dicomdeid-portable",
    [string]$Tesseract = "vendor/tesseract",
    [string]$Workspace = "workspace",
    [switch]$SkipSecrets,
    [string]$RVersion,
    [string]$Snapshot
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$rscript = (Get-Command Rscript -ErrorAction SilentlyContinue).Source
if (-not $rscript) {
    $cand = Get-ChildItem "C:\Program Files\R\*\bin\Rscript.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
    if ($cand) { $rscript = $cand.FullName }
}
if (-not $rscript) { throw "Rscript not found; install R or add it to PATH." }

function Robo($src, $dst, [string[]]$xd = @()) {
    if (-not (Test-Path $src)) { Write-Host "  (skip, absent) $src"; return }
    $args = @($src, $dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    $xd = @("__pycache__", ".git") + $xd
    foreach ($d in $xd) { $args += @("/XD", $d) }
    $args += @("/XF", "*.pyc")
    robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $src ($LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}

# Remove a tree that may contain paths past Windows MAX_PATH (260). A previous
# bundle's torch venv nests wheel-license dirs deep enough that Remove-Item
# -Recurse dies with "Could not find a part of the path". robocopy mirrors an
# empty dir over the target (it handles long paths natively), emptying it, then
# the now-empty root is removed.
function Remove-Tree($p) {
    if (-not (Test-Path $p)) { return }
    $empty = Join-Path $env:TEMP ("empty_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $empty | Out-Null
    robocopy $empty $p /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy /MIR failed clearing $p ($LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
    Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $empty -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $p) { cmd /c rmdir /s /q "$p" 2>$null }
}

$OutApp = Join-Path $Out "app"
$AppSrc = Join-Path $repo "dist\_appsrc"

# --- 1. stage a CLEAN minimal app tree (no .git/.claude/tests/venv/model) -----
Write-Host "== Staging clean app source into $AppSrc"
foreach ($p in @($Out, $AppSrc)) { Remove-Tree $p }
New-Item -ItemType Directory -Force -Path $AppSrc | Out-Null
foreach ($item in @("app.R", "DESCRIPTION", "LICENSE", "README.md", "CLAUDE.md")) {
    $s = Join-Path $repo $item
    if (Test-Path $s) { Copy-Item $s (Join-Path $AppSrc $item) -Force }
}
Copy-Item (Join-Path $repo "R")   (Join-Path $AppSrc "R")   -Recurse -Force
Copy-Item (Join-Path $repo "docs") (Join-Path $AppSrc "docs") -Recurse -Force
Robo (Join-Path $repo "inst\profiles")   (Join-Path $AppSrc "inst\profiles")
Robo (Join-Path $repo "inst\gazetteers") (Join-Path $AppSrc "inst\gazetteers")
Robo (Join-Path $repo "inst\testdata")   (Join-Path $AppSrc "inst\testdata")

# --- 2. portable R + app packages via shiny_alcatraz -------------------------
Write-Host "== build_portable (portable R + app R packages). This downloads from CRAN..."
$pkgs = "'shiny','bslib','reticulate','DBI','RSQLite','yaml','jsonlite','digest','openssl','mirai','callr'"
$rv = if ($RVersion) { ", r_portable_version='$RVersion'" } else { "" }
$sn = if ($Snapshot) { ", snapshot='$Snapshot'" } else { "" }
$expr = "shinyalcatraz::build_portable(app_dir='$($AppSrc -replace '\\','/')', out_dir='$($Out -replace '\\','/')', platform='windows', packages=c($pkgs), runtimes='none'$rv$sn)"
& $rscript -e $expr
if ($LASTEXITCODE -ne 0) { throw "build_portable failed (exit $LASTEXITCODE)." }
if (-not (Test-Path (Join-Path $Out "run_app.R"))) { throw "build_portable produced no run_app.R." }

# --- 3-5. compose the heavy / secret components into <Out>/app ---------------
Write-Host "== Composing engine venv, NER model, Tesseract, signing keys"
Robo (Join-Path $repo "inst\python") (Join-Path $OutApp "inst\python")
if (-not (Test-Path (Join-Path $OutApp "inst\python\.venv"))) {
    Write-Warning "No relocatable venv staged - build it first: inst/python/build_venv.ps1 -Phi -Ner"
}
Robo (Join-Path $repo "inst\models") (Join-Path $OutApp "inst\models")
if (-not (Test-Path (Join-Path $OutApp "inst\models"))) {
    Write-Warning "No NER model staged (inst/models) - the transformer NER layer will be off."
}
$tessSrc = if ([System.IO.Path]::IsPathRooted($Tesseract)) { $Tesseract } else { Join-Path $repo $Tesseract }
if (Test-Path (Join-Path $tessSrc "tesseract.exe")) {
    Robo $tessSrc (Join-Path $OutApp "bin\tesseract")
} else {
    Write-Warning "No Tesseract at $tessSrc - burned-in-pixel OCR will be off (manual redaction still works)."
}
# The relocatable engine .pth resolves deid_engine from <venv>/..; make sure the
# build-machine absolute editable pointer never travels.
$badPth = Join-Path $OutApp "inst\python\.venv\Lib\site-packages\_editable_impl_deid_engine.pth"
if (Test-Path $badPth) { Remove-Item $badPth -Force }
# build_venv.ps1 is a build-machine-only script; it has no business in a bundle
# the air-gapped target runs entirely from .bat. Drop it so the shipped tree
# carries no PowerShell entry point.
$strayBuildPs1 = Join-Path $OutApp "inst\python\build_venv.ps1"
if (Test-Path $strayBuildPs1) { Remove-Item $strayBuildPs1 -Force }

if (-not $SkipSecrets) {
    $wsAbs = if ([System.IO.Path]::IsPathRooted($Workspace)) { $Workspace } else { Join-Path $repo $Workspace }
    $sigSrc = Join-Path $wsAbs "signing"
    if (Test-Path $sigSrc) {
        Write-Warning "Including SECRETS (Ed25519 signing keys). Handle the bundle accordingly."
        Robo $sigSrc (Join-Path $OutApp "workspace\signing")
    } else { Write-Host "  (no signing keys under $wsAbs yet)" }
} else {
    Write-Host "Secrets NOT included (-SkipSecrets)."
}

# --- 6. wire the launcher at the composed components -------------------------
# run.bat cd's to <Out> then runs run_app.R, which runApp('app'); getwd() is <Out>
# here, so the engine's absolute paths are <Out>/app/... . Env vars set in R are
# inherited by the in-process (reticulate) Python interpreter.
Write-Host "== Patching run_app.R with engine env"
$runApp = Join-Path $Out "run_app.R"
$prelude = @'
local({
  app <- normalizePath(file.path(getwd(), "app"), winslash = "/", mustWork = FALSE)
  Sys.setenv(DICOMDEID_VENV      = file.path(app, "inst", "python", ".venv"))
  Sys.setenv(PYTHONPATH          = file.path(app, "inst", "python"))
  Sys.setenv(DICOMDEID_WORKSPACE = file.path(app, "workspace"))
  Sys.setenv(DICOMDEID_TESSERACT = file.path(app, "bin", "tesseract", "tesseract.exe"))
  Sys.setenv(TESSDATA_PREFIX     = file.path(app, "bin", "tesseract", "tessdata"))
  Sys.setenv(KMP_DUPLICATE_LIB_OK = "TRUE")     # torch/openmp on Windows
  Sys.setenv(TRANSFORMERS_OFFLINE = "1")
  Sys.setenv(HF_HUB_OFFLINE       = "1")
})
'@
$orig = Get-Content -Raw $runApp
# Write BOM-less UTF-8: a leading BOM can trip R's source parser on some builds.
[System.IO.File]::WriteAllText($runApp, ($prelude + "`r`n" + $orig),
    (New-Object System.Text.UTF8Encoding $false))

# --- 6b. target-side entry points (NO PowerShell on the locked-down box) ------
# The target runs entirely from Command Prompt / double-click: run.bat (from
# build_portable) launches; verify.bat re-checksums the copy with the bundle's
# OWN portable R; the acceptance self-test is the app's Validation tab (pure R).
# We emit verify.R/verify.bat/BUNDLE_README.md here so a rebuild reproduces that
# PowerShell-free experience instead of shipping only run.bat.
Write-Host "== Writing target-side verify.bat / verify.R / BUNDLE_README.md"
function WriteText($path, $text) {
    # BOM-less UTF-8 so cmd.exe doesn't echo a stray BOM and R parses cleanly.
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $false))
}

$verifyR = @'
# Integrity self-check for the portable bundle.
# Re-checksums every shipped file against BUNDLE_MANIFEST.json using the bundle's
# own portable R (no install, no shell knowledge needed). Double-click verify.bat.
local({
  root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
  source(file.path(root, "app", "R", "bundle.R"))
  mp <- file.path(root, "BUNDLE_MANIFEST.json")
  if (!file.exists(mp)) stop("BUNDLE_MANIFEST.json not found next to verify.bat")
  cat("Verifying", root, "against the manifest - this hashes every file...\n\n")
  res <- bundle_verify(root, mp)
  cat(sprintf("Checked : %d files\n", res$n_checked))
  cat(sprintf("Missing : %d\n", length(res$missing)))
  cat(sprintf("Changed : %d\n", length(res$changed)))
  cat(sprintf("Extra   : %d\n", length(res$extra)))
  show <- function(label, v) if (length(v))
    cat(sprintf("\n%s:\n%s\n", label, paste0("  - ", utils::head(v, 25L),
                                             collapse = "\n")))
  show("MISSING (in manifest, not on disk)", res$missing)
  show("CHANGED (checksum differs)", res$changed)
  show("EXTRA (on disk, not in manifest)", res$extra)
  cat("\n", if (isTRUE(res$ok)) "RESULT: OK - the copy is intact.\n"
      else "RESULT: FAILED - the copy differs from the manifest (see above).\n",
      sep = "")
  if (!isTRUE(res$ok)) quit(status = 1L, save = "no")
})
'@
WriteText (Join-Path $Out "verify.R") $verifyR

$verifyBat = @'
@echo off
cd /d %~dp0
set R_LIBS=%~dp0library
set R_LIBS_USER=%~dp0library
echo Verifying bundle integrity against BUNDLE_MANIFEST.json ...
"%~dp0R-Portable\bin\x64\Rscript.exe" --vanilla verify.R
echo.
pause
'@
WriteText (Join-Path $Out "verify.bat") $verifyBat

$bundleReadme = @'
# DICOM De-Identification — portable air-gap bundle

**Built and validated on a connected machine. Copy the whole folder to the air-gapped laptop and run it — nothing needs to be installed there, and nothing needs PowerShell.**

Everything on the target is Command Prompt / double-click only:

- **Launch:** double-click **`run.bat`** (or `run.vbs` for no console window). It starts the app and opens your browser.
- **Verify first:** double-click **`verify.bat`** right after copying (see below).
- **Keep the install path short** on the target (e.g. `C:\dicomdeid\`) so Windows MAX_PATH never trips on deep library files.

## What's inside (self-contained)
| Component | Notes |
|---|---|
| Portable R + all R packages | no R install needed on the target |
| Python engine venv (relocatable) | pydicom/gdcm/pylibjpeg+openjpeg, nibabel, Presidio, torch, transformers, pypdfium2 |
| Transformer NER model | multilingual person/location NER |
| Tesseract OCR (portable) | burned-in-pixel + encapsulated-PDF text detection |
| Ed25519 signing keys | outputs get a verifiable `.sig.json` sidecar |

**No global keystore is bundled.** On first use, pick a passphrase in the app — that creates `workspace/keystores/global.json`. To make pseudonyms link across machines, copy that `global.json` to the other boxes and reuse the same passphrase. The passphrase is never stored in the bundle.

## Verify + self-test on the target (offline, no shell needed)
1. **Integrity** — double-click **`verify.bat`**. It re-checksums every shipped file against `BUNDLE_MANIFEST.json` using the bundle's own portable R and prints `RESULT: OK` (or lists any missing/changed/extra files). Run this right after copying, to catch a truncated or corrupted copy before any de-id.
2. **Acceptance self-test** — double-click **`run.bat`**, then in the app open the **Validation** tab → run it in **reversible** then **irreversible**. Expect **PASS 6/6** each: 0 planted PHI survives in metadata *and* pixels, outputs valid, reversibility policy holds, a rerun reprocesses nothing.

## Rebuild (on a connected machine, from the repo)
```
inst\python\build_venv.ps1 -Phi -Ner        # once: the engine venv
tools\build_portable_bundle.ps1 -Out dist\dicomdeid-portable
```
Reproducible pin: add `-RVersion 4.5.1 -Snapshot <YYYY-MM-DD>`. Omit secrets with `-SkipSecrets`. (These build steps use PowerShell, but they run on the *connected* machine, never on the locked-down target.)

> Educational/research de-identification tool — **not for diagnosis or clinical decision-making**. Never place real patient data inside the bundle or the repo.
'@
WriteText (Join-Path $Out "BUNDLE_README.md") $bundleReadme

# --- 7. checksum manifest for on-target verification -------------------------
Write-Host "== Writing BUNDLE_MANIFEST.json (checksums every file; may take a minute)"
$srcR = "for (f in list.files(file.path('$($repo -replace '\\','/')','R'), pattern='[.]R`$', full.names=TRUE)) source(f); bundle_write_manifest('$($Out -replace '\\','/')', meta=list(built_by='build_portable_bundle.ps1', portable_r=TRUE, secrets_included=$([string](-not $SkipSecrets).ToString().ToUpper())))"
& $rscript -e $srcR
if ($LASTEXITCODE -ne 0) { throw "manifest write failed." }

# tidy the staging tree
Remove-Item $AppSrc -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "== DONE -> $Out"
Write-Host "   Launch on target: double-click run.bat (or run.vbs for no console)."
Write-Host "   Verify the copy:  double-click verify.bat (uses the bundle's own portable R; no PowerShell needed on the target)."
Write-Host "   Self-test:        open the app, Validation tab -> run acceptance (both modes)."
