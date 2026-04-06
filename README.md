# WordPack (Windows)

WordPack 是一个 Windows 桌面翻译工具，支持：
- AI 翻译
- Argos 离线模型翻译（通过 `.argosmodel`）
- 划词翻译
- 截图 OCR 翻译

## 运行环境

- Windows 10/11
- Python 3.12（推荐）

## 本地启动

```powershell
pip install -r requirements.txt
python app.py
```

## 发布打包（安装包）

目标产物：
- 程序目录版：`dist/WordPack`
- 安装包：`dist/installer/WordPack-Setup.exe`

### 1. 安装 Inno Setup 编译器（一次性）

本仓库不再包含 `innosetup-*.exe`。  
请从官网下载安装 Inno Setup 6：`https://jrsoftware.org/isdl.php`

安装后需能找到 `ISCC.exe`，脚本会按以下顺序查找：
1. `tools/InnoSetup/ISCC.exe`
2. `%ProgramFiles%\Inno Setup 6\ISCC.exe`
3. `%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe`

### 2. 从头打包（已验证流程）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -Clean
```

脚本会执行：
1. 创建/刷新 `.venv-release`
2. 安装依赖和 `pyinstaller`
3. 构建 `onedir` 到 `dist/WordPack`
4. 调用 `ISCC.exe` 编译 `scripts/WordPack.iss`
5. 生成安装包 `dist/installer/WordPack-Setup.exe`

### 3. 仅重打安装包（不重编 Python）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -InstallerOnly
```

要求 `dist/WordPack/WordPack.exe` 已存在。

## 离线模型（Argos）

不再内置“几十词条兜底词典”。  
离线翻译依赖 Argos 模型包（`.argosmodel`），由用户自行下载导入。

## 目录说明

```text
WordPack/
  app.py
  requirements.txt
  scripts/
    build_release.ps1
    WordPack.iss
  src/
  data/
  dist/               # 构建输出
  tools/InnoSetup/    # 可选：本地 Inno 编译器（不入库）
```

## 关于 tools/InnoSetup

`tools/InnoSetup/` 是本地编译器目录，不建议提交到 GitHub：
- 体积大
- 属于第三方二进制
- 会显著增加仓库体积

建议仅提交脚本和配置：
- `scripts/build_release.ps1`
- `scripts/WordPack.iss`

## 2026 Refactor Notes

### Screenshot Capture Path
- Screenshot translation now uses an in-app `screenshot` window flow (Pot-style):
  - Capture virtual screen once
  - User drag-select region
  - Crop and OCR/translate
- System snipping (`ms-screenclip`) is no longer the default path.

### OCR Engine
- OCR is now Windows OCR only.
- OCR config section in `config.json`:
  - `ocr.windows_lang`: default `auto`
  - `ocr.timeout_sec`: default `6`

### Build
- Use a single dependency set:
  - `requirements.txt`
- Build script no longer includes OCR-pack variant toggles:

```powershell
# Standard package
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -Clean
```
