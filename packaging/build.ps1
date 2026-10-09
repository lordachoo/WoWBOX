# Builds dist\WoWBox\wowbox.exe (single file) plus config\ and addon\ next to it.
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. Run setup.ps1 first." }

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name wowbox `
    --paths src --distpath dist\WoWBox --workpath build\pyinstaller --specpath build `
    --exclude-module dxcam --exclude-module cv2 --exclude-module tkinter `
    packaging\wowbox_entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$out = Join-Path $root "dist\WoWBox"
New-Item -ItemType Directory -Force (Join-Path $out "config") | Out-Null
Copy-Item config\team.example.yaml (Join-Path $out "config\team.example.yaml") -Force
Copy-Item addon (Join-Path $out "addon") -Recurse -Force
Copy-Item README.md $out -Force
Write-Host "Built $out\wowbox.exe"
