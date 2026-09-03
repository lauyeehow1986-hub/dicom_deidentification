<#
Assemble a reproducible, copy-over air-gap bundle of the DICOM de-identification
app, and checksum every file into BUNDLE_MANIFEST.json (verify it on the target
with -Verify). Runtimes are built beforehand:
  * the R side by shiny_alcatraz build_portable() (portable R + packages + app),
  * the Python engine by inst/python/build_venv.ps1 (relocatable uv venv).

This script STAGES those plus the profiles/gazetteers/models and, only when you
pass -Secrets, the global keystore + Ed25519 signing keys (which link patients /
can sign as the pipeline - never commit them). See docs/airgap-install.md.

  pwsh tools/build_bundle.ps1 -Out dist/dicomdeid-portable
  pwsh tools/build_bundle.ps1 -Out dist/dicomdeid-portable -Models inst/models -Secrets
  pwsh tools/build_bundle.ps1 -Out dist/dicomdeid-portable -Verify
#>
param(
    [Parameter(Mandatory = $true)] [string]$Out,
    [string]$Models,
    [string]$Dcmtk,
    [string]$Workspace = "workspace",
    [switch]$Secrets,
    [switch]$Verify
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$rscript = (Get-Command Rscript -ErrorAction SilentlyContinue).Source
if (-not $rscript) {
    $cand = Get-ChildItem "C:\Program Files\R\*\bin\Rscript.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
    if ($cand) { $rscript = $cand.FullName }
}
if (-not $rscript) { throw "Rscript not found; install R or add it to PATH." }

function Invoke-BundleR($expr) {
    $src = "for (f in list.files(file.path('$($repo -replace '\\','/')','R'), pattern='[.]R`$', full.names=TRUE)) source(f); $expr"
    & $rscript -e $src
    if ($LASTEXITCODE -ne 0) { throw "Rscript failed: $expr" }
}

if ($Verify) {
    $mp = Join-Path $Out "BUNDLE_MANIFEST.json"
    if (-not (Test-Path $mp)) { throw "No BUNDLE_MANIFEST.json in $Out" }
    Write-Host "Verifying $Out against its manifest..."
    # Call Rscript directly (not the throwing wrapper) so a failed verify just
    # propagates exit code 1 rather than raising a confusing error.
    $expr = "for (f in list.files(file.path('$($repo -replace '\\','/')','R'), pattern='[.]R`$', full.names=TRUE)) source(f); v <- bundle_verify('$($Out -replace '\\','/')','$($mp -replace '\\','/')'); cat(sprintf('ok=%s checked=%d missing=%d changed=%d extra=%d\n', v`$ok, v`$n_checked, length(v`$missing), length(v`$changed), length(v`$extra))); if (!v`$ok) { print(v[c('missing','changed','extra')]); quit(status=1) }"
    & $rscript -e $expr
    exit $LASTEXITCODE
}

# --- stage ---------------------------------------------------------------
Write-Host "Staging bundle into $Out"
if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# small files/dirs copied directly
$direct = @("app.R", "DESCRIPTION", "LICENSE", "README.md", "CLAUDE.md", "docs")
foreach ($item in $direct) {
    $s = Join-Path $repo $item
    if (Test-Path $s) { Copy-Item $s (Join-Path $Out $item) -Recurse -Force }
}
Copy-Item (Join-Path $repo "R") (Join-Path $Out "R") -Recurse -Force

# inst subtrees (robocopy for the big venv; /XD prunes caches). robocopy exit
# codes < 8 are success.
function Robo($src, $dst) {
    if (-not (Test-Path $src)) { Write-Host "  (skip, absent) $src"; return }
    robocopy $src $dst /E /XD "__pycache__" ".git" /XF "*.pyc" /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $src ($LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}
Robo (Join-Path $repo "inst\python")     (Join-Path $Out "inst\python")
Robo (Join-Path $repo "inst\profiles")   (Join-Path $Out "inst\profiles")
Robo (Join-Path $repo "inst\gazetteers") (Join-Path $Out "inst\gazetteers")
Robo (Join-Path $repo "inst\testdata")   (Join-Path $Out "inst\testdata")

if (-not (Test-Path (Join-Path $Out "inst\python\.venv"))) {
    Write-Warning "No relocatable venv staged - run inst/python/build_venv.ps1 first."
}

if ($Models) { Robo (Resolve-Path $Models) (Join-Path $Out "inst\models") }
if ($Dcmtk)  { Robo (Resolve-Path $Dcmtk)  (Join-Path $Out "bin\dcmtk") }

# The global keystore + signing keys live under the workspace (keystores/, signing/).
# Stage them into the bundle's workspace/ so the app finds them by default on the target.
if ($Secrets) {
    Write-Warning "Including SECRETS (global keystore + signing keys). Handle the bundle accordingly."
    $wsAbs = if ([System.IO.Path]::IsPathRooted($Workspace)) { $Workspace } else { Join-Path $repo $Workspace }
    foreach ($sec in @("keystores", "signing")) {
        $s = Join-Path $wsAbs $sec
        if (Test-Path $s) { Robo $s (Join-Path $Out (Join-Path "workspace" $sec)) }
        else { Write-Host "  (no $sec/ under $wsAbs yet)" }
    }
} else {
    Write-Host "Secrets NOT included (pass -Secrets to add the global keystore + signing keys)."
}

# launcher: point reticulate at the bundled venv, then run the app
$launcher = @'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:DICOMDEID_VENV = Join-Path $here "inst\python\.venv"
$env:KMP_DUPLICATE_LIB_OK = "TRUE"
Rscript -e "shiny::runApp('.', launch.browser = TRUE)"
'@
Set-Content -Path (Join-Path $Out "run_app.ps1") -Value $launcher -Encoding utf8

# --- manifest ------------------------------------------------------------
Write-Host "Writing BUNDLE_MANIFEST.json"
$secR = if ($Secrets) { "TRUE" } else { "FALSE" }
Invoke-BundleR "bundle_write_manifest('$($Out -replace '\\','/')', meta=list(built_by='build_bundle.ps1', secrets_included=$secR))"

Write-Host ""
Write-Host "Bundle components (required / secret):"
Invoke-BundleR "c <- bundle_components(); for (i in seq_len(nrow(c))) cat(sprintf('  [%s%s] %s\n', ifelse(c`$required[i],'R',' '), ifelse(c`$secret[i],'S',' '), c`$component[i]))"
Write-Host ""
Write-Host "Done -> $Out   (verify on target: pwsh tools/build_bundle.ps1 -Out '$Out' -Verify)"
