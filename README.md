# WordPack

A lightweight Windows desktop translator focused on fast daily workflows: AI translation, offline dictionary translation, selection translation, and screenshot OCR translation.

[中文文档 (Chinese)](./README.zh-CN.md)

## Table of Contents

1. [Features](#features)
2. [Screenshots](#screenshots)
3. [Requirements](#requirements)
4. [Quick Start](#quick-start)
5. [Configuration](#configuration)
6. [Build and Packaging](#build-and-packaging)
7. [Project Structure](#project-structure)
8. [Roadmap](#roadmap)
9. [Contributing](#contributing)

## Features

- AI translation (configurable endpoint/model)
- Offline dictionary translation via Argos models (`.argosmodel`)
- Selection translation (hotkey / icon trigger)
- Screenshot OCR translation (in-app region capture flow)
- Tray menu controls and startup options
- English/Chinese UI support

## Screenshots

Add real screenshots to make the README more useful for new users.

```text
Suggested path:
docs/images/
  main-window.png
  tray-menu.png
  screenshot-selection.png
  settings-ai.png
```

```md
![Main Window](docs/images/main-window.png)
![Tray Menu](docs/images/tray-menu.png)
![Screenshot Selection](docs/images/screenshot-selection.png)
![AI Settings](docs/images/settings-ai.png)
```

TODO for you:
- Capture main window (dictionary + AI mode switch visible)
- Capture tray menu
- Capture screenshot region selection UI
- Capture settings page (AI config section)

## Requirements

- Windows 10/11
- Python 3.12 (recommended)
- WebView2 Runtime (required by pywebview)

WebView2 download:
- <https://developer.microsoft.com/microsoft-edge/webview2/>

## Quick Start

### Option A: Direct run

```powershell
pip install -r requirements.txt
python app.py
```

### Option B: Dev script (isolated runtime data)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_dev.ps1
```

## Configuration

Runtime data directory:
- Development (source run): `<repo>/data`
- Packaged app: `<install_dir>/data` (fallback: `%LOCALAPPDATA%\WordPack\data`)
- Optional override: set `WORDPACK_DATA_DIR`

Main config file:
- `config.json` under runtime data directory

Key options:
- `ui_language`: `zh-CN` or `en-US`
- `translation_mode`: `dictionary` or `ai`
- `openai.base_url`, `openai.api_key`, `openai.model`
- `interaction.screenshot_hotkey`, `interaction.main_toggle_hotkey`

## Build and Packaging

Output targets:
- App directory: `dist/WordPack`
- Installer: `dist/installer/WordPack-Setup.exe`

### Full clean build

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -Clean
```

### Build installer only (reuse existing `dist/WordPack`)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -InstallerOnly
```

### Build offline installer

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -WithOfflineInstaller
```

Notes:
- Inno Setup 6 is required for installer generation (`ISCC.exe`)
- The build script checks these locations for `ISCC.exe`:
1. `tools/InnoSetup/ISCC.exe`
2. `%ProgramFiles%\Inno Setup 6\ISCC.exe`
3. `%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe`

## Project Structure

```text
WordPack/
  app.py
  requirements.txt
  scripts/
    run_dev.ps1
    build_release.ps1
    WordPack.iss
  src/
  icon/
  data/               # runtime data (source mode)
  dist/               # build outputs
```

## Roadmap

- Improve first-run onboarding (AI + dictionary model import guidance)
- Add end-to-end smoke tests for packaging
- Improve README screenshots and usage demos

## Contributing

Issues and pull requests are welcome.

Recommended contribution flow:
1. Create a feature branch
2. Keep changes focused and testable
3. Open a PR with clear behavior changes and screenshots (for UI changes)
