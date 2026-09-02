# Build a RELOCATABLE Python venv for the de-identification engine, using uv.
#
# Design goals:
#   * no IT-admin install (uv + a user-space Python; venv is copy-movable),
#   * air-gappable: build once on a connected machine, then move inst/python/.venv
#     (plus inst/models/) onto the target box; point DICOMDEID_VENV at it.
#
# This machine has intermittent HTTPS/cert interception -> uv needs --system-certs
# (matches reference_uv_setup.md).
#
# Usage:
#   pwsh inst/python/build_venv.ps1            # base engine (DICOM I/O)
#   pwsh inst/python/build_venv.ps1 -Phi       # + Presidio + OCR
#   pwsh inst/python/build_venv.ps1 -Phi -Ner  # + transformer NER (CPU torch)

param(
    [switch]$Phi,
    [switch]$Ner
)

$ErrorActionPreference = "Stop"
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv  = Join-Path $here ".venv"

Write-Host "Creating relocatable venv at $venv"
uv venv --system-certs --relocatable $venv

$extras = @()
if ($Phi) { $extras += "phi" }
if ($Ner) { $extras += "ner" }

$target = $here
if ($extras.Count -gt 0) {
    $spec = "$here" + "[" + ($extras -join ",") + "]"
    Write-Host "Installing deid-engine with extras: $($extras -join ', ')"
    uv pip install --python $venv --system-certs -e $spec
} else {
    Write-Host "Installing base deid-engine"
    uv pip install --python $venv --system-certs -e $here
}

Write-Host ""
Write-Host "Done. Verify with:"
Write-Host "  `$env:DICOMDEID_VENV='$venv'; python -c 'import deid_engine, json; print(json.dumps(deid_engine.engine_info()))'"
Write-Host "For the air-gapped box: move inst/python/.venv (+ inst/models/) and set DICOMDEID_VENV."
