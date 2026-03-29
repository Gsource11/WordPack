from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ctypes import Structure, byref, c_int, c_size_t, memmove, sizeof, windll

from .app_logging import get_logger
from .branding import APP_TITLE
from .config import AppConfig, ConfigStore
from .hotkeys import HotkeyManager
from .ocr import ScreenshotOCRService
from .selection_capture import ClipboardCaptureResult, SelectionCaptureService
from .mouse_hooks import MouseHookManager
from .screenshot import ScreenCaptureOverlay, ScreenRegion, capture_screen_region
from .storage import HistoryStore
from .translator import TranslationService


class POINT(Structure):
    _fields_ = [("x", c_int), ("y", c_int)]


class TranslatorApp:
    def __init__(self) -> None:
        self.base_dir = Path(__file__).resolve().parent.parent
        self.data_dir = self.base_dir / "data"

        self.config_store = ConfigStore(self.data_dir / "config.json")
        self.config: AppConfig = self.config_store.load()

        self.history = HistoryStore(self.data_dir / "history.db")
        self.service = TranslationService(self.data_dir / "offline_dict.json", self.get_config)
        self.ocr_service = ScreenshotOCRService()
        self.selection_capture = SelectionCaptureService()
        self.logger = get_logger(__name__)

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_TITLE)
        self.root.geometry("560x680")
        self.root.minsize(480, 540)
        self._window_target_alpha = 0.95
        self.root.attributes("-alpha", 0.0)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#e6ecf2")
        self._center_window(self.root, 560, 680)
        self.mode_var = tk.StringVar(value=self.config.translation_mode)
        self.direction_var = tk.StringVar(value=self._direction_label())
        self.topmost_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value=f"就绪 · {self.service.offline_diagnostics()}")
        self.history_rows: list[dict[str, str]] = []
        self.hidden = False

        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.hotkeys = HotkeyManager(self._on_hook_event)
        self.mouse_hooks = MouseHookManager(self._on_hook_event)
        self._event_poll_active_ms = 16
        self._event_poll_idle_ms = 48
        self._translate_flush_chars = 8
        self._translate_flush_interval_sec = 0.03
        self._translate_in_flight = False

        self.pending_selection_text = ""
        self.pending_selection_at = 0.0
        self.selection_icon_popup: tk.Toplevel | None = None
        self.selection_capture_job: str | None = None
        self.selection_capture_retry: int = 0
        self.selection_capture_payload: dict | None = None
        self.selection_icon_anchor_pos: tuple[int, int] | None = None

        self.result_popup: tk.Toplevel | None = None
        self.result_popup_leave_job: str | None = None
        self.result_popup_auto_close_job: str | None = None
        self.result_popup_pinned = False
        self.result_popup_dragging = False
        self.result_popup_hovering = False
        self.result_popup_pending = False

        self._cursor_last_pos: tuple[int, int] | None = None
        self._active_popup_position: tuple[int, int] | None = None
        self._cursor_last_move_at = time.time()
        self._translate_request_seq = 0
        self._active_translate_request_seq = 0
        self._active_popup_request_seq = 0
        self._screenshot_request_seq = 0
        self._active_screenshot_request_seq = 0
        self._active_translation_text = ""
        self._result_popup_source_text = ""
        self._result_popup_result_text = ""
        self._result_popup_title_var: tk.StringVar | None = None
        self._result_popup_content_widget: tk.Text | None = None
        self._result_popup_copy_button: tk.Button | None = None
        self._screenshot_overlay: ScreenCaptureOverlay | None = None
        self._screenshot_capture_active = False
        self._screenshot_show_popup = True
        self._screenshot_popup_position: tuple[int, int] | None = None
        self._screenshot_temporarily_hid_root = False

        # Polling fallback: avoids relying only on low-level global hooks.
        self._fb_last_lbtn_down = False
        self._fb_lbtn_down_pos: tuple[int, int] | None = None
        self._fb_last_lbtn_up_at = 0.0
        self._fb_lbtn_click_count = 0
        self._fb_last_ctrl_down = False
        self._fb_last_ctrl_tap_at = 0.0
        self._fb_last_combo_t = False
        self._fb_last_combo_s = False
        self._fb_last_combo_h = False
        self._last_selection_trigger_at = 0.0
        self._last_poll_input_error_log_at = 0.0
        self._last_clipboard_restore_error_log_at = 0.0
        self._build_ui()
        self._apply_glass_effect()
        self._load_history()
        self.logger.info("UI initialized")

        self.mode_var.trace_add("write", self._on_mode_changed)
        self.root.bind("<Escape>", lambda _e: self._hide_window())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._on_tk_callback_exception
        self.root.after(self._event_poll_idle_ms, self._poll_events)
        self.root.after(12, self._poll_input_fallback)

    def run(self) -> None:
        self._show_initial_window()

        # Polling fallback is primary; hooks are best-effort enhancements.
        try:
            self.hotkeys.start()
            self.mouse_hooks.start()
        except Exception:
            self.logger.exception("Failed to start hook services")
        self.root.mainloop()

    def get_config(self) -> AppConfig:
        return self.config

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Glass.TFrame", background="#edf2f7")
        style.configure("Glass.TLabel", background="#edf2f7", foreground="#1d2733")
        style.configure("Glass.TButton", background="#d9e5f0", foreground="#122131", borderwidth=0)

        main = ttk.Frame(self.root, style="Glass.TFrame", padding=10)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main, style="Glass.TFrame")
        top.pack(fill="x", pady=(0, 8))

        ttk.Label(top, text="模式", style="Glass.TLabel").pack(side="left")
        ttk.Combobox(
            top,
            textvariable=self.mode_var,
            values=["offline", "ai"],
            width=9,
            state="readonly",
        ).pack(side="left", padx=6)
        ttk.Button(top, textvariable=self.direction_var, command=self._cycle_translation_direction, style="Glass.TButton").pack(side="left", padx=(0, 6))

        ttk.Checkbutton(top, text="置顶", variable=self.topmost_var, command=self._on_topmost_changed).pack(side="left", padx=(2, 6))
        ttk.Button(top, text="交互设置", command=self._open_capture_settings, style="Glass.TButton").pack(side="left")
        ttk.Button(top, text="离线模型", command=self._open_offline_models, style="Glass.TButton").pack(side="left", padx=6)
        ttk.Button(top, text="AI设置", command=self._open_settings, style="Glass.TButton").pack(side="left")
        ttk.Button(top, text="测试AI", command=self._on_test_ai_click, style="Glass.TButton").pack(side="left", padx=6)
        ttk.Button(top, text="隐藏", command=self._hide_window, style="Glass.TButton").pack(side="right")

        actions = ttk.Frame(main, style="Glass.TFrame")
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="翻译", command=self._on_translate_click, style="Glass.TButton").pack(side="left")
        ttk.Button(actions, text="润色(AI)", command=self._on_polish_click, style="Glass.TButton").pack(side="left", padx=6)
        ttk.Button(actions, text="粘贴并翻译", command=self._paste_and_translate, style="Glass.TButton").pack(side="left")
        ttk.Button(actions, text="截图翻译", command=self._on_screenshot_translate_click, style="Glass.TButton").pack(side="left", padx=6)

        ttk.Label(
            main,
            text=(
                "划词: 图标触发或双击 Ctrl 触发（可配置） ｜ "
                "快捷键: Ctrl+Alt+T ｜ 截图: Ctrl+Alt+S"
            ),
            style="Glass.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(main, text="输入文本", style="Glass.TLabel").pack(anchor="w")
        self.source_text = tk.Text(
            main,
            height=7,
            font=("Microsoft YaHei UI", 10),
            bg="#f5f8fb",
            fg="#15212d",
            relief="flat",
            padx=10,
            pady=8,
            insertbackground="#223447",
        )
        self.source_text.pack(fill="x")

        ttk.Label(main, text="翻译结果", style="Glass.TLabel").pack(anchor="w", pady=(8, 0))
        self.result_text = tk.Text(
            main,
            height=9,
            font=("Microsoft YaHei UI", 10),
            bg="#f8fbff",
            fg="#0f2136",
            relief="flat",
            padx=10,
            pady=8,
            state="disabled",
        )
        self.result_text.pack(fill="both", expand=True)

        ttk.Label(main, text="历史记录", style="Glass.TLabel").pack(anchor="w", pady=(8, 0))
        self.history_list = tk.Listbox(
            main,
            height=7,
            font=("Microsoft YaHei UI", 9),
            bg="#f2f7fc",
            fg="#15212d",
            relief="flat",
            highlightthickness=0,
            activestyle="none",
        )
        self.history_list.pack(fill="x")
        self.history_list.bind("<<ListboxSelect>>", self._on_history_selected)

        bottom = ttk.Frame(main, style="Glass.TFrame")
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="清空历史", command=self._clear_history, style="Glass.TButton").pack(side="right")
        ttk.Label(bottom, textvariable=self.status_var, style="Glass.TLabel").pack(side="left", fill="x", expand=True)

    def _save_interaction_config(self) -> None:
        self.config_store.save(self.config)

    def _center_window(
        self,
        window: tk.Tk | tk.Toplevel,
        width: int,
        height: int,
        parent: tk.Tk | tk.Toplevel | None = None,
    ) -> None:
        try:
            window.update_idletasks()
        except Exception:
            pass

        screen_w = int(window.winfo_screenwidth())
        screen_h = int(window.winfo_screenheight())
        x = (screen_w - int(width)) // 2
        y = (screen_h - int(height)) // 2

        if parent is not None:
            try:
                if parent.winfo_exists():
                    parent.update_idletasks()
                    pw = int(parent.winfo_width())
                    ph = int(parent.winfo_height())
                    if pw > 1 and ph > 1:
                        px = int(parent.winfo_x())
                        py = int(parent.winfo_y())
                        x = px + (pw - int(width)) // 2
                        y = py + (ph - int(height)) // 2
            except Exception:
                pass

        max_x = max(0, screen_w - int(width))
        max_y = max(0, screen_h - int(height))
        x = max(0, min(x, max_x))
        y = max(0, min(y, max_y))
        window.geometry(f"{int(width)}x{int(height)}+{x}+{y}")

    def _show_initial_window(self) -> None:
        if self.hidden:
            return
        self._center_window(self.root, 560, 680)
        self.root.deiconify()

        def reveal_root() -> None:
            if not self.root.winfo_exists():
                return
            try:
                self.root.attributes("-alpha", float(self._window_target_alpha))
            except Exception:
                pass
            self.root.lift()
            self.root.after_idle(self.root.lift)

        self.root.after(10, reveal_root)

    def _show_dialog(self, dialog: tk.Toplevel, width: int, height: int) -> None:
        try:
            dialog.attributes("-alpha", 0.0)
        except Exception:
            pass

        dialog.update_idletasks()
        self._center_window(dialog, width, height, parent=self.root)
        dialog.deiconify()

        def reveal() -> None:
            if not dialog.winfo_exists():
                return
            dialog.lift()
            dialog.update_idletasks()
            dialog.grab_set()
            try:
                dialog.focus_force()
            except Exception:
                pass
            try:
                dialog.attributes("-alpha", 1.0)
            except Exception:
                pass

        dialog.after(10, reveal)
    def _open_capture_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title("交互设置")
        dialog.transient(self.root)
        dialog.configure(bg="#edf2f7")

        frame = ttk.Frame(dialog, style="Glass.TFrame", padding=12)
        frame.pack(fill="both", expand=True)

        selection_enabled_var = tk.BooleanVar(value=self.config.interaction.selection_enabled)
        selection_mode_var = tk.StringVar(value=self.config.interaction.selection_trigger_mode)
        selection_icon_trigger_var = tk.StringVar(value=self.config.interaction.selection_icon_trigger)
        selection_icon_cancel_sensitivity_var = tk.StringVar(value=self.config.interaction.selection_icon_cancel_sensitivity)
        selection_hotkey_var = tk.BooleanVar(value=self.config.interaction.selection_hotkey_enabled)
        screenshot_hotkey_var = tk.BooleanVar(value=self.config.interaction.screenshot_hotkey_enabled)
        icon_delay_var = tk.StringVar(value=str(self.config.interaction.selection_icon_delay_ms))

        ttk.Checkbutton(frame, text="启用划词翻译", variable=selection_enabled_var).pack(anchor="w", pady=3)
        row = ttk.Frame(frame, style="Glass.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="划词触发模式", style="Glass.TLabel").pack(side="left")
        ttk.Combobox(
            row,
            textvariable=selection_mode_var,
            values=["icon", "double_ctrl"],
            width=16,
            state="readonly",
        ).pack(side="left", padx=8)
        ttk.Label(row, text="icon=图标触发，double_ctrl=双击Ctrl触发", style="Glass.TLabel").pack(side="left")

        row2 = ttk.Frame(frame, style="Glass.TFrame")
        row2.pack(fill="x", pady=(10, 0))
        ttk.Label(row2, text="图标触发方式", style="Glass.TLabel").pack(side="left")
        ttk.Combobox(
            row2,
            textvariable=selection_icon_trigger_var,
            values=["click", "hover"],
            width=10,
            state="readonly",
        ).pack(side="left", padx=8)
        ttk.Label(row2, text="click=点击图标翻译，hover=鼠标悬停图标翻译", style="Glass.TLabel").pack(side="left")

        row3 = ttk.Frame(frame, style="Glass.TFrame")
        row3.pack(fill="x", pady=(10, 0))
        ttk.Label(row3, text="图标隐藏灵敏度", style="Glass.TLabel").pack(side="left")
        ttk.Combobox(
            row3,
            textvariable=selection_icon_cancel_sensitivity_var,
            values=["high", "medium", "low"],
            width=10,
            state="readonly",
        ).pack(side="left", padx=8)
        ttk.Label(row3, text="high=最灵敏，low=最不灵敏", style="Glass.TLabel").pack(side="left")

        row4 = ttk.Frame(frame, style="Glass.TFrame")
        row4.pack(fill="x", pady=(10, 0))
        ttk.Label(row4, text="图标延时(ms)", style="Glass.TLabel").pack(side="left")
        tk.Entry(row4, textvariable=icon_delay_var, width=10).pack(side="left", padx=8)
        ttk.Label(row4, text="选区后鼠标静置该时长才显示图标", style="Glass.TLabel").pack(side="left")

        ttk.Checkbutton(frame, text="启用划词快捷键（Ctrl+Alt+T）", variable=selection_hotkey_var).pack(anchor="w", pady=(12, 2))
        ttk.Checkbutton(frame, text="启用截图快捷键（Ctrl+Alt+S）", variable=screenshot_hotkey_var).pack(anchor="w", pady=(4, 2))

        ttk.Label(
            frame,
            text=(
                "说明: 划词取词优先走 UI Automation。"
                "标准 Edit / Document 文本控件最稳定；浏览器内容区、Electron、自绘画布、终端等控件常会回退。"
                "当 UIA 不暴露 TextPattern、没有选区或超时失败时，才回退到 Ctrl+C。"
            ),
            style="Glass.TLabel",
            wraplength=560,
        ).pack(anchor="w", pady=(12, 2))

        def save() -> None:
            mode = selection_mode_var.get().strip()
            if mode not in {"icon", "double_ctrl"}:
                mode = "icon"

            icon_trigger = selection_icon_trigger_var.get().strip()
            if icon_trigger not in {"click", "hover"}:
                icon_trigger = "click"

            icon_cancel_sensitivity = selection_icon_cancel_sensitivity_var.get().strip()
            if icon_cancel_sensitivity not in {"low", "medium", "high"}:
                icon_cancel_sensitivity = "medium"

            try:
                icon_delay = int(icon_delay_var.get().strip() or "1500")
            except ValueError:
                messagebox.showerror("错误", "图标延时必须是整数")
                return

            self.config.interaction.selection_enabled = bool(selection_enabled_var.get())
            self.config.interaction.selection_trigger_mode = mode
            self.config.interaction.selection_icon_trigger = icon_trigger
            self.config.interaction.selection_icon_cancel_sensitivity = icon_cancel_sensitivity
            self.config.interaction.selection_hotkey_enabled = bool(selection_hotkey_var.get())
            self.config.interaction.screenshot_hotkey_enabled = bool(screenshot_hotkey_var.get())
            self.config.interaction.selection_icon_delay_ms = max(300, min(5000, icon_delay))

            self._save_interaction_config()
            self.status_var.set("交互设置已保存")
            dialog.destroy()

        ttk.Button(frame, text="保存", command=save, style="Glass.TButton").pack(anchor="e", pady=10)
        self._show_dialog(dialog, 560, 420)

    def _apply_glass_effect(self) -> None:
        self.root.update_idletasks()
        hwnd = self.root.winfo_id()
        try:
            attr = c_int(38)
            backdrop = c_int(3)
            windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, byref(backdrop), sizeof(backdrop))
        except Exception:
            pass

    def _on_mode_changed(self, *_args) -> None:
        value = self.mode_var.get().strip().lower()
        if value not in {"offline", "ai"}:
            value = "offline"
            self.mode_var.set(value)
        self.config.translation_mode = value
        self.config_store.save(self.config)
        if value == "offline":
            self.status_var.set(self.service.offline_diagnostics())
        else:
            self.status_var.set("已切换到 AI 模式")

    def _direction_label(self) -> str:
        value = str(self.config.offline.preferred_direction or "auto").strip() or "auto"
        return "方向: 自动" if value == "auto" else f"方向: {value}"

    def _cycle_translation_direction(self) -> None:
        options = ["auto", "en->zh", "zh->en"]
        current = str(self.config.offline.preferred_direction or "auto").strip() or "auto"
        if current not in options:
            current = "auto"
        index = options.index(current)
        nxt = options[(index + 1) % len(options)]
        self.config.offline.preferred_direction = nxt
        self.config_store.save(self.config)
        self.direction_var.set(self._direction_label())
        self.status_var.set(f"离线方向已切换: {nxt}")

    def _on_topmost_changed(self) -> None:
        is_top = bool(self.topmost_var.get())
        self.root.attributes("-topmost", is_top)
        self.status_var.set("窗口已置顶" if is_top else "窗口取消置顶")

    def _hide_window(self) -> None:
        if self.hidden:
            return
        self.hidden = True
        self.root.withdraw()

    def _show_window(self) -> None:
        self.hidden = False
        self.root.deiconify()
        try:
            self.root.attributes("-alpha", float(self._window_target_alpha))
        except Exception:
            pass
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def _toggle_window_visibility(self) -> None:
        if self.hidden:
            self._show_window()
            self.status_var.set("窗口已显示")
        else:
            self._hide_window()

    def _open_offline_models(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title("离线模型管理（Argos）")
        dialog.transient(self.root)
        dialog.configure(bg="#edf2f7")

        frame = ttk.Frame(dialog, style="Glass.TFrame", padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="当前默认方向", style="Glass.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        direction_var = tk.StringVar(value=self.config.offline.preferred_direction or "auto")
        direction_combo = ttk.Combobox(frame, textvariable=direction_var, state="readonly", width=40)
        direction_combo.grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(frame, text="已安装模型", style="Glass.TLabel").grid(row=1, column=0, sticky="nw", pady=4)
        models_box = tk.Listbox(
            frame,
            height=12,
            font=("Microsoft YaHei UI", 9),
            bg="#f5f8fb",
            fg="#15212d",
            relief="flat",
            highlightthickness=0,
            activestyle="none",
        )
        models_box.grid(row=1, column=1, sticky="nsew", pady=4)

        info_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=info_var, style="Glass.TLabel").grid(row=2, column=1, sticky="w", pady=(4, 8))

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

        def refresh_models() -> None:
            runtime_ready = self.service.offline_runtime_ready()
            runtime_hint = self.service.offline_runtime_hint()

            items = self.service.list_offline_models() if runtime_ready else []
            values = ["auto"] + [item["direction"] for item in items]
            direction_combo["values"] = values

            if direction_var.get() not in values:
                direction_var.set("auto")

            models_box.delete(0, "end")
            if not runtime_ready:
                models_box.insert("end", "(当前构建未包含 Argos 运行库)")
                info_var.set(runtime_hint)
                import_btn.config(state="disabled")
                save_btn.config(state="disabled")
                direction_combo.config(state="disabled")
                return

            import_btn.config(state="normal")
            save_btn.config(state="normal")
            direction_combo.config(state="readonly")

            if not items:
                models_box.insert("end", "(未发现已安装的 Argos 模型方向)")
            else:
                for item in items:
                    models_box.insert("end", f"{item['direction']}  |  {item['label']}")

            info_var.set(f"可用方向数量: {len(items)}")

        def save_selection() -> None:
            if not self.service.offline_runtime_ready():
                messagebox.showerror("离线模型", self.service.offline_runtime_hint())
                return
            selected = direction_var.get().strip() or "auto"
            self.config.offline.preferred_direction = selected
            self.config_store.save(self.config)
            self.direction_var.set(self._direction_label())
            self.status_var.set(self.service.offline_diagnostics())
            messagebox.showinfo("离线模型", f"已保存离线方向: {selected}")

        def import_model() -> None:
            if not self.service.offline_runtime_ready():
                messagebox.showerror("离线模型", self.service.offline_runtime_hint())
                return

            file_path = filedialog.askopenfilename(
                title="选择 Argos 模型文件",
                filetypes=[("Argos model", "*.argosmodel"), ("All files", "*.*")],
            )
            if not file_path:
                return

            info_var.set("正在导入模型，可能持续 10-60 秒，请稍候...")
            import_btn.config(state="disabled")
            refresh_btn.config(state="disabled")
            save_btn.config(state="disabled")
            direction_combo.config(state="disabled")

            def worker() -> None:
                try:
                    message = self.service.import_offline_model(file_path)
                    self.root.after(0, lambda m=message: on_import_done(True, m))
                except Exception as exc:
                    err_msg = str(exc)
                    self.root.after(0, lambda m=err_msg: on_import_done(False, m))

            threading.Thread(target=worker, daemon=True).start()

        def on_import_done(ok: bool, message: str) -> None:
            if not dialog.winfo_exists():
                self.status_var.set(message)
                return
            refresh_btn.config(state="normal")
            refresh_models()
            if ok:
                info_var.set(message)
                self.status_var.set(message)
                messagebox.showinfo("离线模型", message)
            else:
                info_var.set(f"导入失败: {message}")
                self.status_var.set(f"离线模型导入失败: {message}")
                messagebox.showerror("离线模型", message)

        controls = ttk.Frame(frame, style="Glass.TFrame")
        controls.grid(row=3, column=1, sticky="e", pady=6)
        refresh_btn = ttk.Button(controls, text="刷新", command=refresh_models, style="Glass.TButton")
        refresh_btn.pack(side="left")
        import_btn = ttk.Button(controls, text="导入模型", command=import_model, style="Glass.TButton")
        import_btn.pack(side="left", padx=6)
        save_btn = ttk.Button(controls, text="保存选择", command=save_selection, style="Glass.TButton")
        save_btn.pack(side="left")

        refresh_models()
        self._show_dialog(dialog, 640, 420)

    def _open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title("AI 接口设置")
        dialog.transient(self.root)
        dialog.configure(bg="#edf2f7")

        frame = ttk.Frame(dialog, style="Glass.TFrame", padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Base URL", style="Glass.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        base_entry = tk.Entry(frame, width=54)
        base_entry.insert(0, self.config.openai.base_url)
        base_entry.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="API Key", style="Glass.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        key_entry = tk.Entry(frame, width=54, show="*")
        key_entry.insert(0, self.config.openai.api_key)
        key_entry.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="Model", style="Glass.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        model_entry = tk.Entry(frame, width=54)
        model_entry.insert(0, self.config.openai.model)
        model_entry.grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="Timeout(s)", style="Glass.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        timeout_entry = tk.Entry(frame, width=12)
        timeout_entry.insert(0, str(self.config.openai.timeout_sec))
        timeout_entry.grid(row=3, column=1, sticky="w", pady=4)

        frame.columnconfigure(1, weight=1)

        def use_ollama_defaults() -> None:
            base_entry.delete(0, "end")
            base_entry.insert(0, "http://127.0.0.1:11434/v1")
            key_entry.delete(0, "end")
            key_entry.insert(0, "ollama")
            if not model_entry.get().strip():
                model_entry.insert(0, "qwen2.5:7b")

        def save_settings(test_after_save: bool) -> None:
            try:
                timeout = int(timeout_entry.get().strip() or "60")
            except ValueError:
                messagebox.showerror("错误", "Timeout 必须是整数")
                return

            self.config.openai.base_url = base_entry.get().strip()
            self.config.openai.api_key = key_entry.get().strip()
            self.config.openai.model = model_entry.get().strip()
            self.config.openai.timeout_sec = max(5, timeout)
            self.config_store.save(self.config)
            self.status_var.set("AI 配置已保存")
            if test_after_save:
                self._on_test_ai_click()
            dialog.destroy()

        controls = ttk.Frame(frame, style="Glass.TFrame")
        controls.grid(row=5, column=1, sticky="e", pady=12)
        ttk.Button(controls, text="Ollama默认", command=use_ollama_defaults, style="Glass.TButton").pack(side="left")
        ttk.Button(controls, text="保存并测试", command=lambda: save_settings(True), style="Glass.TButton").pack(side="left", padx=6)
        ttk.Button(controls, text="仅保存", command=lambda: save_settings(False), style="Glass.TButton").pack(side="left")
        self._show_dialog(dialog, 560, 320)

    def _on_test_ai_click(self) -> None:
        self.status_var.set("正在测试 AI 连接...")

        def worker() -> None:
            ok, message = self.service.test_ai_connection()
            self.event_queue.put(("ai_test_done", {"ok": ok, "message": message}))

        threading.Thread(target=worker, daemon=True).start()

    def _on_translate_click(self) -> None:
        text = self.source_text.get("1.0", "end").strip()
        self._start_translate(text, action="translate", show_popup=False)

    def _on_polish_click(self) -> None:
        text = self.source_text.get("1.0", "end").strip()
        self._start_translate(text, action="polish", show_popup=False)

    def _paste_and_translate(self) -> None:
        text = self._get_clipboard_text()
        if not text:
            self.status_var.set("剪贴板为空")
            return
        self.source_text.delete("1.0", "end")
        self.source_text.insert("1.0", text)
        self._start_translate(text, action="translate", show_popup=False)

    def _on_screenshot_translate_click(self) -> None:
        self._start_screenshot_capture(show_popup=True)

    def _start_screenshot_capture(self, show_popup: bool) -> None:
        if self._screenshot_overlay is not None:
            self.status_var.set("截图操作已在进行中")
            self.logger.info("Screenshot capture ignored: overlay already active")
            return

        self._translate_request_seq += 1
        self._active_translate_request_seq = self._translate_request_seq
        self._active_popup_request_seq = 0
        self._active_translation_text = ""
        self._set_result_text("")
        self._hide_selection_icon()
        self._destroy_result_popup()
        self._screenshot_capture_active = True
        self._screenshot_show_popup = bool(show_popup)
        self._screenshot_popup_position = None
        self._temporarily_hide_root_for_capture()
        self.status_var.set("拖拽选择截图区域，右键或 Esc 取消")
        self.logger.info("Screenshot capture started popup=%s root_hidden=%s", show_popup, self._screenshot_temporarily_hid_root)
        self.root.after(80, self._launch_screenshot_overlay)

    def _temporarily_hide_root_for_capture(self) -> None:
        self._screenshot_temporarily_hid_root = False
        if self.hidden:
            return
        try:
            self.root.withdraw()
            self._screenshot_temporarily_hid_root = True
        except Exception:
            self._screenshot_temporarily_hid_root = False

    def _restore_root_after_capture(self) -> None:
        if not self._screenshot_temporarily_hid_root:
            return
        self._screenshot_temporarily_hid_root = False
        if self.hidden:
            return
        self.root.deiconify()
        try:
            self.root.attributes("-alpha", float(self._window_target_alpha))
        except Exception:
            pass
        self.root.lift()
        self.root.after_idle(self.root.lift)
        self.logger.info("Screenshot capture restored main window")

    def _keep_root_hidden_after_capture(self) -> None:
        if not self._screenshot_temporarily_hid_root:
            return
        self._screenshot_temporarily_hid_root = False
        self.hidden = True
        self.logger.info("Screenshot capture kept main window hidden for popup flow")

    def _launch_screenshot_overlay(self) -> None:
        if self._screenshot_overlay is not None:
            return
        try:
            overlay = ScreenCaptureOverlay(
                self.root,
                on_capture=self._on_screenshot_region_selected,
                on_cancel=self._on_screenshot_capture_cancelled,
            )
            self._screenshot_overlay = overlay
            overlay.start()
            self.logger.info("Screenshot overlay shown bounds=%s", overlay.bounds.as_bbox())
        except Exception as exc:
            self._screenshot_overlay = None
            self._screenshot_capture_active = False
            self._restore_root_after_capture()
            message = f"无法启动截图: {exc}"
            self.status_var.set(message)
            if self._screenshot_show_popup:
                self._show_result_popup("截图失败", message)
            else:
                messagebox.showerror("截图失败", message)

    def _on_screenshot_capture_cancelled(self) -> None:
        self._screenshot_overlay = None
        self._screenshot_capture_active = False
        self._restore_root_after_capture()
        self.status_var.set("截图已取消")
        self.logger.info("Screenshot capture cancelled")

    def _on_screenshot_region_selected(self, region: ScreenRegion) -> None:
        self._screenshot_overlay = None
        cursor_x, cursor_y = self._get_cursor_position()
        self._screenshot_popup_position = (int(cursor_x) + 12, int(cursor_y) + 16)
        self.status_var.set("正在截取区域...")
        self.logger.info(
            "Screenshot region selected bbox=%s popup_pos=%s",
            region.as_bbox(),
            self._screenshot_popup_position,
        )

        def capture_then_ocr() -> None:
            try:
                image = capture_screen_region(region)
            except Exception as exc:
                self._screenshot_capture_active = False
                if self._screenshot_show_popup:
                    self._keep_root_hidden_after_capture()
                else:
                    self._restore_root_after_capture()
                message = str(exc)
                self.status_var.set(message)
                if self._screenshot_show_popup:
                    self._show_result_popup("截图失败", message, popup_pos=self._screenshot_popup_position)
                else:
                    messagebox.showerror("截图失败", message)
                return

            self._screenshot_capture_active = False
            if self._screenshot_show_popup:
                self._keep_root_hidden_after_capture()
                self._active_popup_position = self._show_result_popup(
                    "截图翻译",
                    "",
                    pending=True,
                    popup_pos=self._screenshot_popup_position,
                )
                self.logger.info("Screenshot popup shown immediately at %s", self._active_popup_position)
            else:
                self._restore_root_after_capture()
            self._start_screenshot_ocr(image, show_popup=self._screenshot_show_popup)

        self.root.after(80, capture_then_ocr)

    def _start_screenshot_ocr(self, image, show_popup: bool) -> None:
        self._screenshot_request_seq += 1
        req_id = self._screenshot_request_seq
        self._active_screenshot_request_seq = req_id
        self.status_var.set("截图翻译中...")
        self.logger.info(
            "Screenshot OCR request size=%sx%s popup=%s",
            getattr(image, "width", "?"),
            getattr(image, "height", "?"),
            show_popup,
        )

        def worker() -> None:
            try:
                text = self.ocr_service.extract_text(image).strip()
                if not text:
                    raise RuntimeError("截图中未识别到可翻译文本")
                self.event_queue.put(
                    (
                        "screenshot_ocr_done",
                        {
                            "text": text,
                            "show_popup": show_popup,
                            "req_id": req_id,
                        },
                    )
                )
            except Exception as exc:
                self.logger.exception("Screenshot OCR failed req_id=%s", req_id)
                self.event_queue.put(
                    (
                        "screenshot_ocr_error",
                        {
                            "message": str(exc),
                            "show_popup": show_popup,
                            "req_id": req_id,
                        },
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def _start_translate(
        self,
        text: str,
        action: str,
        show_popup: bool,
        defer_popup: bool = False,
        reuse_popup: bool = False,
    ) -> None:
        if not text:
            self.status_var.set("请输入文本")
            return

        mode = self.mode_var.get().strip().lower()
        self._translate_request_seq += 1
        req_id = self._translate_request_seq
        self._active_translate_request_seq = req_id
        self._translate_in_flight = True
        self._active_translation_text = ""
        self._set_result_text("")
        self.status_var.set("翻译中...")
        self.logger.info(
            "Translate request action=%s mode=%s chars=%d popup=%s defer_popup=%s reuse_popup=%s",
            action,
            mode,
            len(text),
            show_popup,
            defer_popup,
            reuse_popup,
        )

        if show_popup and not defer_popup:
            self._active_popup_request_seq = req_id
            if reuse_popup and self.result_popup and self.result_popup.winfo_exists():
                self._update_result_popup(text, "", pending=True)
                self._active_popup_position = (self.result_popup.winfo_x(), self.result_popup.winfo_y())
            else:
                self._active_popup_position = self._show_result_popup(
                    text,
                    "",
                    pending=True,
                    popup_pos=self._active_popup_position,
                )
        else:
            self._active_popup_request_seq = 0
            if self.result_popup_pending:
                self._destroy_result_popup()

        def worker() -> None:
            chunks: list[str] = []
            buffered_chars = 0
            last_flush_at = time.monotonic()
            flush_chars = self._translate_flush_chars
            flush_interval_sec = self._translate_flush_interval_sec

            def flush_chunks() -> None:
                nonlocal buffered_chars, last_flush_at
                if not chunks:
                    return
                delta = "".join(chunks)
                chunks.clear()
                buffered_chars = 0
                last_flush_at = time.monotonic()
                self.event_queue.put(
                    (
                        "translate_chunk",
                        {
                            "text": text,
                            "delta": delta,
                            "mode": mode,
                            "action": action,
                            "show_popup": show_popup,
                            "defer_popup": defer_popup,
                            "req_id": req_id,
                        },
                    )
                )

            def on_delta(chunk: str) -> None:
                nonlocal buffered_chars, last_flush_at
                if not chunk:
                    return
                chunks.append(chunk)
                buffered_chars += len(chunk)
                now = time.monotonic()
                if buffered_chars >= flush_chars or "\n" in chunk or (now - last_flush_at) >= flush_interval_sec:
                    flush_chunks()

            try:
                if action == "polish":
                    result = self.service.polish_stream(text, mode, on_delta)
                else:
                    result = self.service.translate_stream(text, mode, on_delta)
                flush_chunks()
                self.event_queue.put(
                    (
                        "translate_done",
                        {
                            "text": text,
                            "result": result,
                            "mode": mode,
                            "action": action,
                            "show_popup": show_popup,
                            "defer_popup": defer_popup,
                            "req_id": req_id,
                        },
                    )
                )
            except Exception as exc:
                flush_chunks()
                self.logger.exception("Translate worker failed action=%s mode=%s", action, mode)
                self.event_queue.put(
                    (
                        "translate_error",
                        {
                            "message": str(exc),
                            "text": text,
                            "show_popup": show_popup,
                            "defer_popup": defer_popup,
                            "req_id": req_id,
                        },
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def _poll_input_fallback(self) -> None:
        VK_LBUTTON = 0x01
        VK_CONTROL = 0x11
        VK_MENU = 0x12  # ALT
        VK_T = 0x54
        VK_S = 0x53
        VK_H = 0x48

        try:
            now = time.time()
            point = POINT()
            has_pos = bool(windll.user32.GetCursorPos(byref(point)))
            cursor_x = int(point.x) if has_pos else 0
            cursor_y = int(point.y) if has_pos else 0
            cursor_pos = (cursor_x, cursor_y)
            prev_pos = self._cursor_last_pos
            prev_move_at = self._cursor_last_move_at
            cursor_step = 0
            cursor_dt = 0.0
            if prev_pos is not None and cursor_pos != prev_pos:
                cursor_step = abs(cursor_x - int(prev_pos[0])) + abs(cursor_y - int(prev_pos[1]))
                cursor_dt = max(0.001, now - prev_move_at)

            if cursor_pos != self._cursor_last_pos:
                self._cursor_last_pos = cursor_pos
                self._cursor_last_move_at = now

            self._maybe_hide_selection_icon_by_cursor(cursor_pos)
            self._maybe_hide_result_popup_by_cursor(cursor_pos, cursor_step, cursor_dt)

            lbtn_down = bool(windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            ctrl_down = bool(windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            alt_down = bool(windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            t_down = bool(windll.user32.GetAsyncKeyState(VK_T) & 0x8000)
            s_down = bool(windll.user32.GetAsyncKeyState(VK_S) & 0x8000)
            h_down = bool(windll.user32.GetAsyncKeyState(VK_H) & 0x8000)

            if not self._fb_last_lbtn_down and lbtn_down:
                self._fb_lbtn_down_pos = (cursor_x, cursor_y)

            # Left button release -> likely selection gesture.
            if self._fb_last_lbtn_down and not lbtn_down:
                if (
                    self.config.interaction.selection_enabled
                    and not self._screenshot_capture_active
                    and not self._is_own_window_foreground()
                ):
                    down = self._fb_lbtn_down_pos
                    dx = abs(cursor_x - down[0]) if down else 0
                    dy = abs(cursor_y - down[1]) if down else 0
                    moved = dx + dy

                    if now - self._fb_last_lbtn_up_at <= 0.35:
                        self._fb_lbtn_click_count += 1
                    else:
                        self._fb_lbtn_click_count = 1
                    self._fb_last_lbtn_up_at = now

                    # Trigger only on drag-select or double-click word-select.
                    if moved >= 3 or self._fb_lbtn_click_count >= 2:
                        down_x = int(down[0]) if down else int(cursor_x)
                        down_y = int(down[1]) if down else int(cursor_y)
                        self._emit_selection_candidate(
                            {
                                "x": int(cursor_x),
                                "y": int(cursor_y),
                                "down_x": down_x,
                                "down_y": down_y,
                                "ts": now,
                            }
                        )
                        self._fb_lbtn_click_count = 0
                self._fb_lbtn_down_pos = None

            # Double Ctrl detection.
            if ctrl_down and not self._fb_last_ctrl_down:
                if now - self._fb_last_ctrl_tap_at <= 0.35:
                    if (
                        self.config.interaction.selection_enabled
                        and self.config.interaction.selection_trigger_mode == "double_ctrl"
                        and self.pending_selection_at > 0
                        and now - self.pending_selection_at <= 12
                    ):
                        self._translate_pending_selection("划词翻译")
                self._fb_last_ctrl_tap_at = now

            # Ctrl+Alt+T hotkey fallback.
            combo_t = ctrl_down and alt_down and t_down
            if combo_t and not self._fb_last_combo_t:
                if self.config.interaction.selection_hotkey_enabled:
                    self._translate_selected("划词翻译", silent_if_empty=True)
            self._fb_last_combo_t = combo_t

            # Ctrl+Alt+S hotkey fallback.
            combo_s = ctrl_down and alt_down and s_down
            if combo_s and not self._fb_last_combo_s:
                if self.config.interaction.screenshot_hotkey_enabled:
                    self._start_screenshot_capture(show_popup=True)
            self._fb_last_combo_s = combo_s

            # Ctrl+Alt+H hotkey fallback.
            combo_h = ctrl_down and alt_down and h_down
            if combo_h and not self._fb_last_combo_h:
                self._toggle_window_visibility()
            self._fb_last_combo_h = combo_h

            self._fb_last_lbtn_down = lbtn_down
            self._fb_last_ctrl_down = ctrl_down

        except Exception:
            now = time.time()
            if now - self._last_poll_input_error_log_at >= 5.0:
                self._last_poll_input_error_log_at = now
                self.logger.exception("Error in input fallback polling")

        self.root.after(12, self._poll_input_fallback)

    def _poll_events(self) -> None:
        processed = 0
        while True:
            try:
                event, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1

            if event == "status":
                self.status_var.set(str(payload))

            elif event == "translate_selection":
                if self.config.interaction.selection_hotkey_enabled:
                    self._translate_selected("划词翻译", silent_if_empty=True)

            elif event == "double_ctrl_selection":
                if (
                    self.config.interaction.selection_enabled
                    and self.config.interaction.selection_trigger_mode == "double_ctrl"
                    and self.pending_selection_at > 0
                    and time.time() - self.pending_selection_at <= 12
                ):
                    self._translate_pending_selection("划词翻译")

            elif event == "selection_mouse_up":
                if (
                    self.config.interaction.selection_enabled
                    and not self._screenshot_capture_active
                    and not self._is_own_window_foreground()
                ):
                    self._emit_selection_candidate(payload)

            elif event == "screenshot_translate":
                if self.config.interaction.screenshot_hotkey_enabled:
                    self._start_screenshot_capture(show_popup=True)

            elif event == "toggle_window":
                self._toggle_window_visibility()

            elif event == "ai_test_done":
                data = payload
                assert isinstance(data, dict)
                ok = bool(data.get("ok"))
                message = str(data.get("message", ""))
                self.status_var.set(message)
                if ok:
                    messagebox.showinfo("AI 测试", message)
                else:
                    self.logger.error("AI test failed: %s", message)
                    messagebox.showerror("AI 测试", message)

            elif event == "screenshot_ocr_done":
                data = payload
                assert isinstance(data, dict)
                req_id = int(data.get("req_id", 0))
                if req_id != self._active_screenshot_request_seq:
                    continue

                text = str(data.get("text", "")).strip()
                show_popup = bool(data.get("show_popup"))
                if not text:
                    continue

                self.source_text.delete("1.0", "end")
                self.source_text.insert("1.0", text)
                self.status_var.set("截图翻译中...")
                self.logger.info("Screenshot OCR done chars=%d popup=%s", len(text), show_popup)
                if self._screenshot_popup_position is not None:
                    self._active_popup_position = self._screenshot_popup_position
                self._start_translate(text, action="截图翻译", show_popup=show_popup, reuse_popup=show_popup)

            elif event == "screenshot_ocr_error":
                data = payload if isinstance(payload, dict) else {"message": str(payload), "show_popup": False, "req_id": 0}
                req_id = int(data.get("req_id", 0))
                if req_id != self._active_screenshot_request_seq:
                    continue

                message = str(data.get("message", "截图处理失败"))
                show_popup = bool(data.get("show_popup"))
                self.status_var.set(message)
                self.logger.error("Screenshot OCR error req_id=%s: %s", req_id, message)
                if show_popup:
                    if self.result_popup and self.result_popup.winfo_exists():
                        self._update_result_popup("截图翻译失败", message, pending=False)
                    else:
                        self._show_result_popup("截图翻译失败", message, popup_pos=self._screenshot_popup_position)
                else:
                    messagebox.showerror("截图翻译失败", message)

            elif event == "translate_chunk":
                data = payload
                assert isinstance(data, dict)
                req_id = int(data.get("req_id", 0))
                if req_id != self._active_translate_request_seq:
                    continue

                delta = str(data.get("delta", ""))
                if not delta:
                    continue

                src = str(data.get("text", ""))
                action_name = str(data.get("action", "translate"))
                mode_name = str(data.get("mode", self.mode_var.get()))
                self._active_translation_text += delta
                self._append_result_text(delta)
                self.status_var.set(f"生成中 · {mode_name} · {action_name}")

                if (
                    bool(data.get("show_popup"))
                    and not bool(data.get("defer_popup"))
                    and req_id == self._active_popup_request_seq
                ):
                    self._append_result_popup_text(src, delta, pending=True)

            elif event == "translate_done":
                data = payload
                assert isinstance(data, dict)
                src = str(data["text"])
                res = str(data["result"])
                req_id = int(data.get("req_id", 0))
                if req_id != self._active_translate_request_seq:
                    continue
                self._translate_in_flight = False

                previous_result = self._active_translation_text
                self._active_translation_text = res
                if res != previous_result:
                    self._set_result_text(res)
                action_name = str(data["action"])
                mode_name = str(data["mode"])
                self.history.add_record(action_name, mode_name, src, res)
                self._load_history()
                self.status_var.set(f"完成 · {mode_name} · {action_name}")
                self.logger.info(
                    "Translate done action=%s mode=%s src_chars=%d result_chars=%d",
                    action_name,
                    mode_name,
                    len(src),
                    len(res),
                )

                if (
                    action_name == "划词翻译"
                    and self.config.interaction.selection_enabled
                    and self.config.interaction.selection_trigger_mode == "icon"
                ):
                    self.pending_selection_text = src.strip()
                    self.pending_selection_at = time.time()
                    if self.selection_capture_payload is None:
                        x, y = self._get_cursor_position()
                        self.selection_capture_payload = {
                            "x": x,
                            "y": y,
                            "down_x": x,
                            "down_y": y,
                            "ts": self.pending_selection_at,
                        }
                    self._schedule_selection_icon()

                if bool(data["show_popup"]):
                    if bool(data.get("defer_popup")):
                        self._show_result_popup(src, res, popup_pos=self._active_popup_position)
                    elif req_id == self._active_popup_request_seq:
                        if self.result_popup and self.result_popup.winfo_exists():
                            self._update_result_popup(src, res, pending=False)
                        else:
                            self._show_result_popup(src, res, popup_pos=self._active_popup_position)

            elif event == "translate_error":
                data = payload if isinstance(payload, dict) else {"message": str(payload), "show_popup": False, "req_id": 0}
                message = str(data.get("message", "未知错误"))
                req_id = int(data.get("req_id", 0))
                if req_id != self._active_translate_request_seq:
                    continue
                self._translate_in_flight = False

                src = str(data.get("text", ""))
                self.status_var.set(message)
                self.logger.error("Translate error: %s", message)
                if bool(data.get("show_popup")):
                    popup_text = self._active_translation_text
                    if popup_text:
                        popup_text = f"{popup_text}\n\n[生成中断] {message}"
                    else:
                        popup_text = message
                    if bool(data.get("defer_popup")):
                        self._show_result_popup(src or "翻译失败", popup_text, popup_pos=self._active_popup_position)
                    elif req_id == self._active_popup_request_seq:
                        if self.result_popup and self.result_popup.winfo_exists():
                            self._update_result_popup(src or "翻译失败", popup_text, pending=False)
                        else:
                            self._show_result_popup(src or "翻译失败", popup_text, popup_pos=self._active_popup_position)
                else:
                    messagebox.showerror("翻译失败", message)

        next_delay_ms = self._event_poll_active_ms if (self._translate_in_flight or processed) else self._event_poll_idle_ms
        self.root.after(next_delay_ms, self._poll_events)

    def _emit_selection_candidate(self, payload) -> None:
        now = time.time()
        if now - self._last_selection_trigger_at < 0.08:
            return
        self._last_selection_trigger_at = now
        self._handle_selection_mouse_up(payload)

    def _handle_selection_mouse_up(self, payload) -> None:
        self.pending_selection_text = ""
        self.pending_selection_at = time.time()

        data = payload if isinstance(payload, dict) else {}
        up_x = int(data.get("x", 0))
        up_y = int(data.get("y", 0))
        down_x = int(data.get("down_x", up_x))
        down_y = int(data.get("down_y", up_y))
        self.selection_capture_payload = {
            "x": up_x,
            "y": up_y,
            "down_x": down_x,
            "down_y": down_y,
            "ts": self.pending_selection_at,
        }

        mode = self.config.interaction.selection_trigger_mode
        if mode == "icon":
            self._schedule_selection_icon()
            self.status_var.set("已捕获选区，静置后显示图标")
        else:
            self._hide_selection_icon()
            self.status_var.set("已捕获选区，双击 Ctrl 触发翻译")

    def _schedule_selection_icon(self) -> None:
        if self.selection_capture_job:
            try:
                self.root.after_cancel(self.selection_capture_job)
            except Exception:
                pass
            self.selection_capture_job = None

        self.selection_capture_retry = 0
        delay = max(300, min(5000, int(self.config.interaction.selection_icon_delay_ms or 1500)))
        self.selection_capture_job = self.root.after(delay, self._maybe_show_selection_icon)

    def _selection_icon_cancel_distance(self) -> int:
        sensitivity = str(self.config.interaction.selection_icon_cancel_sensitivity or "medium").strip().lower()
        if sensitivity == "high":
            return 50
        if sensitivity == "low":
            return 150
        return 90

    def _selection_icon_trigger_distance(self) -> int:
        # Cursor must stay on selected text area (with small tolerance).
        return 14

    def _is_cursor_on_selection_anchor(self, cursor_pos: tuple[int, int]) -> bool:
        if self.selection_capture_payload is None:
            return False

        up_x = int(self.selection_capture_payload.get("x", 0))
        up_y = int(self.selection_capture_payload.get("y", 0))
        down_x = int(self.selection_capture_payload.get("down_x", up_x))
        down_y = int(self.selection_capture_payload.get("down_y", up_y))

        left = min(down_x, up_x)
        right = max(down_x, up_x)
        top = min(down_y, up_y)
        bottom = max(down_y, up_y)

        min_width = 52
        min_height = 26
        width = right - left
        height = bottom - top
        if width < min_width:
            pad = (min_width - width) // 2
            left -= pad
            right += (min_width - width - pad)
        if height < min_height:
            pad = (min_height - height) // 2
            top -= pad
            bottom += (min_height - height - pad)

        tolerance = self._selection_icon_trigger_distance()
        left -= tolerance
        right += tolerance
        top -= tolerance
        bottom += tolerance

        cx = int(cursor_pos[0])
        cy = int(cursor_pos[1])
        return left <= cx <= right and top <= cy <= bottom

    def _maybe_hide_selection_icon_by_cursor(self, cursor_pos: tuple[int, int]) -> None:
        if self.selection_icon_popup is None or not self.selection_icon_popup.winfo_exists():
            return
        if self.selection_icon_anchor_pos is None:
            return

        dx = abs(int(cursor_pos[0]) - int(self.selection_icon_anchor_pos[0]))
        dy = abs(int(cursor_pos[1]) - int(self.selection_icon_anchor_pos[1]))
        if dx + dy >= self._selection_icon_cancel_distance():
            self._hide_selection_icon()

    def _is_cursor_inside_result_popup(self, cursor_pos: tuple[int, int]) -> bool:
        if self.result_popup is None or not self.result_popup.winfo_exists():
            return False
        cx, cy = int(cursor_pos[0]), int(cursor_pos[1])
        x = int(self.result_popup.winfo_x())
        y = int(self.result_popup.winfo_y())
        w = max(1, int(self.result_popup.winfo_width()))
        h = max(1, int(self.result_popup.winfo_height()))
        return x <= cx <= (x + w) and y <= cy <= (y + h)

    def _cursor_distance_to_result_popup(self, cursor_pos: tuple[int, int]) -> int:
        if self.result_popup is None or not self.result_popup.winfo_exists():
            return 0

        cx, cy = int(cursor_pos[0]), int(cursor_pos[1])
        x = int(self.result_popup.winfo_x())
        y = int(self.result_popup.winfo_y())
        right = x + max(1, int(self.result_popup.winfo_width()))
        bottom = y + max(1, int(self.result_popup.winfo_height()))

        if cx < x:
            dx = x - cx
        elif cx > right:
            dx = cx - right
        else:
            dx = 0

        if cy < y:
            dy = y - cy
        elif cy > bottom:
            dy = cy - bottom
        else:
            dy = 0

        return dx + dy

    def _maybe_hide_result_popup_by_cursor(self, cursor_pos: tuple[int, int], cursor_step: int, cursor_dt: float) -> None:
        if self.result_popup is None or not self.result_popup.winfo_exists():
            return
        if self.result_popup_pinned or self.result_popup_dragging or self.result_popup_hovering or self.result_popup_pending:
            return
        if self._is_cursor_inside_result_popup(cursor_pos):
            return
        if cursor_step <= 0 or cursor_dt <= 0:
            return

        distance = self._cursor_distance_to_result_popup(cursor_pos)
        speed = cursor_step / max(cursor_dt, 0.001)
        quick_slide = cursor_step >= 36 and cursor_dt <= 0.06 and distance >= 90
        quickly_away = speed >= 1100 and distance >= 70

        if quick_slide or quickly_away:
            self._destroy_result_popup()

    def _get_cursor_position(self) -> tuple[int, int]:
        point = POINT()
        try:
            if bool(windll.user32.GetCursorPos(byref(point))):
                return int(point.x), int(point.y)
        except Exception:
            pass
        if self._cursor_last_pos is not None:
            return self._cursor_last_pos
        return (0, 0)

    def _maybe_show_selection_icon(self) -> None:
        self.selection_capture_job = None
        if not self.config.interaction.selection_enabled:
            return
        if self.config.interaction.selection_trigger_mode != "icon":
            return
        if self.selection_capture_payload is None:
            return
        if not self.pending_selection_at or time.time() - self.pending_selection_at > 18:
            return

        delay_sec = max(0.3, min(5.0, self.config.interaction.selection_icon_delay_ms / 1000.0))
        if time.time() - self._cursor_last_move_at < delay_sec:
            self.selection_capture_job = self.root.after(120, self._maybe_show_selection_icon)
            return

        cursor_pos = self._get_cursor_position()
        if not self._is_cursor_on_selection_anchor(cursor_pos):
            self.selection_capture_job = self.root.after(120, self._maybe_show_selection_icon)
            return

        probe = self.pending_selection_text.strip()
        if not probe:
            # Probe only when cache is empty.
            probe = self._capture_selected_text(wait_sec=0.08, payload=self.selection_capture_payload).strip()
        if not probe:
            if self.selection_capture_retry < 8:
                self.selection_capture_retry += 1
                self.selection_capture_job = self.root.after(120, self._maybe_show_selection_icon)
            return

        self.selection_capture_retry = 0
        self.pending_selection_text = probe

        self._show_selection_icon(int(cursor_pos[0]), int(cursor_pos[1]))

    def _show_selection_icon(self, x: int, y: int) -> None:
        self._hide_selection_icon()

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.attributes("-alpha", 0.97)
        popup.configure(bg="#d9ecff")
        popup_left = int(x) + 12
        popup_top = int(y) + 12
        popup.geometry(f"32x32+{popup_left}+{popup_top}")
        popup.update_idletasks()
        self._make_window_no_activate(popup)
        self.selection_icon_anchor_pos = (popup_left + 16, popup_top + 16)

        icon = tk.Label(
            popup,
            text="译",
            bg="#d9ecff",
            fg="#0f2a44",
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        )
        icon.pack(fill="both", expand=True)

        trigger_state = {"done": False}

        def trigger_translate(_event=None):
            if trigger_state["done"]:
                return "break"
            trigger_state["done"] = True
            self._translate_pending_selection("划词翻译")
            return "break"

        icon_trigger = str(self.config.interaction.selection_icon_trigger or "click").strip().lower()
        icon.bind("<Button-1>", trigger_translate)
        if icon_trigger == "hover":
            icon.bind("<Enter>", trigger_translate)

        self.selection_icon_popup = popup
        popup.after(4200, self._hide_selection_icon)

    def _make_window_no_activate(self, window: tk.Toplevel) -> None:
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        try:
            hwnd = int(window.winfo_id())
            exstyle = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            exstyle |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle)
            windll.user32.SetWindowPos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
        except Exception:
            pass

    def _hide_selection_icon(self) -> None:
        if self.selection_capture_job:
            try:
                self.root.after_cancel(self.selection_capture_job)
            except Exception:
                pass
            self.selection_capture_job = None
        if self.selection_icon_popup and self.selection_icon_popup.winfo_exists():
            self.selection_icon_popup.destroy()
        self.selection_icon_popup = None
        self.selection_icon_anchor_pos = None

    def _translate_pending_selection(self, action: str) -> None:
        self._hide_selection_icon()
        if not self.pending_selection_at or time.time() - self.pending_selection_at > 18:
            message = "未检测到最近选区，请重新划词"
            self.status_var.set(message)
            self._show_result_popup("翻译失败", message)
            return

        text = self.pending_selection_text.strip()
        if not text:
            text = self._capture_selected_text(wait_sec=0.12, payload=self.selection_capture_payload).strip()

        if not text:
            message = f"{action}: 未检测到可翻译文本"
            self.status_var.set(message)
            self._show_result_popup("翻译失败", message)
            return

        self.pending_selection_text = text
        self.pending_selection_at = time.time()
        if self.selection_capture_payload is None:
            x, y = self._get_cursor_position()
            self.selection_capture_payload = {"x": x, "y": y, "down_x": x, "down_y": y, "ts": self.pending_selection_at}

        self._start_translate(text, action=action, show_popup=True)

    def _is_own_window_foreground(self) -> bool:
        try:
            fg = int(windll.user32.GetForegroundWindow())
            own_windows = {int(self.root.winfo_id())}
            if self.selection_icon_popup and self.selection_icon_popup.winfo_exists():
                own_windows.add(int(self.selection_icon_popup.winfo_id()))
            if self.result_popup and self.result_popup.winfo_exists():
                own_windows.add(int(self.result_popup.winfo_id()))
            if (
                self._screenshot_overlay is not None
                and self._screenshot_overlay.window is not None
                and self._screenshot_overlay.window.winfo_exists()
            ):
                own_windows.add(int(self._screenshot_overlay.window.winfo_id()))
            return fg in own_windows
        except Exception:
            return False

    def _translate_selected(self, reason: str, silent_if_empty: bool = False) -> None:
        text = self._capture_selected_text(wait_sec=0.1, payload=self.selection_capture_payload)
        if not text:
            if not silent_if_empty:
                self.status_var.set(f"{reason}: 未检测到可翻译文本")
            return
        self._start_translate(text, action=reason, show_popup=True)

    def _capture_selected_text(
        self,
        wait_sec: float = 0.1,
        allow_unchanged: bool = False,
        payload: dict | None = None,
    ) -> str:
        result = self.selection_capture.capture(
            self._capture_selection_by_ctrl_c,
            payload=payload,
            wait_sec=wait_sec,
            allow_unchanged=allow_unchanged,
        )
        if result.source == "uia" and result.has_text():
            self.logger.info(
                "Selection capture success | scheme=uia | strategy=%s | control=%s | reason=%s | detail=%s",
                result.strategy or "n/a",
                result.control_summary(),
                result.reason or "n/a",
                result.detail or "n/a",
            )
        elif result.source == "clipboard" and result.has_text():
            self.logger.info(
                "Selection capture success | scheme=clipboard | strategy=%s | control=%s | uia_reason=%s | uia_detail=%s | clipboard_reason=%s | clipboard_detail=%s",
                result.strategy or "n/a",
                result.control_summary(),
                result.uia_reason or result.fallback_reason or "n/a",
                result.uia_detail or result.fallback_detail or "n/a",
                result.clipboard_reason or "n/a",
                result.clipboard_detail or result.detail or "n/a",
            )
        else:
            self.logger.warning(
                "Selection capture failed | %s",
                result.diagnostics_summary(),
            )
        return result.text.strip()

    def _copy_selection_once(self, wait_sec: float = 0.1) -> str:
        VK_CONTROL = 0x11
        VK_C = 0x43
        KEYEVENTF_KEYUP = 0x0002

        windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
        windll.user32.keybd_event(VK_C, 0, 0, 0)
        windll.user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
        windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(wait_sec)

        selected = self._get_clipboard_text(raw=True)
        if not selected:
            return ""
        return selected.strip()

    def _capture_selection_by_ctrl_c(
        self,
        wait_sec: float = 0.1,
        allow_unchanged: bool = False,
    ) -> ClipboardCaptureResult:
        # Safety-first path: avoid low-level clipboard memory operations.
        # Some clipboard formats are not HGLOBAL and may crash when force-read.
        backup_text = self._get_clipboard_text(raw=True)
        try:
            seq_before = int(windll.user32.GetClipboardSequenceNumber())
        except Exception:
            seq_before = -1

        selected = ""
        selected_reason = ""
        attempt_notes: list[str] = []
        attempts = 0
        last_error = ""
        saw_nonempty_copy = False
        saw_unchanged_copy = False

        for attempt_idx in range(3):
            attempts = attempt_idx + 1
            try:
                copied = self._copy_selection_once(wait_sec=wait_sec)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                attempt_notes.append(f"attempt={attempts}:copy-exception")
                break

            try:
                seq_after = int(windll.user32.GetClipboardSequenceNumber())
            except Exception:
                seq_after = -1

            copied = copied.strip()
            seq_changed = seq_before >= 0 and seq_after >= 0 and seq_after != seq_before

            if copied:
                saw_nonempty_copy = True

            if copied and seq_after != seq_before:
                selected = copied
                selected_reason = "clipboard-seq-changed"
                attempt_notes.append(f"attempt={attempts}:captured-seq-changed len={len(selected)}")
                break

            if allow_unchanged and copied and backup_text is not None and copied != backup_text.strip():
                selected = copied
                selected_reason = "clipboard-differs-from-backup"
                attempt_notes.append(f"attempt={attempts}:captured-different-from-backup len={len(selected)}")
                if selected:
                    break

            if copied and not seq_changed:
                saw_unchanged_copy = True
                attempt_notes.append(f"attempt={attempts}:copied-but-seq-unchanged len={len(copied)}")
            else:
                attempt_notes.append(f"attempt={attempts}:empty")
            time.sleep(0.04)

        restored = True
        if backup_text is not None:
            restored = self._restore_clipboard_text(backup_text)
            if not restored:
                now = time.time()
                if now - self._last_clipboard_restore_error_log_at >= 5.0:
                    self._last_clipboard_restore_error_log_at = now
                    self.logger.warning(
                        "Selection capture clipboard restore failed | attempts=%s | detail=%s",
                        attempts,
                        "; ".join(attempt_notes) or "n/a",
                    )

        detail_parts = []
        if attempt_notes:
            detail_parts.append("; ".join(attempt_notes))
        if last_error:
            detail_parts.append(f"error={last_error}")
        if backup_text is None:
            detail_parts.append("backup=none")
        if not restored:
            detail_parts.append("restore=failed")
        detail = " | ".join(detail_parts) if detail_parts else "no-attempts"

        if selected:
            return ClipboardCaptureResult(
                text=selected,
                reason=selected_reason or "clipboard-captured",
                detail=detail,
                attempts=attempts,
                restore_ok=restored,
            )

        failure_reason = "clipboard-empty"
        if last_error:
            failure_reason = "clipboard-copy-exception"
        elif saw_unchanged_copy:
            failure_reason = "clipboard-unchanged"
        elif saw_nonempty_copy:
            failure_reason = "clipboard-copy-without-seq-change"

        return ClipboardCaptureResult(
            text="",
            reason=failure_reason,
            detail=detail,
            attempts=attempts,
            restore_ok=restored,
        )

    def _restore_clipboard_text(self, text: str | None) -> bool:
        if text is None:
            return False

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        payload = text.encode("utf-16-le") + b"\x00\x00"

        for _ in range(12):
            opened = False
            try:
                opened = bool(windll.user32.OpenClipboard(0))
            except Exception:
                opened = False

            if not opened:
                time.sleep(0.02)
                continue

            try:
                if not bool(windll.user32.EmptyClipboard()):
                    continue

                hmem = int(windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, c_size_t(len(payload))))
                if hmem == 0:
                    continue

                ptr = int(windll.kernel32.GlobalLock(hmem))
                if ptr == 0:
                    windll.kernel32.GlobalFree(hmem)
                    continue

                try:
                    memmove(ptr, payload, len(payload))
                finally:
                    windll.kernel32.GlobalUnlock(hmem)

                if bool(windll.user32.SetClipboardData(CF_UNICODETEXT, hmem)):
                    return True

                windll.kernel32.GlobalFree(hmem)
            except Exception:
                pass
            finally:
                try:
                    windll.user32.CloseClipboard()
                except Exception:
                    pass

            time.sleep(0.02)

        # Fallback path
        for _ in range(4):
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                self.root.update()
                return True
            except tk.TclError:
                time.sleep(0.03)

        return False

    def _set_text_widget_content(self, widget: tk.Text, text: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        if text:
            widget.insert("1.0", text)
        widget.config(state="disabled")

    def _append_text_widget_content(self, widget: tk.Text, text: str) -> None:
        if not text:
            return
        widget.config(state="normal")
        widget.insert("end", text)
        widget.config(state="disabled")

    def _append_result_text(self, text: str) -> None:
        if not text:
            return
        self._append_text_widget_content(self.result_text, text)
        self.result_text.see("end")

    def _schedule_result_popup_auto_close(self, delay_ms: int = 4200) -> None:
        if self.result_popup is None or not self.result_popup.winfo_exists():
            return
        if self.result_popup_auto_close_job:
            try:
                self.result_popup.after_cancel(self.result_popup_auto_close_job)
            except Exception:
                pass
        self.result_popup_auto_close_job = self.result_popup.after(delay_ms, self._auto_close_result_popup_if_unpinned)

    def _auto_close_result_popup_if_unpinned(self) -> None:
        if self.result_popup is None or not self.result_popup.winfo_exists():
            self.result_popup_auto_close_job = None
            return
        if self.result_popup_pinned:
            self.result_popup_auto_close_job = None
            return
        if self.result_popup_pending or self.result_popup_dragging or self.result_popup_hovering:
            self.result_popup_auto_close_job = self.result_popup.after(300, self._auto_close_result_popup_if_unpinned)
            return
        self.result_popup_auto_close_job = None
        self._destroy_result_popup()

    def _update_result_popup(self, source_text: str, result_text: str, pending: bool | None = None) -> None:
        if self.result_popup is None or not self.result_popup.winfo_exists():
            return

        self._result_popup_source_text = source_text
        self._result_popup_result_text = result_text
        if pending is not None:
            self.result_popup_pending = bool(pending)

        if self._result_popup_title_var is not None:
            self._result_popup_title_var.set("正在翻译..." if self.result_popup_pending else "翻译结果")

        if self._result_popup_copy_button is not None and self._result_popup_copy_button.winfo_exists():
            copy_state = "disabled" if self.result_popup_pending or not result_text.strip() else "normal"
            self._result_popup_copy_button.config(state=copy_state)

        if self._result_popup_content_widget is not None and self._result_popup_content_widget.winfo_exists():
            display_text = result_text if result_text else ("正在翻译，请稍候..." if self.result_popup_pending else "")
            self._set_text_widget_content(self._result_popup_content_widget, display_text)
            self._result_popup_content_widget.see("end")

        if self.result_popup_pending:
            if self.result_popup_auto_close_job and self.result_popup:
                try:
                    self.result_popup.after_cancel(self.result_popup_auto_close_job)
                except Exception:
                    pass
                self.result_popup_auto_close_job = None
            return

        if not self.result_popup_pinned and not self.result_popup_dragging and not self.result_popup_hovering:
            self._schedule_result_popup_auto_close(4200)

    def _append_result_popup_text(self, source_text: str, delta: str, pending: bool | None = None) -> None:
        if self.result_popup is None or not self.result_popup.winfo_exists() or not delta:
            return

        previous_result = self._result_popup_result_text
        self._result_popup_source_text = source_text
        self._result_popup_result_text = f"{previous_result}{delta}"
        if pending is not None:
            self.result_popup_pending = bool(pending)

        if self._result_popup_title_var is not None:
            self._result_popup_title_var.set("正在翻译..." if self.result_popup_pending else "翻译结果")

        if self._result_popup_copy_button is not None and self._result_popup_copy_button.winfo_exists():
            copy_state = "disabled" if self.result_popup_pending or not self._result_popup_result_text.strip() else "normal"
            self._result_popup_copy_button.config(state=copy_state)

        if self._result_popup_content_widget is not None and self._result_popup_content_widget.winfo_exists():
            if previous_result:
                self._append_text_widget_content(self._result_popup_content_widget, delta)
            else:
                self._set_text_widget_content(self._result_popup_content_widget, self._result_popup_result_text)
            self._result_popup_content_widget.see("end")

        if self.result_popup_pending:
            if self.result_popup_auto_close_job and self.result_popup:
                try:
                    self.result_popup.after_cancel(self.result_popup_auto_close_job)
                except Exception:
                    pass
                self.result_popup_auto_close_job = None
            return

        if not self.result_popup_pinned and not self.result_popup_dragging and not self.result_popup_hovering:
            self._schedule_result_popup_auto_close(4200)

    def _show_result_popup(
        self,
        source_text: str,
        result_text: str,
        pending: bool = False,
        popup_pos: tuple[int, int] | None = None,
    ) -> tuple[int, int]:
        self._destroy_result_popup(reset_pin=False)

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.attributes("-alpha", 0.97)
        popup.configure(bg="#eaf2fb")

        if popup_pos is None:
            point = POINT()
            windll.user32.GetCursorPos(byref(point))
            popup_x = point.x + 12
            popup_y = point.y + 16
        else:
            popup_x, popup_y = int(popup_pos[0]), int(popup_pos[1])

        popup.geometry(f"380x180+{popup_x}+{popup_y}")

        container = tk.Frame(popup, bg="#eaf2fb")
        container.pack(fill="both", expand=True, padx=8, pady=8)

        top = tk.Frame(container, bg="#eaf2fb")
        top.pack(fill="x")
        title_var = tk.StringVar(value=("正在翻译..." if pending else "翻译结果"))
        title_label = tk.Label(top, textvariable=title_var, bg="#eaf2fb", fg="#12314e", font=("Microsoft YaHei UI", 9, "bold"))
        title_label.pack(side="left")

        pin_text = tk.StringVar(value=("取消固定" if self.result_popup_pinned else "固定"))

        def toggle_pin() -> None:
            self.result_popup_pinned = not self.result_popup_pinned
            pin_text.set("取消固定" if self.result_popup_pinned else "固定")
            self.status_var.set("气泡已固定" if self.result_popup_pinned else "气泡已取消固定")
            if self.result_popup_pinned:
                if self.result_popup_auto_close_job and self.result_popup:
                    try:
                        self.result_popup.after_cancel(self.result_popup_auto_close_job)
                    except Exception:
                        pass
                    self.result_popup_auto_close_job = None
            elif not self.result_popup_pending:
                self._schedule_result_popup_auto_close(4200)

        def copy_result() -> None:
            self._set_clipboard_text(self._result_popup_result_text)
            self.status_var.set("已复制翻译")

        tk.Button(top, textvariable=pin_text, command=toggle_pin, bg="#d5e7fb", relief="flat").pack(side="right")
        copy_button = tk.Button(top, text="复制", command=copy_result, bg="#d5e7fb", relief="flat")
        copy_button.pack(side="right", padx=(0, 4))
        tk.Button(top, text="×", command=self._destroy_result_popup, bg="#d5e7fb", relief="flat", width=2).pack(side="right", padx=(0, 4))

        content = tk.Text(
            container,
            height=6,
            wrap="word",
            bg="#eaf2fb",
            fg="#122335",
            font=("Microsoft YaHei UI", 10),
            relief="flat",
            highlightthickness=0,
            padx=6,
            pady=6,
            state="disabled",
            cursor="hand2",
        )
        content.pack(fill="both", expand=True)

        def open_detail(_event=None) -> None:
            if self.result_popup_pending:
                return
            self._show_window()
            self.source_text.delete("1.0", "end")
            self.source_text.insert("1.0", self._result_popup_source_text)
            self._set_result_text(self._result_popup_result_text)
            self.status_var.set("已展开完整翻译详情")
            self._destroy_result_popup()

        content.bind("<Button-1>", open_detail)
        container.bind("<Button-1>", open_detail)

        drag = {"dx": 0, "dy": 0}

        def start_drag(event):
            self.result_popup_dragging = True
            cancel_leave_close()
            if self.result_popup and self.result_popup_auto_close_job:
                try:
                    self.result_popup.after_cancel(self.result_popup_auto_close_job)
                except Exception:
                    pass
                self.result_popup_auto_close_job = None
            drag["dx"] = event.x_root - popup.winfo_x()
            drag["dy"] = event.y_root - popup.winfo_y()

        def on_drag(event):
            x = event.x_root - drag["dx"]
            y = event.y_root - drag["dy"]
            popup.geometry(f"+{x}+{y}")
            self._active_popup_position = (x, y)

        def stop_drag(_event=None):
            self.result_popup_dragging = False
            if self.result_popup and self.result_popup.winfo_exists():
                self._active_popup_position = (self.result_popup.winfo_x(), self.result_popup.winfo_y())
                if not self.result_popup_pending and not self.result_popup_pinned:
                    self._schedule_result_popup_auto_close(4200)

        top.bind("<ButtonPress-1>", start_drag)
        top.bind("<B1-Motion>", on_drag)
        top.bind("<ButtonRelease-1>", stop_drag)
        title_label.bind("<ButtonPress-1>", start_drag)
        title_label.bind("<B1-Motion>", on_drag)
        title_label.bind("<ButtonRelease-1>", stop_drag)
        popup.bind("<ButtonRelease-1>", stop_drag)

        def schedule_leave_close(_event=None) -> None:
            self.result_popup_hovering = False
            if (
                self.result_popup is None
                or not self.result_popup.winfo_exists()
                or self.result_popup_pinned
                or self.result_popup_dragging
            ):
                return
            if self.result_popup_leave_job:
                try:
                    self.result_popup.after_cancel(self.result_popup_leave_job)
                except Exception:
                    pass
            self.result_popup_leave_job = self.result_popup.after(650, self._auto_close_result_popup_if_unpinned)

        def cancel_leave_close(_event=None) -> None:
            self.result_popup_hovering = True
            if self.result_popup is None or not self.result_popup.winfo_exists():
                return
            if self.result_popup_leave_job:
                try:
                    self.result_popup.after_cancel(self.result_popup_leave_job)
                except Exception:
                    pass
                self.result_popup_leave_job = None

        popup.bind("<Leave>", schedule_leave_close)
        popup.bind("<Enter>", cancel_leave_close)

        self.result_popup = popup
        self.result_popup_leave_job = None
        self.result_popup_auto_close_job = None
        self.result_popup_dragging = False
        self.result_popup_hovering = False
        self.result_popup_pending = bool(pending)
        self._active_popup_position = (popup_x, popup_y)
        self._result_popup_title_var = title_var
        self._result_popup_content_widget = content
        self._result_popup_copy_button = copy_button
        self._update_result_popup(source_text, result_text, pending=pending)

        return popup_x, popup_y

    def _destroy_result_popup(self, reset_pin: bool = True) -> None:
        if self.result_popup and self.result_popup.winfo_exists():
            try:
                if self.result_popup_leave_job:
                    self.result_popup.after_cancel(self.result_popup_leave_job)
                if self.result_popup_auto_close_job:
                    self.result_popup.after_cancel(self.result_popup_auto_close_job)
            except Exception:
                pass
            self.result_popup.destroy()
        self.result_popup = None
        self.result_popup_leave_job = None
        self.result_popup_auto_close_job = None
        self.result_popup_dragging = False
        self.result_popup_hovering = False
        self.result_popup_pending = False
        self._result_popup_source_text = ""
        self._result_popup_result_text = ""
        self._result_popup_title_var = None
        self._result_popup_content_widget = None
        self._result_popup_copy_button = None
        if reset_pin:
            self.result_popup_pinned = False
            self._active_popup_position = None

    def _set_result_text(self, text: str) -> None:
        self._set_text_widget_content(self.result_text, text)

    def _load_history(self) -> None:
        self.history_rows = self.history.list_recent(limit=120)
        self.history_list.delete(0, "end")
        for item in self.history_rows:
            src = item["source_text"].replace("\n", " ")
            if len(src) > 22:
                src = f"{src[:22]}..."
            line = f"{item['created_at']} [{item['mode']}] {src}"
            self.history_list.insert("end", line)

    def _on_history_selected(self, _event) -> None:
        if not self.history_list.curselection():
            return
        idx = self.history_list.curselection()[0]
        if idx < 0 or idx >= len(self.history_rows):
            return
        row = self.history_rows[idx]
        self.source_text.delete("1.0", "end")
        self.source_text.insert("1.0", row["source_text"])
        self._set_result_text(row["result_text"])
        self.status_var.set(f"历史记录 · {row['created_at']} · {row['action']}")

    def _clear_history(self) -> None:
        if not messagebox.askyesno("确认", "确定清空本地历史记录吗？"):
            return
        self.history.clear()
        self._load_history()
        self.status_var.set("历史记录已清空")

    def _get_clipboard_text(self, raw: bool = False) -> str | None:
        try:
            text = self.root.clipboard_get()
            if raw:
                return text
            return text.strip()
        except tk.TclError:
            return None if raw else ""

    def _set_clipboard_text(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
        except tk.TclError:
            pass

    def _on_hook_event(self, event: str, payload) -> None:
        self.event_queue.put((event, payload))

    def _on_tk_callback_exception(self, exc, val, tb) -> None:
        self.logger.critical("Tk callback exception", exc_info=(exc, val, tb))

    def _on_close(self) -> None:
        self.logger.info("Shutting down UI")
        if self._screenshot_overlay is not None:
            try:
                self._screenshot_overlay.cancel()
            except Exception:
                pass
            self._screenshot_overlay = None
        self._screenshot_capture_active = False
        self._destroy_result_popup()
        self._hide_selection_icon()
        self.mouse_hooks.stop()
        self.hotkeys.stop()
        self.root.destroy()









































































































