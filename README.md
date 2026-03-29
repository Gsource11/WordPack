# WordPack 词小包（Windows）

WordPack（词小包）是一个 Windows 桌面翻译工具，支持离线翻译与 OpenAI 兼容接口翻译（含 Ollama）。

当前仓库为**初版（v0.1.0）**，目标是先稳定核心流程：输入翻译、划词翻译、AI 接口切换、离线模型管理。

## 功能概览

- 双模式翻译：`offline`（Argos 优先，词典兜底）与 `ai`（OpenAI 兼容/Ollama）。
- 主界面支持：翻译、AI 润色、粘贴并翻译、AI 配置保存与连接测试。
- 划词翻译支持 `icon` / `double_ctrl` 两种触发，图标触发支持 `click` / `hover`。
- 划词取词链路升级为 `UI Automation -> Ctrl+C fallback`，优先读取当前控件公开的选区文本。
- 全局快捷键：`Ctrl+Alt+T`（划词翻译，可关闭）与 `Ctrl+Alt+H`（显示/隐藏主窗口）。
- 本地数据持久化：SQLite 历史记录、配置文件、按日滚动日志（单文件 10MB 分片）。

## 使用方式

### 1) 普通用户（推荐）

直接使用打包产物（无需安装 Python）。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1
```

构建完成后产物在 `dist/WordPack`，分发整个目录即可。

### 2) 开发运行

环境要求：

- Windows 10/11
- Python 3.10+（需可用 `tkinter`）

安装与启动：

```powershell
pip install -r requirements.txt
python app.py
```

## 离线模型（Argos）

在应用中点击 `离线模型`：

1. 点击 `导入模型`，选择 `.argosmodel` 文件  
2. 选择默认方向（`auto` 或如 `en->zh` / `zh->en`）  
3. 点击 `保存选择`

说明：

- 未安装 Argos 运行库或未导入模型时，离线模式会退回词典兜底
- 长文本离线翻译依赖 Argos 模型

## AI 配置（OpenAI 兼容 / Ollama）

在应用中点击 `AI设置`：

- `Base URL`
- `API Key`
- `Model`
- `Timeout(s)`

可点击 `Ollama默认` 自动填充本地参数：

- Base URL: `http://127.0.0.1:11434/v1`
- API Key: `ollama`

配置后可使用 `保存并测试` 或主界面 `测试AI`。

## 划词取词策略

WordPack 当前的系统取词顺序为：

1. 先走 `UI Automation`
2. 若 UIA 当前拿不到选区，再回退到模拟 `Ctrl+C` 并读取剪贴板

### 1) UI Automation 怎么接入

- 通过 `src/selection_capture.py` 调起 `src/uia_capture.ps1`
- PowerShell 脚本使用 Windows 自带的 `.NET UI Automation` 接口读取前台焦点元素与鼠标点位元素
- 优先尝试当前元素及其父元素链上的 `TextPattern.GetSelection()`
- 成功时直接返回选中文本，不污染剪贴板

### 2) 哪些控件能稳定取词

`UIA stable`

- 暴露 `TextPattern` 的 `Edit` 控件
- 暴露 `TextPattern` 的 `Document` 控件
- 常见原生文本输入框、RichEdit、部分 WPF / XAML 文本控件

`UIA conditional`

- 浏览器内容区
- Electron / Chromium 嵌入页面
- 自绘文本控件
- 终端、游戏、Canvas 或 GPU 渲染表面

这些控件不一定公开稳定的 `TextPattern`，所以可能直接走回退。

### 3) 什么时候回退到 Ctrl+C

以下情况会回退到 `Ctrl+C`：

- UIA 没有找到可用前台元素
- 当前控件不支持 `TextPattern`
- UIA 返回空选区
- UIA 调用超时或脚本异常

以下情况不会强制回退：

- 密码框等 `IsPassword=true` 的控件

## 项目结构

```text
.
├─ app.py                      # 程序入口
├─ requirements.txt            # 依赖
├─ scripts/
│  └─ build_release.ps1        # 发布构建脚本（PyInstaller）
├─ src/
│  ├─ ui.py                    # Tkinter 主界面与交互流程
│  ├─ translator.py            # 离线/AI 翻译服务
│  ├─ hotkeys.py               # 全局键盘热键
│  ├─ mouse_hooks.py           # 全局鼠标钩子
│  ├─ selection_capture.py     # UIA 优先、Ctrl+C 兜底的取词策略
│  ├─ uia_capture.ps1          # Windows UI Automation 取词脚本
│  ├─ storage.py               # 历史记录存储
│  ├─ config.py                # 配置模型与读写
│  ├─ app_logging.py           # 日志系统
│  └─ branding.py              # 应用命名
└─ data/
   └─ offline_dict.json        # 离线词典兜底数据
```

## 数据文件说明

- `data/config.json`：运行时配置（已加入 `.gitignore`）
- `data/history.db`：本地历史数据库（已加入 `.gitignore`）
- `data/offline_dict.json`：内置离线词典兜底
- `%USERPROFILE%\.wordpack\`：日志目录

## 日志与排障

- 应用日志默认写入 `%USERPROFILE%\.wordpack\`
- 划词取词日志会记录最终使用的是 `uia` 还是 `clipboard`
- 当取词回退或失败时，日志会附带 `uia_reason`、`clipboard_reason`、控件类型和简要细节
- `argostranslate` 的调试级 INFO 日志默认已降噪到 `WARNING`，避免淹没取词诊断日志

## 已知限制

- 当前仅支持 Windows（依赖 Win32 全局钩子）
- 划词翻译优先依赖 UI Automation；当目标程序不暴露文本选区时才回退到复制选中文本
- 浏览器内容区、Electron、自绘控件、终端等场景下，仍可能因目标程序限制而抓取失败
- 在未导入 Argos 模型时，离线长文本能力受限

## 初版说明

- 当前版本定位为首个可用版本，优先保证主流程稳定
- 欢迎基于 Issue/PR 反馈异常场景与改进建议
