# One-time setup: Python 3.12 venv in .venv with WoWBox and its dependencies.
#   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv python install 3.12
    uv venv .venv --python 3.12
    uv pip install --python .venv\Scripts\python.exe -e ".[dev,dxcam]"
} else {
    py -3.12 -m venv .venv
    .venv\Scripts\python.exe -m pip install -e ".[dev,dxcam]"
}
if (-not (Test-Path config\team.yaml)) { Copy-Item config\team.example.yaml config\team.yaml }
Write-Host "Done. Edit config\team.yaml, then run: .\wowbox.bat"
