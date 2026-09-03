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

$OutApp = Join-Path $Out "app"
$AppSrc = Join-Path $repo "dist\_appsrc"

# --- 1. stage a CLEAN minimal app tree (no .git/.claude/tests/venv/model) -----
Write-Host "== Staging clean app source into $AppSrc"
foreach ($p in @($Out, $AppSrc)) { if (Test-Path $p) { Remove-Item $p -Recurse -Force } }
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
Write-Host "   Verify the copy:  pwsh tools/build_bundle.ps1 -Out '$Out' -Verify"
Write-Host "   Self-test:        open the app, Validation tab -> run acceptance (both modes)."
