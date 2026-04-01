param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPath = Join-Path $ProjectRoot '.venv-release'
$Py = Join-Path $VenvPath 'Scripts\python.exe'
$IconPng = Join-Path $ProjectRoot 'icon\app-icon.png'
$IconIco = Join-Path $ProjectRoot 'icon\app-icon.ico'

if ($Clean) {
    Remove-Item -Recurse -Force dist, build, .venv-release -ErrorAction SilentlyContinue
}

if (!(Test-Path $Py)) {
    python -m venv $VenvPath
}

& $Py -m pip install --upgrade pip
& $Py -m pip install -r requirements.txt pyinstaller

if (Test-Path $IconPng) {
@"
from pathlib import Path
from PIL import Image

png_path = Path(r"$IconPng")
ico_path = Path(r"$IconIco")
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

with Image.open(png_path) as image:
    image.save(ico_path, format="ICO", sizes=sizes)
"@ | & $Py -
}

& $Py -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name WordPack `
    --icon "icon/app-icon.ico" `
    --add-data "data/offline_dict.json;data" `
    --add-data "icon;icon" `
    --add-data "src/uia_capture.ps1;src" `
    --collect-all rapidocr `
    --collect-all onnxruntime `
    --hidden-import argostranslate `
    --hidden-import argostranslate.package `
    --hidden-import argostranslate.translate `
    app.py

Write-Host "Build done. Output: dist/WordPack" -ForegroundColor Green

