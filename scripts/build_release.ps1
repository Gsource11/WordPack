param(
    [switch]$Clean,
    [switch]$InstallerOnly,
    [switch]$WithOfflineInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPath = Join-Path $ProjectRoot ".venv-release"
$Py = Join-Path $VenvPath "Scripts\python.exe"
$IconPng = Join-Path $ProjectRoot "icon\app-icon.png"
$IconIco = Join-Path $ProjectRoot "icon\app-icon.ico"

if ($Clean) {
    Remove-Item -Recurse -Force dist, build, .venv-release -ErrorAction SilentlyContinue
}

if (!(Test-Path $Py)) {
    python -m venv $VenvPath
}

& $Py -m pip --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning ".venv-release pip is unavailable, fallback to system python."
    $Py = "python"
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

if (-not $InstallerOnly) {
    $pyiArgs = @(
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name", "WordPack",
        "--icon", "icon/app-icon.ico",
        "--add-data", "icon;icon",
        "--add-data", "src/uia_capture.ps1;src",
        "--add-data", "src/windows_ocr.ps1;src",
        "--add-data", "src/ui_webview/frontend;src/ui_webview/frontend",
        "--hidden-import", "argostranslate",
        "--hidden-import", "argostranslate.package",
        "--hidden-import", "argostranslate.translate",
        "--hidden-import", "argostranslate.sbd",
        "--hidden-import", "stanza",
        "--exclude-module", "tkinter",
        "--exclude-module", "spacy",
        "--exclude-module", "torch",
        "--exclude-module", "tensorflow",
        "app.py"
    )

    & $Py -m PyInstaller @pyiArgs
}

if (!(Test-Path "dist/WordPack/WordPack.exe")) {
    throw "dist/WordPack/WordPack.exe not found. Build onedir first."
}

$pf86 = ${env:ProgramFiles(x86)}
if (-not $pf86) {
    $pf86 = $env:ProgramFiles
}

$isccCandidates = @(
    (Join-Path $ProjectRoot "tools\InnoSetup\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $pf86 "Inno Setup 6\ISCC.exe")
)
$iscc = $null
foreach ($candidate in $isccCandidates) {
    if ($candidate -and (Test-Path $candidate)) {
        $iscc = $candidate
        break
    }
}

if (-not $iscc) {
    Write-Warning "ISCC.exe not found. onedir build completed."
    Write-Warning "Install Inno Setup 6 and run: powershell -ExecutionPolicy Bypass -File .\\scripts\\build_release.ps1 -InstallerOnly"
    Write-Host "Build done. Output: dist/WordPack" -ForegroundColor Yellow
    exit 0
}

& $iscc "scripts\WordPack.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed for online installer."
}

if ($WithOfflineInstaller) {
    $webview2StandaloneInstaller = Join-Path $ProjectRoot "webview2\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    $argosModelDir = Join-Path $ProjectRoot "argosmodel"
    if (!(Test-Path $webview2StandaloneInstaller)) {
        throw "Offline installer requires $webview2StandaloneInstaller"
    }
    if (!(Test-Path $argosModelDir)) {
        throw "Offline installer requires $argosModelDir"
    }
    & $iscc "/DOFFLINE=1" "scripts\WordPack.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed for offline installer."
    }
}

Write-Host "Build done. App dir: dist/WordPack" -ForegroundColor Green
Write-Host "Installer: dist/installer/WordPack-Setup.exe" -ForegroundColor Green
if ($WithOfflineInstaller) {
    Write-Host "Installer: dist/installer/WordPack-Offline-Setup.exe" -ForegroundColor Green
}

