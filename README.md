# WordPack 词小包（Windows）

WordPack（词小包）是一个 Windows 桌面翻译工具，支持离线翻译与 OpenAI 兼容接口翻译（含 Ollama）。

当前仓库为**初版（v0.1.0）**，目标是先稳定核心流程：输入翻译、划词翻译、AI 接口切换、离线模型管理。

## 功能概览

- 双模式翻译：`offline`（Argos 优先，词典兜底）与 `ai`（OpenAI 兼容/Ollama）。
- 主界面支持：翻译、AI 润色、粘贴并翻译、AI 配置保存与连接测试。
- 划词翻译支持 `icon` / `double_ctrl` 两种触发，图标触发支持 `click` / `hover`。
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

## 已知限制

- 当前仅支持 Windows（依赖 Win32 全局钩子）
- 划词翻译依赖复制选中文本，目标程序禁止复制时可能抓取失败
- 在未导入 Argos 模型时，离线长文本能力受限

## 初版说明

- 当前版本定位为首个可用版本，优先保证主流程稳定
- 欢迎基于 Issue/PR 反馈异常场景与改进建议
