# WordPack（Windows）

WordPack 是一个 Windows 桌面翻译工具，支持离线翻译、OpenAI 兼容接口翻译、划词翻译，以及截图翻译。

当前仓库的重点是把桌面端核心链路做稳：
- 输入文本翻译
- 划词翻译
- 截图翻译
- 离线模型管理
- OpenAI / Ollama 兼容接口接入

## 功能概览

- 双模式翻译：`offline` 与 `ai`
- 主界面支持：翻译、AI 润色、粘贴并翻译、截图翻译
- 划词翻译支持两种触发方式：
  - `icon`：选区静置后显示小图标
  - `double_ctrl`：双击 `Ctrl` 触发
- 划词取词链路：
  - 优先 `UI Automation`
  - 失败时回退 `Ctrl+C + 剪贴板`
- 截图翻译链路：
  - `Ctrl+Alt+S` 或点击“截图翻译”
  - 拖拽选择区域
  - 本地 OCR 提取文本
  - 复用现有翻译链路与结果气泡
- 全局快捷键：
  - `Ctrl+Alt+T`：划词翻译
  - `Ctrl+Alt+S`：截图翻译
  - `Ctrl+Alt+H`：显示 / 隐藏主窗口
- 本地持久化：
  - SQLite 历史记录
  - JSON 配置文件
  - 按日滚动日志

## 运行环境

- Windows 10 / 11
- Python 3.10+

## 安装与启动

```powershell
pip install -r requirements.txt
python app.py
```

## 打包发布

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release.ps1
```

构建完成后，产物位于 `dist/WordPack`。

## 离线翻译

离线模式分两层：
- 优先使用 Argos Translate 模型
- 没有可用模型时回退内置离线词典

在应用中点击“离线模型”后可：
1. 导入 `.argosmodel`
2. 选择默认方向
3. 保存配置

说明：
- 未安装 Argos 运行库或未导入模型时，会回退词典兜底
- 长文本离线翻译依赖 Argos 模型

## AI 配置

在应用中点击“AI 设置”后配置：
- `Base URL`
- `API Key`
- `Model`
- `Timeout(s)`

内置 Ollama 默认值：
- Base URL: `http://127.0.0.1:11434/v1`
- API Key: `ollama`

配置完成后可点击“测试AI”验证连接。

## 划词翻译说明

WordPack 当前的取词顺序：
1. `UI Automation`
2. `Ctrl+C` 剪贴板回退

适合 `UI Automation` 的常见控件：
- 原生 `Edit`
- `Document`
- 部分 RichEdit / WPF / XAML 文本控件

不稳定或经常回退的场景：
- 浏览器内容区
- Electron / Chromium 嵌入页面
- 自绘控件
- 终端、游戏、Canvas、GPU 渲染界面

当 UIA 不暴露 `TextPattern`、没有选区、超时或脚本异常时，才会回退到剪贴板。

## 截图翻译说明

截图翻译默认流程：
1. 触发截图模式
2. 拖拽选择区域
3. 使用本地 OCR 提取文本
4. 调用当前翻译模式进行翻译
5. 在鼠标附近显示结果气泡

交互说明：
- 左键拖拽选择区域
- `Esc` 退出截图模式
- 右键退出截图模式

当前实现策略：
- 截图后立即显示结果气泡
- 气泡内部展示“翻译中”，不单独暴露 OCR 中间态
- 最终结果直接更新到同一气泡

## 配置文件与数据

- `data/config.json`：运行时配置
- `data/history.db`：历史记录数据库
- `data/offline_dict.json`：内置离线词典
- `%USERPROFILE%\\.wordpack\\`：日志目录

## 项目结构

```text
.
├─ app.py
├─ requirements.txt
├─ scripts/
│  └─ build_release.ps1
├─ src/
│  ├─ translator.py
│  ├─ config.py
│  ├─ storage.py
│  ├─ hotkeys.py
│  ├─ mouse_hooks.py
│  ├─ selection_capture.py
│  ├─ uia_capture.ps1
│  ├─ screenshot.py
│  ├─ ocr.py
│  ├─ app_logging.py
│  └─ branding.py
└─ data/
   └─ offline_dict.json
```

## 日志与排障

- 应用日志默认写入 `%USERPROFILE%\\.wordpack\\`
- 划词翻译日志会记录最终使用的是 `uia` 还是 `clipboard`
- 截图翻译日志会记录：
  - 截图模式启动 / 取消
  - 截图区域
  - OCR 请求与结果
  - 气泡显示位置
  - 翻译请求与异常
- `argostranslate` 的噪声日志默认压到 `WARNING`

## 已知限制

- 当前仅支持 Windows
- 划词翻译依赖 Win32 全局钩子与 UI Automation
- 浏览器内容区、Electron、自绘控件、终端等场景下，划词取词仍可能失败
- 截图翻译当前输出为整段文本翻译结果，不做原图上的逐块替换排版
- 截图 OCR 为本地 OCR，识别效果受截图清晰度、字号、背景干扰影响

## 开发说明

安装依赖后建议先做静态检查：

```powershell
python -m compileall app.py src
```

如果你修改了打包依赖，记得重新执行发布脚本验证 `dist/WordPack` 是否可运行。
