$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$VenvPython = Join-Path $ProjectRoot ".venv/Scripts/python.exe"

if (-not (Test-Path $VenvPython)) {
  throw "The isolated environment is missing. Run scripts/setup.ps1 first."
}

& $VenvPython -m app.launcher
