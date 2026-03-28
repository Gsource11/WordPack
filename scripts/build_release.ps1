param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPath = Join-Path $ProjectRoot '.venv-release'
$Py = Join-Path $VenvPath 'Scripts\python.exe'

if ($Clean) {
    Remove-Item -Recurse -Force dist, build, .venv-release -ErrorAction SilentlyContinue
}

if (!(Test-Path $Py)) {
    python -m venv $VenvPath
}

& $Py -m pip install --upgrade pip
& $Py -m pip install -r requirements.txt pyinstaller

& $Py -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name WordPack `
    --add-data "data/offline_dict.json;data" `
    --hidden-import argostranslate `
    --hidden-import argostranslate.package `
    --hidden-import argostranslate.translate `
    app.py

Write-Host "Build done. Output: dist/WordPack" -ForegroundColor Green

