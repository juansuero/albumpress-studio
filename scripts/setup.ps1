$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$PythonCommand = Get-Command py -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $PythonCommand) {
  throw "Python 3.10 or newer is required. Install Python and rerun scripts/setup.ps1."
}

if (-not (Test-Path ".venv/Scripts/python.exe")) {
  & $PythonCommand.Source -m venv .venv
}

$VenvPython = Join-Path $ProjectRoot ".venv/Scripts/python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements-cpu.txt

$NpmCommand = Get-Command npm -ErrorAction SilentlyContinue
if (-not $NpmCommand) {
  throw "Node.js and npm are required. Install Node.js LTS and rerun scripts/setup.ps1."
}
& $NpmCommand.Source install --prefix frontend

Write-Host "Setup complete. Launch with scripts/launch.ps1."
