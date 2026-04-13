# 词小包 WordPack

一个专注高频日常翻译场景的 Windows 桌面翻译工具：支持 AI 翻译、离线词典翻译、划词翻译与截图 OCR 翻译。

[English](./README.md)

## 目录

1. [功能特性](#功能特性)
2. [界面截图](#界面截图)
3. [运行要求](#运行要求)
4. [快速开始](#快速开始)
5. [配置说明](#配置说明)
6. [构建与打包](#构建与打包)
7. [项目结构](#项目结构)
8. [路线图](#路线图)
9. [参与贡献](#参与贡献)

## 功能特性

- AI 翻译（可配置接口和模型）
- 基于 Argos 模型的离线词典翻译（`.argosmodel`）
- 划词翻译（热键或图标触发）
- 截图 OCR 翻译（应用内框选流程）
- 托盘菜单与开机启动控制
- 中英文界面

## 界面截图

建议补充真实截图，能显著提升 README 的可读性和可信度。

```text
建议图片路径：
docs/images/
  main-window.png
  tray-menu.png
  screenshot-selection.png
  settings-ai.png
```

```md
![主界面](docs/images/main-window.png)
![托盘菜单](docs/images/tray-menu.png)
![截图框选](docs/images/screenshot-selection.png)
![AI 设置](docs/images/settings-ai.png)
```

请你补图（截图后按上面文件名放置即可）：
- 主界面（包含词典/AI 模式切换）
- 托盘菜单
- 截图翻译框选界面
- 设置页中的 AI 配置区域

## 运行要求

- Windows 10/11
- Python 3.12（推荐）
- WebView2 Runtime（pywebview 运行必需）

WebView2 下载地址：
- <https://developer.microsoft.com/microsoft-edge/webview2/>

## 快速开始

### 方案 A：直接运行

```powershell
pip install -r requirements.txt
python app.py
```

### 方案 B：开发脚本运行（隔离运行数据目录）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_dev.ps1
```

## 配置说明

运行数据目录：
- 源码运行：`<repo>/data`
- 打包版本：`<install_dir>/data`（回退到 `%LOCALAPPDATA%\WordPack\data`）
- 可通过环境变量 `WORDPACK_DATA_DIR` 覆盖

主配置文件：
- 运行数据目录下的 `config.json`

常用配置项：
- `ui_language`: `zh-CN` / `en-US`
- `translation_mode`: `dictionary` / `ai`
- `openai.base_url` / `openai.api_key` / `openai.model`
- `interaction.screenshot_hotkey` / `interaction.main_toggle_hotkey`

## 构建与打包

产物位置：
- 程序目录版：`dist/WordPack`
- 安装包：`dist/installer/WordPack-Setup.exe`

### 全量清理后构建

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -Clean
```

### 仅构建安装包（复用已有 `dist/WordPack`）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -InstallerOnly
```

### 构建离线安装包

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1 -WithOfflineInstaller
```

说明：
- 生成安装包依赖 Inno Setup 6（`ISCC.exe`）
- 脚本会按以下顺序查找 `ISCC.exe`：
1. `tools/InnoSetup/ISCC.exe`
2. `%ProgramFiles%\Inno Setup 6\ISCC.exe`
3. `%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe`

## 项目结构

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
  data/               # 源码模式运行数据
  dist/               # 构建产物
```

## 路线图

- 优化首次启动引导（AI 配置与词典模型导入）
- 增加打包链路端到端冒烟测试
- 完善 README 截图与使用演示

## 参与贡献

欢迎提交 Issue 和 Pull Request。

建议流程：
1. 新建功能分支
2. 保持改动聚焦且可验证
3. 提交 PR 时写清行为变化，UI 变更加截图
