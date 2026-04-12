from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
import winreg
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import webview

from src.app_logging import APP_RUNTIME_DIR, APP_USER_RUNTIME_DIR, LEGACY_USER_DIR, get_logger
from src.branding import APP_TITLE, ensure_icon_ico, icon_data_url, icon_path
from src.config import AppConfig, ConfigStore, SelectionAppProfile
from src.hotkeys import HotkeyManager, normalize_shortcut, parse_shortcut
from src.mouse_hooks import MouseHookManager
from src.native_screenshot_overlay import NativeScreenshotOverlay
from src.ocr import ScreenshotOCRService
from src.selection_capture import ClipboardCaptureResult, SelectionCaptureResult, SelectionCaptureService
from src.screenshot import MIN_CAPTURE_SIZE, ScreenRegion, capture_screen_region
from src.storage import HistoryStore
from src.translator import TranslationService
from src.native_icon_overlay import NativeIconOverlay
from src.tray_icon import TrayIconManager
from src.ui_webview.api import WindowApi
from src.ui_webview.backend import (
    capture_virtual_screen,
    copy_selection_once,
    get_foreground_process_name,
    get_clipboard_text,
    get_cursor_position,
    get_system_dpi_scale,
    get_system_theme_preference,
    get_virtual_screen_bounds,
    set_clipboard_text,
)
from src.ui_webview.bridge import FrontendBridge
from src.ui_webview.state import BubbleState, ScreenshotSession, SelectionCandidate, SelectionFlowState, UiState


class WordPackWebviewApp:
    MAIN_WINDOW_BG = "#c4c6ca"
    BUBBLE_WINDOW_BG = "#c4c6ca"
    DARK_WINDOW_BG = "#1b2028"
    TRAY_WINDOW_BG = "#f8fafd"
    TRAY_WINDOW_DARK_BG = "#1b2028"
    MAIN_WIDTH = 468
    MAIN_HEIGHT = 680
    MAIN_MIN_HEIGHT = 360
    MAIN_COMPACT_HEIGHT = 430
    MAIN_STARTUP_Y_SHIFT = 24
    BUBBLE_WIDTH = 408
    BUBBLE_HEIGHT = 272
    ICON_WIDTH = 34
    ICON_HEIGHT = 34
    TRAY_MENU_WIDTH = 286
    TRAY_MENU_HEIGHT = 338
    TRAY_SHOW_DELAY_MS = 28
    TRAY_BLUR_GUARD_MS = 320
    AI_PROBE_INTERVAL_SEC = 3600
    AI_STARTUP_CACHE_MAX_AGE_SEC = 86400
    WEBVIEW2_RUNTIME_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    WEBVIEW2_DOWNLOAD_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/"
    STARTUP_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    STARTUP_RUN_VALUE_NAME = "WordPack"

    def __init__(self) -> None:
        if getattr(sys, "frozen", False):
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
            exe_dir = Path(sys.executable).resolve().parent
            self.base_dir = bundle_root
            legacy_data_candidates = [
                APP_USER_RUNTIME_DIR,
                LEGACY_USER_DIR / "data",
                exe_dir.parent / "data",
                exe_dir / "data",
            ]
        else:
            self.base_dir = Path(__file__).resolve().parent.parent.parent
            legacy_data_candidates = [LEGACY_USER_DIR / "data", self.base_dir / "data"]
        self.logger = get_logger(__name__)
        self.lock = threading.RLock()
        self.data_dir = self._resolve_data_dir(legacy_data_candidates)

        self.config_store = ConfigStore(self.data_dir / "config.json")
        self.config: AppConfig = self.config_store.load()
        self._ensure_startup_launch_state()

        self.history = HistoryStore(self.data_dir / "history.db")
        self._prune_history_by_policy()
        self.service = TranslationService(self.get_config, data_dir=self.data_dir)
        self.ocr_service = ScreenshotOCRService(self.get_config)
        self.selection_capture = SelectionCaptureService()
        self.bridge = FrontendBridge(self.logger)
        self._app_icon_url = icon_data_url()
        self._app_icon_ico = ensure_icon_ico()
        self.frontend_dir = self._resolve_frontend_dir()
        self.webview_storage_dir = self._resolve_webview_storage_dir()

        self.ui_state = UiState(
            status=self._initial_status(),
            translation_mode=self.config.translation_mode,
            theme_mode=self._resolved_theme_mode(),
            direction=self._direction_label(),
            history=self.get_history_rows(),
        )

        self.hotkeys = HotkeyManager(self._on_hook_event, self._hotkey_map)
        self.mouse_hooks = MouseHookManager(self._on_hook_event, logger=self.logger)

        self.main_window = None
        self.bubble_window = None
        self.icon_window = None
        self.tray_window = None
        self.window_apis: dict[str, WindowApi] = {}
        self._native_icon_overlay = NativeIconOverlay(
            icon_path=icon_path("app-icon.png"),
            logger=self.logger,
            on_click=lambda: self.trigger_selection_translate(),
            window_size=self.ICON_WIDTH,
            icon_size=18,
        )
        self._native_screenshot_overlay = NativeScreenshotOverlay(
            logger=self.logger,
            on_selection=self._on_native_screenshot_selection,
            on_cancel=self._on_native_screenshot_cancel,
        )
        tray_icon = self._app_icon_ico or icon_path("app-icon.ico")
        self.tray_icon = TrayIconManager(
            title=APP_TITLE,
            icon_path=str(tray_icon),
            on_action=self._on_tray_action,
            logger=self.logger,
        )

        # Keep main window hidden until frontend explicitly reports ready,
        # preventing first-paint white/black flash from WebView2 default surface.
        self.hidden = True
        self._main_startup_revealed = False
        self._shutting_down = False
        self._translate_request_seq = 0
        self._active_translate_request_seq = 0
        self._active_translation_text = ""
        self._active_translate_cancel: threading.Event | None = None
        self._active_translate_shows_bubble = False
        self._candidate_request_seq = 0
        self._active_candidate_request_seq = 0
        ai_cached = self._load_ai_status_cache()
        self._ai_available = bool(ai_cached.get("available", False))
        self._ai_available_checked = bool(ai_cached.get("checked", False))
        self._ai_probe_inflight = False
        self._ai_availability_message = str(ai_cached.get("message", ""))
        self._ai_availability_checked_at = float(ai_cached.get("checkedAt", 0.0) or 0.0)
        self._ai_probe_thread: threading.Thread | None = None
        self._bubble_state = BubbleState(mode=self.config.translation_mode)
        self._selection_candidate = SelectionCandidate()
        self._selection_flow = SelectionFlowState()
        self._selection_last_fingerprint = ""
        self._selection_last_fingerprint_at = 0.0
        self._selection_icon_timer: threading.Timer | None = None
        self._selection_icon_hide_timer: threading.Timer | None = None
        self._selection_icon_anchor_pos: tuple[int, int] | None = None
        self._selection_icon_shown_at = 0.0
        self._selection_icon_retry = 0
        self._bubble_hide_timer: threading.Timer | None = None
        self._selection_icon_hover_armed = False
        self._selection_hover_entered_at = 0.0
        self._selection_hover_last_cursor: tuple[int, int] | None = None
        self._selection_hover_last_at = 0.0
        self._bubble_fast_close_pending_at = 0.0
        self._bubble_fast_close_pending_distance = 0
        self._bubble_last_fast_closed_at = 0.0
        self._bubble_last_fast_closed_snapshot: dict[str, Any] | None = None
        self._screenshot_session: ScreenshotSession | None = None
        self._screenshot_session_seq = 0
        self._screenshot_background_path: Path | None = None
        self._screenshot_cancel_last_at = 0.0
        self._screenshot_starting = False
        self._last_screenshot_trigger_at = 0.0
        self._screenshot_ocr_request_seq = 0
        self._screenshot_ocr_suppress_bubble_until_seq = 0
        self._selection_suppressed_until = 0.0
        self._last_window_interaction_at = 0.0
        self._input_poll_stop = threading.Event()
        self._input_poll_thread: threading.Thread | None = None
        self._cursor_last_pos: tuple[int, int] | None = None
        self._cursor_last_move_at = 0.0
        self._fb_last_lbtn_down = False
        self._fb_last_rbtn_down = False
        self._fb_lbtn_down_pos: tuple[int, int] | None = None
        self._fb_lbtn_down_at = 0.0
        self._fb_last_lbtn_up_at = 0.0
        self._fb_last_lbtn_up_pos: tuple[int, int] | None = None
        self._fb_lbtn_click_count = 0
        self._fb_last_ctrl_down = False
        self._fb_last_alt_down = False
        self._fb_last_shift_down = False
        self._fb_ctrl_combo_used = False
        self._fb_alt_combo_used = False
        self._fb_shift_combo_used = False
        self._fb_last_escape_down = False
        self._fb_last_ctrl_tap_at = 0.0
        self._fb_last_alt_tap_at = 0.0
        self._fb_last_shift_tap_at = 0.0
        self._fb_last_combo_s = False
        self._last_selection_trigger_at = 0.0
        self._last_selection_translate_trigger_at = 0.0
        self._last_poll_input_error_log_at = 0.0
        self._dpi_scale_cache = 1.0
        self._dpi_scale_cached_at = 0.0
        self._main_window_geometry_before_zoom: tuple[int, int, int, int] | None = None
        self._hide_main_after_zoom_close = False
        self._main_compact = True
        self._webview2_hint_shown = False
        self._webview_started = False
        self._selection_runtime_warmup_started = False
        self._selection_runtime_warmup_ok = False
        self._selection_runtime_warmup_done = threading.Event()
        self._tray_window_ready = False
        self._tray_window_shape_applied = False
        self._tray_window_popup_flags_applied = False
        self._tray_window_created_at = 0.0
        self._pending_tray_anchor: dict[str, int] | None = None
        self._tray_show_timer: threading.Timer | None = None
        self._tray_show_ticket = 0

    def _resolve_data_dir(self, legacy_candidates: list[Path]) -> Path:
        def ensure_writable_dir(path: Path) -> bool:
            try:
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ".write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return True
            except Exception:
                return False

        def first_legacy_dir() -> Path:
            for item in legacy_candidates:
                try:
                    candidate = Path(item)
                    if ensure_writable_dir(candidate):
                        return candidate
                except Exception:
                    continue
            if ensure_writable_dir(APP_USER_RUNTIME_DIR):
                return APP_USER_RUNTIME_DIR
            fallback = self.base_dir / "data"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

        override_raw = str(os.environ.get("WORDPACK_DATA_DIR") or "").strip()
        if override_raw:
            override = Path(override_raw).expanduser()
            if ensure_writable_dir(override):
                return override
            self.logger.warning("WORDPACK_DATA_DIR not writable, fallback to default strategy: %s", override)

        shared = APP_RUNTIME_DIR
        if not ensure_writable_dir(shared):
            self.logger.warning("Shared data dir unavailable, fallback to legacy: %s", shared)
            return first_legacy_dir()
        if (shared / "config.json").exists() or (shared / "history.db").exists():
            return shared

        for legacy in legacy_candidates:
            try:
                if not legacy.exists():
                    continue
                has_payload = (legacy / "config.json").exists() or (legacy / "history.db").exists()
                if not has_payload:
                    continue
                for name in ("config.json", "history.db"):
                    src = legacy / name
                    dst = shared / name
                    if src.exists() and not dst.exists():
                        shutil.copy2(src, dst)
                legacy_webview = legacy / "webview"
                shared_webview = shared / "webview"
                if legacy_webview.exists() and not shared_webview.exists():
                    shutil.copytree(legacy_webview, shared_webview, dirs_exist_ok=True)
                return shared
            except Exception:
                self.logger.exception("Failed migrating legacy data dir: %s", legacy)
        try:
            probe = shared / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return shared
        except Exception as exc:
            self.logger.warning("Shared data dir not writable (%s), fallback to legacy: %s", exc, shared)
            return first_legacy_dir()

    def _resolve_webview_storage_dir(self) -> Path:
        candidates = [self.data_dir / "webview"]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return candidate
            except Exception:
                continue
        return self.data_dir

    def _startup_run_command(self) -> str:
        def quote(path: Path | str) -> str:
            value = str(path)
            return f"\"{value.replace('\"', '\"\"')}\""

        if getattr(sys, "frozen", False):
            return quote(Path(sys.executable).resolve())

        py_exe = quote(Path(sys.executable).resolve())
        argv0 = Path(sys.argv[0]).resolve() if sys.argv else (self.base_dir / "app.py")
        if argv0.exists() and argv0.suffix.lower() in {".py", ".pyw"}:
            return f"{py_exe} {quote(argv0)}"
        fallback = self.base_dir / "app.py"
        if fallback.exists():
            return f"{py_exe} {quote(fallback.resolve())}"
        return py_exe

    def _is_startup_enabled_in_system(self) -> bool:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self.STARTUP_RUN_KEY_PATH,
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, self.STARTUP_RUN_VALUE_NAME)
                return bool(str(value or "").strip())
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def _set_startup_enabled_in_system(self, enabled: bool) -> bool:
        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                self.STARTUP_RUN_KEY_PATH,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                if enabled:
                    winreg.SetValueEx(
                        key,
                        self.STARTUP_RUN_VALUE_NAME,
                        0,
                        winreg.REG_SZ,
                        self._startup_run_command(),
                    )
                else:
                    try:
                        winreg.DeleteValue(key, self.STARTUP_RUN_VALUE_NAME)
                    except FileNotFoundError:
                        pass
            return True
        except Exception:
            self.logger.exception("Failed to update startup launch setting enabled=%s", bool(enabled))
            return False

    def _apply_startup_launch_enabled(self, enabled: bool) -> tuple[bool, bool]:
        desired = bool(enabled)
        changed = self._set_startup_enabled_in_system(desired)
        actual = self._is_startup_enabled_in_system()
        with self.lock:
            self.config.interaction.startup_launch_enabled = bool(actual)
        return bool(changed and (actual == desired)), bool(actual)

    def _ensure_startup_launch_state(self) -> None:
        desired = bool(self.config.interaction.startup_launch_enabled)
        success, actual = self._apply_startup_launch_enabled(desired)
        if actual != desired:
            self.logger.warning(
                "Startup launch state differs from config (desired=%s actual=%s success=%s)",
                desired,
                actual,
                success,
            )
            self.config_store.save(self.config)

    def _has_webview2_runtime(self) -> bool:
        key_paths = [
            rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{self.WEBVIEW2_RUNTIME_CLIENT_ID}",
            rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{self.WEBVIEW2_RUNTIME_CLIENT_ID}",
        ]
        hives = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
        for hive in hives:
            for key_path in key_paths:
                try:
                    with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                        version, _ = winreg.QueryValueEx(key, "pv")
                        if str(version or "").strip():
                            return True
                except Exception:
                    continue
        return False

    def _show_webview2_required_hint(self) -> None:
        if self._webview2_hint_shown:
            return
        self._webview2_hint_shown = True
        message = (
            "检测到 WebView2 Runtime 不可用。\n"
            "WordPack 需要 WebView2 才能运行（不再回退到 mshtml）。\n\n"
            "请先安装后重试。\n\n"
            "下载地址：\n"
            f"{self.WEBVIEW2_DOWNLOAD_PAGE}\n\n"
            "（可选命令行安装）\n"
            "winget install --id Microsoft.EdgeWebView2Runtime --silent --accept-package-agreements --accept-source-agreements"
        )
        self.logger.error(message.replace("\n", " "))
        try:
            MB_ICONERROR = 0x00000010
            ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, MB_ICONERROR)
        except Exception:
            pass

    def _frontend_source_dir(self) -> Path:
        return self.base_dir / "src" / "ui_webview" / "frontend"

    def _resolve_frontend_dir(self) -> Path:
        source = self._frontend_source_dir()
        preferred_targets = [self.data_dir / "frontend"]

        for target in preferred_targets:
            try:
                target.mkdir(parents=True, exist_ok=True)
                probe = target / ".write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                if source.exists():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                return target
            except Exception as exc:
                self.logger.warning("Runtime frontend dir unavailable (%s), fallback target: %s", exc, target)

        if source.exists():
            self.logger.warning("Fallback to source frontend dir: %s", source)
            return source
        return self.data_dir

    def run(self) -> None:
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
        initial_main_height = self.MAIN_COMPACT_HEIGHT
        main_x, main_y = self._centered_position(self.MAIN_WIDTH, initial_main_height)
        bounds = get_virtual_screen_bounds()
        main_y = max(bounds.top + 8, int(main_y) - self.MAIN_STARTUP_Y_SHIFT)

        self.main_window = self._create_window(
            kind="main",
            title=APP_TITLE,
            width=self.MAIN_WIDTH,
            height=initial_main_height,
            min_size=(420, self.MAIN_MIN_HEIGHT),
            x=main_x,
            y=main_y,
            frameless=True,
            shadow=True,
            resizable=True,
            on_top=False,
            focus=True,
            transparent=False,
            hidden=True,
        )
        self.logger.info("Main webview window created")
        try:
            self.tray_icon.start()
        except Exception:
            self.logger.exception("Failed to start tray icon")
        if not self._has_webview2_runtime():
            self.logger.warning("WebView2 runtime not found. mshtml fallback is disabled because frontend requires modern JavaScript.")
            self._show_webview2_required_hint()
        try:
            webview.start(
                self._on_webview_started,
                http_server=True,
                debug=False,
                storage_path=str(self.webview_storage_dir),
                gui="edgechromium",
            )
        except Exception as exc:
            raw = str(exc or "")
            lowered = raw.lower()
            self.logger.exception("Failed to start pywebview with edgechromium")
            if "class not registered" in lowered or "没有注册类" in raw or "webview2" in lowered:
                self._show_webview2_required_hint()
                return
            raise

    def get_config(self) -> AppConfig:
        return self.config

    @staticmethod
    def _normalize_translation_mode(mode: str | None) -> str:
        value = str(mode or "dictionary").strip().lower()
        return value if value in {"dictionary", "ai"} else "dictionary"

    def _initial_status(self) -> str:
        if self._normalize_translation_mode(self.config.translation_mode) == "ai":
            return "已切换到 AI 模式"
        return self.service.dictionary_diagnostics()

    def _resolved_theme_mode(self, theme_mode: str | None = None) -> str:
        current = str(theme_mode if theme_mode is not None else self.config.theme_mode or "system").strip().lower()
        if current == "system":
            return get_system_theme_preference()
        return "dark" if current == "dark" else "light"

    def _frontend_url(self, view_name: str) -> str:
        base = (self.frontend_dir / "index.html").resolve().as_posix()
        rev = int(time.time())
        theme = self._resolved_theme_mode()
        return f"{base}?view={view_name}&rev={rev}&theme={theme}"

    def _window_background_color(self, kind: str) -> str:
        theme = self._resolved_theme_mode()
        if kind in {"main", "bubble"}:
            return self.DARK_WINDOW_BG if theme == "dark" else self.MAIN_WINDOW_BG
        if kind == "tray":
            return self.TRAY_WINDOW_DARK_BG if theme == "dark" else self.TRAY_WINDOW_BG
        if kind == "icon":
            return "#000000"
        return "#eef1ec"

    def _apply_window_background(self, window, color_hex: str) -> None:
        if window is None:
            return
        native = getattr(window, "native", None)
        if native is None:
            return
        try:
            from System import Action  # type: ignore[import-not-found]
            from System.Drawing import Color  # type: ignore[import-not-found]
        except ImportError:
            return

        def _parse_color(value: str):
            text = str(value or "").strip().lstrip("#")
            if len(text) != 6:
                return Color.Black
            try:
                r = int(text[0:2], 16)
                g = int(text[2:4], 16)
                b = int(text[4:6], 16)
            except Exception:
                return Color.Black
            return Color.FromArgb(r, g, b)

        target_color = _parse_color(color_hex)

        def apply() -> None:
            if getattr(native, "IsDisposed", False):
                return
            try:
                native.BackColor = target_color
            except Exception:
                self.logger.exception("Failed to set native window background")

        try:
            if getattr(native, "InvokeRequired", False):
                native.BeginInvoke(Action(apply))
            else:
                apply()
        except Exception:
            self.logger.exception("Failed to apply native window background")

    def _run_on_window_ui(
        self,
        window,
        task: Callable[[], None],
        *,
        wait: bool = True,
        timeout_sec: float = 2.0,
        log_prefix: str = "window-ui",
    ) -> None:
        if window is None:
            return
        native = getattr(window, "native", None)
        if native is None:
            task()
            return
        try:
            from System import Action  # type: ignore[import-not-found]
        except ImportError:
            task()
            return

        if not getattr(native, "InvokeRequired", False):
            task()
            return

        done = threading.Event()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                task()
            except Exception as exc:  # pragma: no cover - forwarded to caller
                errors.append(exc)
            finally:
                done.set()

        native.BeginInvoke(Action(run))
        if not wait:
            return
        if not done.wait(timeout=max(0.1, float(timeout_sec))):
            raise TimeoutError(f"{log_prefix}: ui invoke timed out")
        if errors:
            raise errors[0]

    def _apply_theme_backgrounds(self) -> None:
        main_bg = self._window_background_color("main")
        bubble_bg = self._window_background_color("bubble")
        tray_bg = self._window_background_color("tray")
        self._apply_window_background(self.main_window, main_bg)
        self._apply_window_background(self.bubble_window, bubble_bg)
        self._apply_window_background(self.tray_window, tray_bg)

    def _centered_position(self, width: int, height: int) -> tuple[int, int]:
        bounds = get_virtual_screen_bounds()
        x = bounds.left + max(0, (bounds.width - width) // 2)
        # Keep startup position slightly above geometric center so expanded panels
        # are less likely to overflow below the visible screen.
        center_y = bounds.top + max(0, (bounds.height - height) // 2)
        y_bias = min(120, max(56, height // 5))
        y = max(bounds.top + 8, center_y - y_bias)
        return x, y

    def _hotkey_map(self) -> dict[str, str]:
        screenshot_hotkey = ""
        if self.config.interaction.screenshot_enabled:
            screenshot_hotkey = normalize_shortcut(self.config.interaction.screenshot_hotkey)
        restore_hotkey = normalize_shortcut(str(getattr(self.config.interaction, "bubble_restore_hotkey", "") or ""))
        toggle_main_hotkey = normalize_shortcut(str(getattr(self.config.interaction, "main_toggle_hotkey", "") or ""))
        return {
            "screenshot_translate": screenshot_hotkey,
            "restore_bubble": restore_hotkey,
            "toggle_main_window": toggle_main_hotkey,
        }

    def _shortcut_descriptions(self) -> list[str]:
        screenshot = ""
        if self.config.interaction.screenshot_enabled:
            screenshot = normalize_shortcut(self.config.interaction.screenshot_hotkey)
        restore_bubble = normalize_shortcut(str(getattr(self.config.interaction, "bubble_restore_hotkey", "") or ""))
        toggle_main = normalize_shortcut(str(getattr(self.config.interaction, "main_toggle_hotkey", "") or ""))
        items: list[str] = []
        if screenshot:
            items.append(f"{screenshot} 截图翻译")
        if restore_bubble:
            items.append(f"{restore_bubble} 恢复最近关闭气泡")
        if toggle_main:
            items.append(f"{toggle_main} 显示/隐藏主窗口")
        return items

    def _current_main_geometry(self) -> tuple[int, int, int, int] | None:
        if self.main_window is None:
            return None
        try:
            width = int(self.main_window.width)
            height = int(self.main_window.height)
            x = int(self.main_window.x)
            y = int(self.main_window.y)
            return x, y, width, height
        except Exception:
            self.logger.exception("Failed to read main window geometry")
            return None

    def _apply_main_geometry(self, x: int, y: int, width: int, height: int) -> None:
        if self.main_window is None:
            return
        try:
            self.main_window.resize(int(width), int(height))
            self.main_window.move(int(x), int(y))
            self.main_window.show()
            self.hidden = False
        except Exception:
            self.logger.exception("Failed to apply main window geometry")

    def _animate_main_height(self, x: int, y: int, width: int, from_height: int, to_height: int) -> None:
        if self.main_window is None:
            return
        delta = int(to_height) - int(from_height)
        if abs(delta) < 20:
            self._apply_main_geometry(x, y, width, to_height)
            return
        try:
            # Apply initial geometry once, then animate with lightweight resize-only frames.
            self.main_window.move(int(x), int(y))
            self.main_window.resize(int(width), int(from_height))
            self.main_window.show()
            self.hidden = False
        except Exception:
            self.logger.exception("Failed to initialize main window resize animation")
            self._apply_main_geometry(x, y, width, to_height)
            return
        duration_sec = 0.22
        frame_interval_sec = 1 / 60
        start = time.perf_counter()
        end = start + duration_sec
        last_h = int(from_height)
        while True:
            now = time.perf_counter()
            if now >= end:
                break
            progress = max(0.0, min(1.0, (now - start) / duration_sec))
            eased = progress * progress * (3 - 2 * progress)
            h = int(round(from_height + (delta * eased)))
            if h != last_h:
                try:
                    self.main_window.resize(int(width), int(h))
                except Exception:
                    self.logger.exception("Failed during main window resize animation frame")
                    break
                last_h = h
            sleep_for = frame_interval_sec - (time.perf_counter() - now)
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._apply_main_geometry(x, y, width, to_height)

    def _create_window(
        self,
        *,
        kind: str,
        title: str,
        width: int,
        height: int,
        frameless: bool,
        shadow: bool,
        resizable: bool,
        on_top: bool,
        focus: bool,
        min_size: tuple[int, int] | None = None,
        x: int | None = None,
        y: int | None = None,
        transparent: bool = False,
        hidden: bool = False,
    ):
        api = WindowApi(self, kind)
        self.window_apis[kind] = api
        kwargs: dict[str, Any] = {
            "title": title,
            "url": self._frontend_url(kind),
            "js_api": api,
            "width": width,
            "height": height,
            "frameless": frameless,
            "shadow": shadow,
            "resizable": resizable,
            "on_top": on_top,
            "focus": focus,
            "easy_drag": False,
            "text_select": True,
            # IMPORTANT:
            # icon window must keep native background fully transparent,
            # otherwise a gray/black rectangular plate appears behind the floating icon.
            "background_color": self._window_background_color(kind),
            "transparent": transparent,
            "hidden": hidden,
        }
        if min_size is not None:
            kwargs["min_size"] = min_size
        if x is not None:
            kwargs["x"] = x
        if y is not None:
            kwargs["y"] = y

        window = webview.create_window(**kwargs)
        api.attach_window(window)
        self.bridge.register_window(kind, window)
        try:
            window.events.closed += lambda *_args: self._on_window_closed(kind)
        except Exception:
            self.logger.exception("Failed to attach close event for %s window", kind)
        try:
            window.events.moved += lambda x, y, _kind=kind: self._on_window_moved(_kind, x, y)
        except Exception:
            self.logger.exception("Failed to attach move event for %s window", kind)
        if kind == "main":
            self._apply_native_window_icon(window)
        return window

    def _branding_payload(self) -> dict[str, str]:
        return {
            "appIconUrl": self._app_icon_url,
            "bubbleIconUrl": self._app_icon_url,
        }

    def _apply_native_window_icon(self, window) -> None:
        if self._app_icon_ico is None:
            return

        icon_path = str(self._app_icon_ico)

        def worker() -> None:
            try:
                if not window.events.shown.wait(2.0):
                    return
                native = getattr(window, "native", None)
                if native is None:
                    return

                try:
                    from System import Action  # type: ignore[import-not-found]
                    from System.Drawing import Icon  # type: ignore[import-not-found]
                except ImportError:
                    # pythonnet/.NET runtime unavailable in current interpreter (e.g. static analysis env).
                    return

                icon = Icon(icon_path)

                def apply_icon() -> None:
                    if getattr(native, "IsDisposed", False):
                        return
                    native.Icon = icon

                if getattr(native, "InvokeRequired", False):
                    native.BeginInvoke(Action(apply_icon))
                else:
                    apply_icon()
            except Exception:
                self.logger.exception("Failed to apply native window icon")

        threading.Thread(target=worker, daemon=True).start()

    def _apply_icon_window_shape_fix(self, window) -> None:
        def worker() -> None:
            try:
                if not window.events.shown.wait(2.0):
                    return
                native = getattr(window, "native", None)
                if native is None:
                    return
                handle = getattr(native, "Handle", None)
                if handle is None:
                    return

                hwnd = self._native_handle_to_int(handle)
                if not hwnd:
                    return
                rect = wintypes.RECT()
                if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
                    return

                client_width = max(0, int(rect.right - rect.left))
                client_height = max(0, int(rect.bottom - rect.top))
                if client_width <= 0 or client_height <= 0:
                    return

                # Clip to an icon-centered circle to remove any rectangular background plate.
                diameter = max(18, min(26, int(min(client_width, client_height))))
                cx = client_width // 2
                cy = client_height // 2
                left = int(cx - (diameter // 2))
                top = int(cy - (diameter // 2))
                right = int(left + diameter)
                bottom = int(top + diameter)

                region = ctypes.windll.gdi32.CreateEllipticRgn(left, top, right + 1, bottom + 1)
                if not region:
                    return

                if not ctypes.windll.user32.SetWindowRgn(hwnd, region, True):
                    ctypes.windll.gdi32.DeleteObject(region)
            except Exception:
                self.logger.exception("Failed to apply icon window shape fix")

        threading.Thread(target=worker, daemon=True).start()

    def _apply_tray_window_shape_fix(self, window, *, wait: bool = False) -> None:
        def apply() -> None:
            native = getattr(window, "native", None)
            if native is None:
                return
            handle = getattr(native, "Handle", None)
            if handle is None:
                return

            hwnd = self._native_handle_to_int(handle)
            if not hwnd:
                return

            # Windows 10 and below: keep tray menu as square corners.
            if not self._is_windows_11_or_newer():
                try:
                    ctypes.windll.user32.SetWindowRgn(hwnd, None, True)
                except Exception:
                    pass
                return

            # Prefer DWM compositor rounded corners (anti-aliased) on
            # supported Windows versions. SetWindowRgn is a hard clip fallback.
            try:
                DWMWA_WINDOW_CORNER_PREFERENCE = 33
                DWMWCP_ROUND = 2
                preference = ctypes.c_int(DWMWCP_ROUND)
                dwm = ctypes.windll.dwmapi
                hr = int(
                    dwm.DwmSetWindowAttribute(
                        ctypes.c_void_p(hwnd),
                        ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
                        ctypes.byref(preference),
                        ctypes.sizeof(preference),
                    )
                )
                if hr == 0:
                    return
            except Exception:
                pass

            rect = wintypes.RECT()
            has_rect = bool(ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)))
            client_width = max(0, int(rect.right - rect.left)) if has_rect else 0
            client_height = max(0, int(rect.bottom - rect.top)) if has_rect else 0
            if client_width <= 0 or client_height <= 0:
                client_width = int(self.TRAY_MENU_WIDTH)
                client_height = int(self.TRAY_MENU_HEIGHT)
            if client_width <= 0 or client_height <= 0:
                return

            radius = max(16, min(28, int(min(client_width, client_height) // 6)))
            region = ctypes.windll.gdi32.CreateRoundRectRgn(
                0,
                0,
                client_width + 1,
                client_height + 1,
                radius,
                radius,
            )
            if not region:
                return
            if not ctypes.windll.user32.SetWindowRgn(hwnd, region, True):
                ctypes.windll.gdi32.DeleteObject(region)

        try:
            self._run_on_window_ui(
                window,
                apply,
                wait=wait,
                log_prefix="apply_tray_window_shape_fix",
            )
        except Exception:
            self.logger.exception("Failed to schedule tray window shape fix")

    def _apply_tray_window_popup_flags(self, window, *, wait: bool = False) -> None:
        # Keep custom tray window out of taskbar/Alt-Tab while preserving webview rendering.
        def apply() -> None:
            native = getattr(window, "native", None)
            if native is None:
                return

            try:
                native.ShowInTaskbar = False
            except Exception:
                pass
            try:
                native.ShowIcon = False
            except Exception:
                pass

            handle = getattr(native, "Handle", None)
            hwnd = self._native_handle_to_int(handle)
            if not hwnd:
                return

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            try:
                current = int(user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE))
                desired = (current | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
                if desired != current:
                    user32.SetWindowLongW(int(hwnd), GWL_EXSTYLE, int(desired))
                    user32.SetWindowPos(
                        int(hwnd),
                        None,
                        0,
                        0,
                        0,
                        0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                    )
            except Exception:
                self.logger.exception("Failed to apply tray window popup style flags")

        try:
            self._run_on_window_ui(
                window,
                apply,
                wait=wait,
                log_prefix="apply_tray_window_popup_flags",
            )
        except Exception:
            self.logger.exception("Failed to schedule tray window popup flags")

    def _set_main_window_taskbar_visibility(self, visible_on_taskbar: bool, *, wait: bool = False) -> None:
        window = self.main_window
        if window is None:
            return

        def apply() -> None:
            native = getattr(window, "native", None)
            if native is None:
                return

            try:
                native.ShowInTaskbar = bool(visible_on_taskbar)
            except Exception:
                pass
            try:
                native.ShowIcon = True
            except Exception:
                pass

            handle = getattr(native, "Handle", None)
            hwnd = self._native_handle_to_int(handle)
            if not hwnd:
                return

            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            try:
                current = int(user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE))
                if bool(visible_on_taskbar):
                    desired = (current | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
                else:
                    desired = (current | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
                if desired != current:
                    user32.SetWindowLongW(int(hwnd), GWL_EXSTYLE, int(desired))
                    user32.SetWindowPos(
                        int(hwnd),
                        None,
                        0,
                        0,
                        0,
                        0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                    )
            except Exception:
                self.logger.exception(
                    "Failed to switch main window taskbar visibility visible_on_taskbar=%s",
                    bool(visible_on_taskbar),
                )

            # Win11 may drop rounded-corner compositor preference after EXSTYLE
            # flips between TOOLWINDOW/APPWINDOW; restore it when main window
            # returns to taskbar-visible mode.
            if bool(visible_on_taskbar) and self._is_windows_11_or_newer():
                try:
                    DWMWA_WINDOW_CORNER_PREFERENCE = 33
                    DWMWCP_ROUND = 2
                    preference = ctypes.c_int(DWMWCP_ROUND)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        ctypes.c_void_p(hwnd),
                        ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
                        ctypes.byref(preference),
                        ctypes.sizeof(preference),
                    )
                except Exception:
                    pass

        try:
            self._run_on_window_ui(
                window,
                apply,
                wait=wait,
                log_prefix="set_main_window_taskbar_visibility",
            )
        except Exception:
            self.logger.exception(
                "Failed to schedule main window taskbar visibility switch visible_on_taskbar=%s",
                bool(visible_on_taskbar),
            )

    def _on_webview_started(self) -> None:
        self._webview_started = True
        self.logger.info("Starting background input services")
        try:
            if self.main_window is not None:
                current = self._current_main_geometry()
                if current is not None:
                    x, y, width, _height = current
                    try:
                        self.main_window.resize(int(width), int(self.MAIN_COMPACT_HEIGHT))
                        self.main_window.move(int(x), int(y))
                    except Exception:
                        self.logger.exception("Failed to prepare main window compact geometry on startup")
                    self._main_compact = True
        except Exception:
            self.logger.exception("Failed to prepare main window on startup")
        self._start_selection_runtime_warmup()
        self._start_input_polling()
        if self._should_probe_ai_on_startup():
            self._probe_ai_availability_async()
        self._start_ai_probe_loop()
        try:
            self.hotkeys.start()
            self.mouse_hooks.start()
        except Exception:
            self.logger.exception("Failed to start global hook services")
        self._start_bundled_model_import()

    def _start_selection_runtime_warmup(self) -> None:
        with self.lock:
            if self._selection_runtime_warmup_started:
                return
            self._selection_runtime_warmup_started = True
            self._selection_runtime_warmup_done.clear()

        def worker() -> None:
            ok = False
            try:
                ok = bool(self.selection_capture.warmup())
            except Exception:
                self.logger.exception("Selection runtime warmup failed")
            finally:
                with self.lock:
                    self._selection_runtime_warmup_ok = bool(ok)
                self._selection_runtime_warmup_done.set()
            self.logger.info("Selection runtime warmup done ok=%s", bool(ok))

        threading.Thread(
            target=worker,
            name="wordpack-runtime-warmup",
            daemon=True,
        ).start()

    def _wait_selection_runtime_warmup(self, timeout_sec: float) -> None:
        with self.lock:
            started = bool(self._selection_runtime_warmup_started)
            done = bool(self._selection_runtime_warmup_done.is_set())
        if not started or done:
            return
        wait_sec = max(0.0, float(timeout_sec))
        if wait_sec <= 0:
            return
        self._selection_runtime_warmup_done.wait(wait_sec)

    def _bundled_argos_model_manifest_path(self) -> Path:
        return self.data_dir / "bundled_argos_models_manifest.json"

    def _load_bundled_argos_model_manifest(self) -> dict[str, str]:
        path = self._bundled_argos_model_manifest_path()
        try:
            if not path.exists():
                return {}
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            result: dict[str, str] = {}
            for k, v in raw.items():
                key = str(k or "").strip()
                val = str(v or "").strip()
                if key and val:
                    result[key] = val
            return result
        except Exception:
            return {}

    def _save_bundled_argos_model_manifest(self, payload: dict[str, str]) -> None:
        path = self._bundled_argos_model_manifest_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            self.logger.exception("Failed to persist bundled argos model manifest")

    def _bundled_argos_model_dirs(self) -> list[Path]:
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            try:
                candidates.append(Path(sys.executable).resolve().parent / "argosmodel")
            except Exception:
                pass
        candidates.append(self.base_dir / "argosmodel")
        unique: list[Path] = []
        seen: set[str] = set()
        for item in candidates:
            try:
                resolved = item.resolve()
            except Exception:
                resolved = item
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            if resolved.exists() and resolved.is_dir():
                unique.append(resolved)
        return unique

    def _start_bundled_model_import(self) -> None:
        def worker() -> None:
            try:
                model_files: list[Path] = []
                for root in self._bundled_argos_model_dirs():
                    try:
                        model_files.extend(sorted(root.glob("*.argosmodel")))
                    except Exception:
                        continue
                if not model_files:
                    return

                old_manifest = self._load_bundled_argos_model_manifest()
                new_manifest: dict[str, str] = {}
                to_import: list[Path] = []

                for path in model_files:
                    try:
                        stat = path.stat()
                        stamp = f"{int(stat.st_size)}:{int(stat.st_mtime_ns)}"
                    except Exception:
                        continue
                    key = str(path.resolve()).lower()
                    new_manifest[key] = stamp
                    if old_manifest.get(key) != stamp:
                        to_import.append(path)

                if not to_import:
                    return

                imported = 0
                for path in to_import:
                    try:
                        self.service.import_dictionary_model(str(path))
                        imported += 1
                    except Exception:
                        self.logger.exception("Failed to import bundled argos model: %s", path)

                self._save_bundled_argos_model_manifest(new_manifest)

                if imported > 0:
                    self.service.dictionary_status(probe=True, force_refresh=True)
                    payload = self._config_event_payload()
                    self.bridge.send("main", "dictionary-models-updated", payload)
                    self.set_status(f"已导入预置词典模型 {imported} 个")
            except Exception:
                self.logger.exception("Bundled argos model import worker failed")

        threading.Thread(target=worker, daemon=True).start()

    def _ai_status_cache_path(self) -> Path:
        return self.data_dir / "ai_status_cache.json"

    def _load_ai_status_cache(self) -> dict[str, Any]:
        path = self._ai_status_cache_path()
        try:
            if not path.exists():
                return {}
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}
            return {
                "available": bool(raw.get("available", False)),
                "checked": bool(raw.get("checked", False)),
                "message": str(raw.get("message", "")),
                "checkedAt": float(raw.get("checkedAt", 0.0) or 0.0),
            }
        except Exception:
            return {}

    def _save_ai_status_cache(self) -> None:
        path = self._ai_status_cache_path()
        payload = self._ai_availability_payload()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            self.logger.exception("Failed to persist AI availability cache")

    def _should_probe_ai_on_startup(self) -> bool:
        if not self._ai_available_checked:
            return True
        checked_at = float(self._ai_availability_checked_at or 0.0)
        if checked_at <= 0:
            return True
        return (time.time() - checked_at) > float(self.AI_STARTUP_CACHE_MAX_AGE_SEC)

    def _ai_availability_payload(self) -> dict[str, Any]:
        with self.lock:
            return {
                "available": bool(self._ai_available),
                "checked": bool(self._ai_available_checked),
                "message": str(self._ai_availability_message or ""),
                "checkedAt": float(self._ai_availability_checked_at or 0.0),
            }

    def _emit_ai_availability(self) -> None:
        payload = self._ai_availability_payload()
        self.bridge.send("main", "ai-availability", payload)
        self.bridge.send("bubble", "ai-availability", payload)
        self.bridge.send("tray", "ai-availability", payload)

    def _probe_ai_availability_async(self) -> None:
        with self.lock:
            if self._ai_probe_inflight:
                return
            self._ai_probe_inflight = True

        def worker() -> None:
            try:
                ok, message = self.service.test_ai_connection()
                with self.lock:
                    self._ai_available = bool(ok)
                    self._ai_available_checked = True
                    self._ai_availability_message = str(message or "")
                    self._ai_availability_checked_at = time.time()
                self._save_ai_status_cache()
            finally:
                with self.lock:
                    self._ai_probe_inflight = False
                self._emit_ai_availability()

        threading.Thread(target=worker, daemon=True).start()

    def _start_ai_probe_loop(self) -> None:
        if self._ai_probe_thread is not None and self._ai_probe_thread.is_alive():
            return

        def worker() -> None:
            while not self._input_poll_stop.wait(self.AI_PROBE_INTERVAL_SEC):
                self._probe_ai_availability_async()

        self._ai_probe_thread = threading.Thread(target=worker, daemon=True)
        self._ai_probe_thread.start()

    def _on_window_closed(self, kind: str) -> None:
        self.logger.info("Window closed kind=%s", kind)
        self.bridge.unregister_window(kind)

        with self.lock:
            if kind == "main":
                self.main_window = None
            elif kind == "bubble":
                self.bubble_window = None
                self._bubble_state.visible = False
                self._bubble_fast_close_pending_at = 0.0
                self._bubble_fast_close_pending_distance = 0
                self._screenshot_ocr_suppress_bubble_until_seq = max(
                    int(self._screenshot_ocr_suppress_bubble_until_seq),
                    int(self._screenshot_ocr_request_seq),
                )
            elif kind == "icon":
                self.icon_window = None
                self._native_icon_overlay.hide()
            elif kind == "tray":
                self.tray_window = None
                self._tray_window_ready = False
                self._tray_window_shape_applied = False
                self._tray_window_popup_flags_applied = False
                self._tray_window_created_at = 0.0
                self._pending_tray_anchor = None
                self._cancel_tray_show_timer()

        if kind == "main" and not self._shutting_down:
            self.shutdown()

    def _on_window_moved(self, kind: str, x: int, y: int) -> None:
        if kind != "bubble":
            return

        with self.lock:
            if self.bubble_window is None:
                return
            self._bubble_state.x = int(x)
            self._bubble_state.y = int(y)

    def shutdown(self) -> None:
        if self._shutting_down:
            return

        self._shutting_down = True
        self.logger.info("Shutting down pywebview app")
        self._cancel_selection_icon_timer()
        self._cancel_selection_icon_hide_timer()
        self._cancel_bubble_hide_timer()
        self._native_icon_overlay.destroy()
        self._native_screenshot_overlay.destroy()
        self._set_global_cursor_crosshair(False)
        try:
            self.tray_icon.stop()
        except Exception:
            self.logger.exception("Failed to stop tray icon")
        self._input_poll_stop.set()
        self.mouse_hooks.stop()
        self.hotkeys.stop()
        if self._input_poll_thread is not None and self._input_poll_thread.is_alive():
            self._input_poll_thread.join(timeout=1.2)
        if self._ai_probe_thread is not None and self._ai_probe_thread.is_alive():
            self._ai_probe_thread.join(timeout=0.8)

        for kind in ("tray", "bubble", "icon", "main"):
            window = getattr(self, f"{kind}_window", None)
            if window is None:
                continue
            try:
                window.destroy()
            except Exception:
                self.logger.exception("Failed to destroy %s window", kind)
        self._clear_screenshot_background_image()

    def bootstrap_window(self, kind: str) -> dict[str, Any]:
        if kind == "main":
            return {
                "view": "main",
                "appTitle": APP_TITLE,
                "branding": self._branding_payload(),
                "ui": self.ui_state.to_payload(),
                "config": self._serialize_config(),
                "settings": self.get_settings_payload(),
                "themeMode": self._resolved_theme_mode(),
                "shortcuts": self._shortcut_descriptions(),
                "aiAvailability": self._ai_availability_payload(),
            }
        if kind == "bubble":
            return {
                "view": "bubble",
                "branding": self._branding_payload(),
                "themeMode": self._resolved_theme_mode(),
                "bubble": self._bubble_state.to_payload(),
                "aiAvailability": self._ai_availability_payload(),
            }
        if kind == "icon":
            return {
                "view": "icon",
                "branding": self._branding_payload(),
                "themeMode": self._resolved_theme_mode(),
                "mode": self.config.translation_mode,
                "triggerMode": self.config.interaction.selection_icon_trigger,
            }
        if kind == "tray":
            return {
                "view": "tray",
                "appTitle": APP_TITLE,
                "branding": self._branding_payload(),
                "themeMode": self._resolved_theme_mode(),
                "trayMenu": self._tray_menu_payload(),
                "aiAvailability": self._ai_availability_payload(),
            }
        return {"view": kind}

    def mark_window_ready(self, kind: str) -> None:
        self.bridge.mark_ready(kind)
        if kind == "tray":
            self._tray_window_ready = True
            if self.tray_window is not None:
                if not self._tray_window_popup_flags_applied:
                    self._apply_tray_window_popup_flags(self.tray_window, wait=True)
                    self._tray_window_popup_flags_applied = True
                if not self._tray_window_shape_applied:
                    self._apply_tray_window_shape_fix(self.tray_window, wait=True)
                    self._tray_window_shape_applied = True
            pending = dict(self._pending_tray_anchor or {})
            self._pending_tray_anchor = None
            if pending:
                self._show_tray_menu(pending)
            return
        if kind != "main" or self.main_window is None:
            return
        with self.lock:
            if self._main_startup_revealed:
                return
            self._main_startup_revealed = True

        def reveal_main() -> None:
            if self.main_window is None:
                return
            current = self._current_main_geometry()
            if current is not None:
                x, y, width, _height = current
                self._apply_main_geometry(x, y, width, self.MAIN_COMPACT_HEIGHT)
                self._main_compact = True
                return
            try:
                self.main_window.show()
                self.hidden = False
            except Exception:
                self.logger.exception("Failed to reveal main window on ready")

        try:
            self._run_on_window_ui(
                self.main_window,
                reveal_main,
                wait=False,
                log_prefix="mark_window_ready.reveal_main",
            )
        except Exception:
            self.logger.exception("Failed to schedule main window reveal after ready")

    def screenshot_presented(self, session_id: int | None = None) -> None:
        del session_id

    @staticmethod
    def _native_handle_to_int(handle) -> int | None:
        if handle is None:
            return None
        for method_name in ("ToInt64", "ToInt32"):
            method = getattr(handle, method_name, None)
            if method is None:
                continue
            try:
                value = int(method())
                if value:
                    return value
            except Exception:
                continue
        try:
            value = int(handle)
            return value or None
        except Exception:
            return None

    @staticmethod
    def _is_windows_11_or_newer() -> bool:
        try:
            version = sys.getwindowsversion()
            return int(version.major) >= 10 and int(version.build) >= 22000
        except Exception:
            return False

    @staticmethod
    def _set_global_cursor_crosshair(enabled: bool) -> None:
        try:
            cursor_id = 32515 if enabled else 32512  # IDC_CROSS / IDC_ARROW
            hcursor = ctypes.windll.user32.LoadCursorW(None, ctypes.c_void_p(cursor_id))
            if hcursor:
                ctypes.windll.user32.SetCursor(ctypes.c_void_p(hcursor))
                point = wintypes.POINT()
                if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                    ctypes.windll.user32.SetCursorPos(int(point.x), int(point.y))
        except Exception:
            pass

    def _suppress_selection_events(self, seconds: float = 0.65) -> None:
        until = time.time() + max(0.0, float(seconds))
        with self.lock:
            self._selection_suppressed_until = max(float(self._selection_suppressed_until or 0.0), until)

    def _selection_events_suppressed(self, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        with self.lock:
            return current < float(self._selection_suppressed_until or 0.0)

    def note_window_interaction(self, kind: str) -> None:
        del kind
        self._last_window_interaction_at = time.time()
        if self._native_icon_overlay.is_visible():
            self.close_window("icon")
        if self.tray_window is not None:
            self.close_window("tray")

    def set_main_compact(self, compact: bool, height: int | None = None) -> dict[str, Any]:
        compact = bool(compact)
        if self.main_window is None:
            self._main_compact = compact
            return {"ok": False, "compact": compact, "fromHeight": 0, "toHeight": 0, "durationMs": 0}
        if self._main_window_geometry_before_zoom is not None:
            self._main_compact = compact
            current = self._current_main_geometry()
            current_height = int(current[3]) if current else 0
            return {"ok": True, "compact": compact, "fromHeight": current_height, "toHeight": current_height, "durationMs": 0}

        current = self._current_main_geometry()
        if current is None:
            self._main_compact = compact
            return {"ok": False, "compact": compact, "fromHeight": 0, "toHeight": 0, "durationMs": 0}

        x, y, width, current_height = current
        target_height = self.MAIN_HEIGHT
        if compact:
            requested = self.MAIN_COMPACT_HEIGHT if height is None else int(height)
            target_height = max(self.MAIN_MIN_HEIGHT, min(self.MAIN_HEIGHT, requested))
        if compact == self._main_compact and int(current_height) == int(target_height):
            return {
                "ok": True,
                "compact": compact,
                "fromHeight": int(current_height),
                "toHeight": int(target_height),
                "durationMs": 0,
            }
        bounds = get_virtual_screen_bounds()
        max_x = max(bounds.left + 8, bounds.right - int(width) - 8)
        max_y = max(bounds.top + 8, bounds.bottom - int(target_height) - 8)
        clamped_x = min(max(bounds.left + 8, int(x)), int(max_x))
        clamped_y = min(max(bounds.top + 8, int(y)), int(max_y))
        self._apply_main_geometry(clamped_x, clamped_y, width, target_height)
        self._main_compact = compact
        return {
            "ok": True,
            "compact": compact,
            "fromHeight": int(current_height),
            "toHeight": int(target_height),
            "durationMs": 0,
        }

    def _serialize_config(self) -> dict[str, Any]:
        payload = asdict(self.config)
        payload["translation_mode"] = self._normalize_translation_mode(payload.get("translation_mode"))
        return payload

    @staticmethod
    def _history_source_kind_from_action(action: str) -> str:
        text = str(action or "").strip().lower()
        if "截图" in action or "screenshot" in text:
            return "screenshot"
        if "划词" in action or "selection" in text:
            return "selection"
        return "manual"

    def _prune_history_by_policy(self) -> int:
        days = int(getattr(self.config.history, "retention_days", 30) or 30)
        if days not in {7, 30, 90}:
            days = 30
            self.config.history.retention_days = days
        return self.history.prune_older_than(days)

    def get_history_rows(self) -> list[dict[str, Any]]:
        return self.history.list_recent(limit=120)

    def _history_filters_payload(self) -> dict[str, Any]:
        return {
            "directions": ["all", *self.history.distinct_directions()],
            "retentionDays": int(getattr(self.config.history, "retention_days", 30) or 30),
        }

    def list_history(self, payload: dict[str, Any]) -> dict[str, Any]:
        args = payload if isinstance(payload, dict) else {}
        tab = str(args.get("tab", "recent") or "recent").strip().lower()
        if tab not in {"recent", "favorites"}:
            tab = "recent"
        q = str(args.get("q", "") or "").strip()
        mode = str(args.get("mode", "all") or "all").strip().lower()
        if mode not in {"all", "dictionary", "ai"}:
            mode = "all"
        direction = str(args.get("direction", "all") or "all").strip()
        source_kind = str(args.get("source_kind", "all") or "all").strip().lower()
        if source_kind not in {"all", "manual", "selection", "screenshot"}:
            source_kind = "all"
        try:
            range_days = int(args.get("range_days", 0) or 0)
        except Exception:
            range_days = 0
        if range_days not in {0, 7, 30, 90}:
            range_days = 0
        try:
            limit = int(args.get("limit", 50) or 50)
        except Exception:
            limit = 50
        try:
            offset = int(args.get("offset", 0) or 0)
        except Exception:
            offset = 0

        result = self.history.list_records(
            tab=tab,
            q=q,
            mode=mode,
            direction=direction,
            source_kind=source_kind,
            range_days=range_days,
            limit=limit,
            offset=offset,
        )
        result["filters"] = self._history_filters_payload()
        return result

    def toggle_history_favorite(self, record_id: int, favorite: bool) -> dict[str, Any]:
        ok = self.history.set_favorite(record_id, favorite)
        return {"ok": bool(ok), "id": int(record_id), "favorite": bool(favorite)}

    def use_history_record(self, record_id: int) -> dict[str, Any]:
        ok = self.history.increment_use_count(record_id)
        return {"ok": bool(ok), "id": int(record_id)}

    def delete_history_record(self, record_id: int) -> dict[str, Any]:
        ok = self.history.delete_record(record_id)
        return {"ok": bool(ok), "id": int(record_id)}

    def get_settings_payload(self, probe_runtime: bool = False) -> dict[str, Any]:
        dictionary_status = self.service.dictionary_status(probe=probe_runtime)
        dictionary_ready = bool(dictionary_status.get("runtime_ready", False))
        return {
            "config": self._serialize_config(),
            "dictionaryModels": list(dictionary_status.get("models", [])) if dictionary_ready else [],
            "dictionaryRuntimeReady": dictionary_ready,
            "dictionaryRuntimeHint": str(dictionary_status.get("runtime_hint", "")),
            "dictionaryDiagnostics": str(dictionary_status.get("diagnostics", "")),
            "effectiveTheme": self._resolved_theme_mode(),
            "historyFilters": self._history_filters_payload(),
        }

    def set_translation_mode(self, mode: str) -> dict[str, Any]:
        with self.lock:
            value = self._normalize_translation_mode(mode)
            if value == "ai" and not self._ai_available:
                message = "AI 不可用，请先配置并测试连接"
                self.set_status(message)
                return {"ok": False, "message": message, **self._config_event_payload()}
            self.config.translation_mode = value
            self.ui_state.translation_mode = value
            self._bubble_state.mode = value
            if value != "ai":
                self._bubble_state.candidate_pending = False
                self._bubble_state.candidate_items = []
            self.config_store.save(self.config)

        if value == "dictionary":
            self.set_status(self.service.dictionary_diagnostics())
        else:
            self.set_status("已切换到 AI 模式")

        payload = self._config_event_payload()
        self.bridge.send("main", "config-updated", payload)
        self.bridge.send("bubble", "theme-updated", {"themeMode": self._resolved_theme_mode(), "mode": value})
        self._emit_bubble_updated()
        self._emit_tray_menu_updated()
        return payload

    def set_theme(self, theme: str) -> dict[str, Any]:
        with self.lock:
            value = str(theme or "").strip().lower()
            if value not in {"system", "light", "dark"}:
                value = "system"
            self.config.theme_mode = value
            self.ui_state.theme_mode = self._resolved_theme_mode(value)
            self.config_store.save(self.config)

        self._apply_theme_backgrounds()
        payload = self._config_event_payload()
        self.bridge.send("main", "config-updated", payload)
        resolved = self._resolved_theme_mode(value)
        self.bridge.send("bubble", "theme-updated", {"themeMode": resolved, "bubble": self._bubble_state.to_payload()})
        self.bridge.send("icon", "theme-updated", {"themeMode": resolved, "mode": self.config.translation_mode})
        self._emit_tray_menu_updated()
        return payload

    def _tray_menu_payload(self) -> dict[str, Any]:
        mode = self._normalize_translation_mode(self.config.translation_mode)
        return {
            "startupEnabled": bool(self.config.interaction.startup_launch_enabled),
            "selectionEnabled": bool(self.config.interaction.selection_enabled),
            "screenshotEnabled": bool(self.config.interaction.screenshot_enabled),
            "mode": mode,
            "modeLabel": "AI" if mode == "ai" else "词典",
            "aiAvailable": bool(self._ai_available),
            "aiChecked": bool(self._ai_available_checked),
        }

    def _emit_tray_menu_updated(self) -> None:
        self.bridge.send(
            "tray",
            "tray-menu-updated",
            {
                "themeMode": self._resolved_theme_mode(),
                "trayMenu": self._tray_menu_payload(),
            },
        )

    def _tray_menu_position(self, anchor_x: int, anchor_y: int) -> tuple[int, int]:
        bounds = get_virtual_screen_bounds()
        width = int(self.TRAY_MENU_WIDTH)
        height = int(self.TRAY_MENU_HEIGHT)
        margin = 8
        top_gap = 10
        x = int(anchor_x) - width + 16
        y = int(anchor_y) - height - top_gap
        if y < (bounds.top + margin):
            y = int(anchor_y) + top_gap
        max_x = max(bounds.left + margin, bounds.right - width - margin)
        max_y = max(bounds.top + margin, bounds.bottom - height - margin)
        x = max(bounds.left + margin, min(x, max_x))
        y = max(bounds.top + margin, min(y, max_y))
        return int(x), int(y)

    def _cancel_tray_show_timer(self) -> None:
        if self._tray_show_timer is not None:
            try:
                self._tray_show_timer.cancel()
            except Exception:
                pass
        self._tray_show_timer = None

    def _ensure_tray_window(self) -> bool:
        if self.tray_window is not None:
            return True
        bounds = get_virtual_screen_bounds()
        width = int(self.TRAY_MENU_WIDTH)
        height = int(self.TRAY_MENU_HEIGHT)
        offscreen_x = int(bounds.left) - width - 120
        offscreen_y = int(bounds.top) - height - 120
        try:
            self.tray_window = self._create_window(
                kind="tray",
                title=f"{APP_TITLE} Tray",
                width=width,
                height=height,
                min_size=(width, height),
                x=offscreen_x,
                y=offscreen_y,
                frameless=True,
                shadow=False,
                resizable=False,
                on_top=True,
                focus=True,
                transparent=True,
                hidden=True,
            )
            self._tray_window_ready = False
            self._tray_window_shape_applied = False
            self._tray_window_popup_flags_applied = False
            self._tray_window_created_at = time.time()
            return True
        except Exception:
            self.logger.exception("Failed to create tray window")
            self.tray_window = None
            self._tray_window_created_at = 0.0
            return False

    def _show_tray_menu(self, payload: dict[str, Any] | None = None) -> None:
        if not self._webview_started:
            return
        if not self._ensure_tray_window():
            return
        cursor_x, cursor_y = get_cursor_position()
        anchor_x = int((payload or {}).get("x", cursor_x))
        anchor_y = int((payload or {}).get("y", cursor_y))
        if not self._tray_window_ready:
            # Guard against a stale pre-created tray window that never reaches
            # frontend-ready (e.g. transient WebView2 init abort). Recreate on demand.
            age = (time.time() - float(self._tray_window_created_at or 0.0)) if self._tray_window_created_at > 0 else 0.0
            if age >= 2.0 and self.tray_window is not None:
                try:
                    self.tray_window.destroy()
                except Exception:
                    self.logger.exception("Failed to destroy stale tray window before recreate")
                self.tray_window = None
                self._tray_window_shape_applied = False
                self._tray_window_popup_flags_applied = False
                self._tray_window_created_at = 0.0
                self._ensure_tray_window()
            self._pending_tray_anchor = {"x": int(anchor_x), "y": int(anchor_y)}
            return
        x, y = self._tray_menu_position(anchor_x, anchor_y)
        # Update tray DOM while still hidden to avoid a visible second paint.
        self.bridge.send(
            "tray",
            "tray-opening",
            {"guardMs": int(max(180, self.TRAY_BLUR_GUARD_MS))},
        )
        self._emit_tray_menu_updated()
        self._cancel_tray_show_timer()
        self._tray_show_ticket += 1
        ticket = int(self._tray_show_ticket)

        def reveal() -> None:
            if ticket != int(self._tray_show_ticket):
                return
            if (not self._tray_window_ready) or self.tray_window is None:
                return
            window = self.tray_window

            def do_reveal() -> None:
                if window is None:
                    return
                try:
                    window.move(x, y)
                    window.show()
                except Exception:
                    self.logger.exception("Failed to show tray menu window")

            try:
                self._run_on_window_ui(
                    window,
                    do_reveal,
                    wait=False,
                    log_prefix="tray.reveal",
                )
            except Exception:
                self.logger.exception("Failed to schedule tray menu reveal")

        delay_ms = max(0, int(self.TRAY_SHOW_DELAY_MS))
        if delay_ms <= 0:
            reveal()
            return
        try:
            timer = threading.Timer(delay_ms / 1000.0, reveal)
            timer.daemon = True
            self._tray_show_timer = timer
            timer.start()
        except Exception:
            reveal()

    def _show_main_window(self) -> None:
        if self.main_window is None:
            return
        try:
            self.main_window.show()
            self.hidden = False
            self.bridge.send("main", "tray-show-main", {})
        except Exception:
            self.logger.exception("Failed to show main window from tray")

    def _open_panel_from_tray(self, panel: str) -> None:
        panel_name = "history" if str(panel or "").strip().lower() == "history" else "settings"
        self._show_main_window()
        self.bridge.send("main", "tray-open-panel", {"panel": panel_name})

    def _toggle_selection_enabled_from_tray(self) -> dict[str, Any]:
        with self.lock:
            self.config.interaction.selection_enabled = not bool(self.config.interaction.selection_enabled)
            enabled = bool(self.config.interaction.selection_enabled)
            self.config_store.save(self.config)
        if not enabled:
            self.close_window("icon")
        self.set_status("已启用划词" if enabled else "已关闭划词")
        payload = self._config_event_payload()
        self.bridge.send("main", "config-updated", payload)
        self._emit_tray_menu_updated()
        return {"ok": True, "enabled": enabled}

    def _toggle_startup_enabled_from_tray(self) -> dict[str, Any]:
        desired = not bool(self.config.interaction.startup_launch_enabled)
        success, actual = self._apply_startup_launch_enabled(desired)
        self.config_store.save(self.config)
        self.set_status("已启用开机自启动" if actual else "已关闭开机自启动")
        if not success and actual != desired:
            self.set_status("开机自启动设置失败，请检查系统权限后重试")
        payload = self._config_event_payload()
        self.bridge.send("main", "config-updated", payload)
        self._emit_tray_menu_updated()
        return {"ok": bool(success), "enabled": bool(actual)}

    def _toggle_screenshot_enabled_from_tray(self) -> dict[str, Any]:
        with self.lock:
            self.config.interaction.screenshot_enabled = not bool(self.config.interaction.screenshot_enabled)
            enabled = bool(self.config.interaction.screenshot_enabled)
            self.config_store.save(self.config)
        try:
            self.hotkeys.stop()
            self.hotkeys.start()
        except Exception:
            self.logger.exception("Failed to restart hotkeys from tray screenshot toggle")
        self.set_status("已启用截图翻译" if enabled else "已关闭截图翻译")
        payload = self._config_event_payload()
        self.bridge.send("main", "config-updated", payload)
        self._emit_tray_menu_updated()
        return {"ok": True, "enabled": enabled}

    def _dispatch_tray_action(self, key: str) -> None:
        if key == "exit":
            self.shutdown()
            return
        if key == "show_main":
            self._show_main_window()
            return
        if key == "open_history":
            self._open_panel_from_tray("history")
            return
        if key == "open_settings":
            self._open_panel_from_tray("settings")
            return
        if key == "toggle_selection":
            self._toggle_selection_enabled_from_tray()
            return
        if key == "toggle_startup":
            self._toggle_startup_enabled_from_tray()
            return
        if key == "toggle_screenshot":
            self._toggle_screenshot_enabled_from_tray()
            return

    def _on_tray_action(self, action: str, payload: dict[str, Any] | None = None) -> None:
        key = str(action or "").strip().lower()
        if key == "show_tray_menu":
            self._show_tray_menu(payload)
            return
        self._dispatch_tray_action(key)

    def tray_action(self, action: str) -> dict[str, Any]:
        key = str(action or "").strip().lower()
        if not key:
            return {"ok": False}
        self.close_window("tray")
        self._dispatch_tray_action(key)
        return {"ok": True, "action": key}

    def cycle_direction(self) -> dict[str, Any]:
        options = ["auto", "en->zh", "zh->en"]
        with self.lock:
            current = str(self.config.dictionary.preferred_direction or "auto").strip() or "auto"
            if current not in options:
                current = "auto"
            next_value = options[(options.index(current) + 1) % len(options)]
            self.config.dictionary.preferred_direction = next_value
            self.ui_state.direction = self._direction_label(next_value)
            self.config_store.save(self.config)

        self.set_status(f"词典方向已切换: {next_value}")
        payload = self._config_event_payload()
        self.bridge.send("main", "config-updated", payload)
        return payload

    def _direction_label(self, value: str | None = None) -> str:
        current = str(value if value is not None else self.config.dictionary.preferred_direction or "auto").strip() or "auto"
        return "方向: 自动" if current == "auto" else f"方向: {current}"

    def _config_event_payload(self) -> dict[str, Any]:
        return {
            "config": self._serialize_config(),
            "ui": self.ui_state.to_payload(),
            "settings": self.get_settings_payload(),
            "themeMode": self._resolved_theme_mode(),
            "aiAvailability": self._ai_availability_payload(),
        }

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def _is_short_text_for_candidates(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        sentence_marks = len(re.findall(r"[.!?。！？]", normalized))
        if sentence_marks > 1:
            return False

        if self._contains_cjk(normalized):
            compact = re.sub(r"\s+", "", normalized)
            return len(compact) <= int(self.config.openai.multi_candidate_short_cn_max_chars or 24)

        words = re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?", normalized)
        if words:
            return len(words) <= int(self.config.openai.multi_candidate_short_en_max_words or 12)
        return len(normalized) <= int(self.config.openai.multi_candidate_short_cn_max_chars or 24)

    def _can_generate_candidates(
        self,
        *,
        source_text: str,
        mode: str,
        is_pending: bool,
        has_result: bool,
    ) -> tuple[bool, str]:
        if mode != "ai":
            return False, "仅 AI 模式支持多候选"
        if not source_text.strip():
            return False, "请先输入并翻译"
        if is_pending:
            return False, "翻译进行中，请稍后"
        if not has_result:
            return False, "请先完成一次翻译"
        if not self._is_short_text_for_candidates(source_text):
            return False, "仅短词或短句支持多候选"
        return True, ""

    def set_status(self, text: str, *, notify: bool = True) -> None:
        with self.lock:
            self.ui_state.status = text
        if notify:
            self.bridge.send("main", "status", {"text": text})

    @staticmethod
    def _http_status_from_message(message: str) -> int | None:
        match = re.search(r"\bHTTP\s+(\d{3})\b", message, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _user_friendly_message(self, message: str, *, scene: str = "general") -> str:
        raw = str(message or "").strip()
        if not raw:
            return "操作失败，请稍后重试。"

        lowered = raw.lower()
        http_code = self._http_status_from_message(raw)

        if http_code in {401, 403}:
            return "连接失败：认证未通过，请检查 API Key。"
        if http_code == 404:
            return "连接失败：地址或模型不存在，请检查设置。"
        if http_code and http_code >= 500:
            return "服务暂时不可用，请稍后重试。"
        if http_code and http_code >= 400:
            return "请求失败，请检查设置后重试。"

        if "请求超时" in raw or "timeout" in lowered or "timed out" in lowered:
            return "连接超时，请稍后重试。"
        if "网络错误" in raw or "urlerror" in lowered or "connection refused" in lowered:
            return "网络连接失败，请检查网络或服务地址。"
        if "api key" in lowered:
            return "请在设置中填写正确的 API Key。"
        if "ai 配置不完整" in raw or "配置不完整" in raw:
            return "请先在设置中填写 AI 地址和模型。"
        if "模式切换失败" in raw:
            return "切换失败，请稍后重试。"
        if "候选解析失败" in raw or "候选数量不足" in raw:
            return "候选生成失败，请重试一次。"
        if "openai兼容接口失败" in lowered or "ollama原生接口失败" in lowered or "ollama 调用失败" in lowered:
            return "AI 服务暂时不可用，请检查设置后重试。"
        if "模型文件不存在" in raw:
            return "导入失败：未找到模型文件。"
        if "模型方向不可用" in raw or "未找到可用词典模型" in raw or "未找到可用词典方向" in raw:
            return "当前词典模型不可用，请在设置中重新选择或导入模型。"
        if "运行库" in raw:
            return "词典翻译暂不可用，请在设置中检查词典运行环境。"
        if "截图中未识别到可翻译文本" in raw:
            return "这次没有识别到可翻译内容。请尽量框住完整文字，或稍微扩大选区后重试。"
        if "windows-empty" in lowered:
            return "这次没有识别到可翻译内容。请尽量框住完整文字，或稍微扩大选区后重试。"
        if "language package not installed" in lowered:
            return "Windows OCR 语言包不可用，请在系统中安装对应识别语言包。"
        if "class not registered" in lowered or "没有注册类" in raw:
            return "Windows OCR 组件不可用，请安装系统 WebView2/Windows OCR 相关组件后重试。"

        if scene == "translate":
            return "翻译失败，请稍后重试。"
        if scene == "candidate":
            return "候选生成失败，请稍后重试。"
        if scene == "screenshot":
            return "截图识别失败，请重试。"
        if scene == "ai_test":
            return raw or "连接测试失败，请检查设置后重试。"
        if scene == "dictionary_import":
            return "导入失败，请确认模型文件可用后重试。"

        return raw

    def _cancel_active_translation(self, message: str = "已取消翻译") -> bool:
        with self.lock:
            req_id = self._active_translate_request_seq
            cancel_event = self._active_translate_cancel
            partial = self._active_translation_text
            showing_bubble = self._active_translate_shows_bubble
            if req_id <= 0 or cancel_event is None or cancel_event.is_set():
                return False
            cancel_event.set()
            self._active_translate_request_seq = 0
            self._active_translate_shows_bubble = False

        self.set_status(message)
        self.bridge.send(
            "main",
            "translation-cancelled",
            {
                "reqId": req_id,
                "resultText": partial,
                "message": message,
            },
        )
        if showing_bubble:
            with self.lock:
                self._bubble_state.pending = False
            self.close_window("bubble")
        return True

    def translate_from_window(self, kind: str, text: str, action: str) -> dict[str, Any]:
        show_bubble = kind in {"bubble", "icon"}
        return self._start_translate(
            text=text,
            action=action or "翻译",
            show_bubble=show_bubble,
            update_main=(kind == "main"),
        )

    def cancel_translation(self) -> dict[str, Any]:
        cancelled = self._cancel_active_translation("已取消翻译")
        return {"ok": True, "cancelled": bool(cancelled)}

    @staticmethod
    def _normalize_candidate_text(value: str) -> str:
        normalized = re.sub(r"\s+", " ", str(value or "").strip()).lower()
        normalized = re.sub(r"[`~!@#$%^&*()_\-+=\[\]{}|\\:;\"'<>,.?/·，。！？；：“”‘’、（）【】《》…—\s]+", "", normalized)
        return normalized

    def _sanitize_candidates(self, items: list[str], *, exclude_text: str = "") -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        excluded_norm = self._normalize_candidate_text(exclude_text)
        for raw in items or []:
            value = str(raw or "").strip()
            if not value:
                continue
            norm = self._normalize_candidate_text(value)
            if not norm:
                continue
            if excluded_norm and norm == excluded_norm:
                continue
            if norm in seen:
                continue
            seen.add(norm)
            cleaned.append(value)
        return cleaned

    @staticmethod
    def _has_latin(text: str) -> bool:
        return bool(re.search(r"[A-Za-z]", str(text or "")))

    def _infer_target_lang(self, reference_result: str, source_text: str = "") -> str:
        ref = str(reference_result or "").strip()
        if self._contains_cjk(ref):
            return "zh"
        if self._has_latin(ref):
            return "en"
        src = str(source_text or "").strip()
        if self._contains_cjk(src):
            return "en"
        if self._has_latin(src):
            return "zh"
        return "unknown"

    def _matches_target_lang(self, text: str, target_lang: str) -> bool:
        value = str(text or "").strip()
        if not value or target_lang == "unknown":
            return True
        has_cjk = self._contains_cjk(value)
        has_latin = self._has_latin(value)
        if target_lang == "zh":
            return has_cjk
        if target_lang == "en":
            return has_latin
        return True

    def generate_multi_candidates_from_window(self, kind: str, text: str = "", result_text: str = "") -> dict[str, Any]:
        source_text = str(text or "").strip()
        latest_result_text = str(result_text or "").strip()
        mode = self._normalize_translation_mode(self.config.translation_mode or "dictionary")
        is_pending = False
        has_result = False

        if kind == "bubble":
            with self.lock:
                source_text = source_text or str(self._bubble_state.source_text or "").strip()
                latest_result_text = latest_result_text or str(self._bubble_state.result_text or "").strip()
                is_pending = bool(self._bubble_state.pending or self._bubble_state.candidate_pending)
                has_result = bool(str(self._bubble_state.result_text or "").strip())
        else:
            with self.lock:
                is_pending = bool(self._active_translate_request_seq > 0 and self._active_translate_cancel and not self._active_translate_cancel.is_set())
                has_result = bool(latest_result_text)

        ok, message = self._can_generate_candidates(
            source_text=source_text,
            mode=mode,
            is_pending=is_pending,
            has_result=has_result,
        )
        if not ok:
            return {"ok": False, "message": message}

        # V1 requirement: always generate 4 additional candidates
        count = 4
        with self.lock:
            self._candidate_request_seq += 1
            req_id = self._candidate_request_seq
            self._active_candidate_request_seq = req_id

        if kind == "bubble":
            with self.lock:
                self._bubble_state.candidate_pending = True
                self._bubble_state.candidate_items = []
            self._emit_bubble_updated()
        else:
            self.bridge.send(
                "main",
                "multi-candidates-start",
                {"reqId": req_id, "sourceText": source_text, "count": count},
            )

        def worker() -> None:
            try:
                gathered: list[str] = []
                seen_norm = {self._normalize_candidate_text(latest_result_text)} if latest_result_text else set()
                target_lang = self._infer_target_lang(latest_result_text, source_text)
                for _attempt in range(6):
                    needed = count - len(gathered)
                    if needed <= 0:
                        break
                    try:
                        raw_candidates = self.service.translate_candidates(
                            source_text,
                            mode,
                            count=needed,
                            reference_result=latest_result_text,
                        )
                    except Exception:
                        continue
                    for candidate in self._sanitize_candidates(raw_candidates, exclude_text=latest_result_text):
                        if not self._matches_target_lang(candidate, target_lang):
                            continue
                        norm = self._normalize_candidate_text(candidate)
                        if not norm or norm in seen_norm:
                            continue
                        seen_norm.add(norm)
                        gathered.append(candidate)
                    if len(gathered) >= count:
                        break
                if not gathered:
                    raise RuntimeError("候选数量不足，请重试")
                candidates = gathered[:count]
                with self.lock:
                    if req_id != self._active_candidate_request_seq:
                        return
                if kind == "bubble":
                    with self.lock:
                        self._bubble_state.candidate_pending = False
                        self._bubble_state.candidate_items = list(candidates)
                    self._emit_bubble_updated()
                else:
                    self.bridge.send(
                        "main",
                        "multi-candidates-done",
                        {"reqId": req_id, "sourceText": source_text, "candidates": list(candidates)},
                    )
            except Exception as exc:
                error_text = self._user_friendly_message(str(exc), scene="candidate")
                with self.lock:
                    if req_id != self._active_candidate_request_seq:
                        return
                if kind == "bubble":
                    with self.lock:
                        self._bubble_state.candidate_pending = False
                        self._bubble_state.candidate_items = []
                    self._emit_bubble_updated()
                    self.bridge.send(
                        "bubble",
                        "multi-candidates-error",
                        {"reqId": req_id, "sourceText": source_text, "message": error_text},
                    )
                else:
                    self.bridge.send(
                        "main",
                        "multi-candidates-error",
                        {"reqId": req_id, "sourceText": source_text, "message": error_text},
                    )

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "started": True, "reqId": req_id}

    def _start_translate(self, text: str, action: str, show_bubble: bool, update_main: bool | None = None) -> dict[str, Any]:
        source_text = str(text or "").strip()
        if not source_text:
            self.set_status("请输入要翻译的内容")
            return {"ok": False, "message": "请输入要翻译的内容"}

        with self.lock:
            previous_cancel = self._active_translate_cancel
            self._translate_request_seq += 1
            req_id = self._translate_request_seq
            self._active_translate_request_seq = req_id
            self._active_translation_text = ""
            self._active_translate_cancel = threading.Event()
            self._active_translate_shows_bubble = show_bubble
            mode = self.config.translation_mode
        if previous_cancel is not None:
            previous_cancel.set()

        self.set_status("")
        if update_main:
            self.bridge.send(
                "main",
                "translation-start",
                {
                    "reqId": req_id,
                    "sourceText": source_text,
                    "mode": mode,
                    "action": action,
                },
            )

        if show_bubble:
            preserve_existing_bubble = self.bubble_window is not None
            bubble_anchor: tuple[int, int] | None = None
            preserve_position = preserve_existing_bubble
            preserve_height = preserve_existing_bubble
            if action == "划词翻译":
                payload = self._selection_candidate.payload if self._selection_candidate.payload else {}
                if payload:
                    anchor_x = int(payload.get("x", 0))
                    anchor_y = int(payload.get("y", 0))
                    if anchor_x or anchor_y:
                        bubble_anchor = (anchor_x, anchor_y)
                preserve_position = False
                preserve_height = False
            self._show_bubble(
                source_text=source_text,
                result_text="",
                pending=True,
                action=action,
                mode=mode,
                anchor=bubble_anchor,
                preserve_position=preserve_position,
                preserve_height=preserve_height,
            )

        def worker() -> None:
            chunks: list[str] = []
            buffered_chars = 0
            last_flush_at = time.monotonic()
            cancel_event = self._active_translate_cancel

            def flush() -> None:
                nonlocal buffered_chars, last_flush_at
                if not chunks:
                    return
                delta = "".join(chunks)
                chunks.clear()
                buffered_chars = 0
                last_flush_at = time.monotonic()
                self._handle_translate_chunk(req_id, source_text, delta, action, mode, show_bubble, update_main)

            def on_delta(chunk: str) -> None:
                nonlocal buffered_chars, last_flush_at
                if not chunk:
                    return
                chunks.append(chunk)
                buffered_chars += len(chunk)
                now = time.monotonic()
                if buffered_chars >= 8 or "\n" in chunk or (now - last_flush_at) >= 0.03:
                    flush()

            try:
                should_cancel = lambda: bool(cancel_event and cancel_event.is_set())
                if action == "AI润色":
                    result = self.service.polish_stream(source_text, mode, on_delta, should_cancel=should_cancel)
                else:
                    result = self.service.translate_stream(source_text, mode, on_delta, should_cancel=should_cancel)
                if should_cancel():
                    return
                flush()
                self._handle_translate_done(req_id, source_text, result, action, mode, show_bubble, update_main)
            except Exception as exc:
                if should_cancel() or "cancel" in str(exc).lower():
                    return
                flush()
                self.logger.exception("Translate worker failed action=%s mode=%s", action, mode)
                self._handle_translate_error(req_id, source_text, str(exc), action, mode, show_bubble, update_main)

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "reqId": req_id}

    def _handle_translate_chunk(
        self,
        req_id: int,
        source_text: str,
        delta: str,
        action: str,
        mode: str,
        show_bubble: bool,
        update_main: bool,
    ) -> None:
        if not delta:
            return

        with self.lock:
            if req_id != self._active_translate_request_seq:
                return
            self._active_translation_text = f"{self._active_translation_text}{delta}"
            current_text = self._active_translation_text
            current_show_bubble = self._active_translate_shows_bubble

        if update_main:
            self.bridge.send(
                "main",
                "translation-chunk",
                {
                    "reqId": req_id,
                    "sourceText": source_text,
                    "delta": delta,
                    "resultText": current_text,
                    "mode": mode,
                    "action": action,
                },
            )
        elif show_bubble:
            self.bridge.send(
                "main",
                "bubble-translation-chunk",
                {
                    "reqId": req_id,
                    "sourceText": source_text,
                    "delta": delta,
                    "resultText": current_text,
                    "mode": mode,
                    "action": action,
                },
            )

        if show_bubble and current_show_bubble:
            self._update_bubble(
                source_text,
                current_text,
                pending=True,
                action=action,
                mode=mode,
                refresh_auto_hide=False,
            )

    def _handle_translate_done(
        self,
        req_id: int,
        source_text: str,
        result_text: str,
        action: str,
        mode: str,
        show_bubble: bool,
        update_main: bool,
    ) -> None:
        with self.lock:
            if req_id != self._active_translate_request_seq:
                return
            self._active_translation_text = result_text
            current_show_bubble = self._active_translate_shows_bubble
            self._active_translate_request_seq = 0
            self._active_translate_cancel = None
            self._active_translate_shows_bubble = False

        self.history.add_record(
            action,
            mode,
            source_text,
            result_text,
            source_kind=self._history_source_kind_from_action(action),
            direction=str(self.config.dictionary.preferred_direction or "auto"),
        )
        self._prune_history_by_policy()
        history_rows = self.get_history_rows()
        with self.lock:
            self.ui_state.history = history_rows

        if update_main:
            self.bridge.send(
                "main",
                "translation-done",
                {
                    "reqId": req_id,
                    "sourceText": source_text,
                    "resultText": result_text,
                    "mode": mode,
                    "action": action,
                    "history": history_rows,
                },
            )
        elif show_bubble:
            self.bridge.send(
                "main",
                "bubble-translation-done",
                {
                    "reqId": req_id,
                    "sourceText": source_text,
                    "resultText": result_text,
                    "mode": mode,
                    "action": action,
                },
            )

        if show_bubble and current_show_bubble:
            self._update_bubble(
                source_text,
                result_text,
                pending=False,
                action=action,
                mode=mode,
                refresh_auto_hide=False,
            )

    def _handle_translate_error(
        self,
        req_id: int,
        source_text: str,
        message: str,
        action: str,
        mode: str,
        show_bubble: bool,
        update_main: bool,
    ) -> None:
        with self.lock:
            if req_id != self._active_translate_request_seq:
                return
            partial = self._active_translation_text
            current_show_bubble = self._active_translate_shows_bubble
            self._active_translate_request_seq = 0
            self._active_translate_cancel = None
            self._active_translate_shows_bubble = False

        user_message = self._user_friendly_message(message, scene="translate")
        self.set_status(user_message)
        if update_main:
            self.bridge.send(
                "main",
                "translation-error",
                {
                    "reqId": req_id,
                    "sourceText": source_text,
                    "resultText": partial,
                    "message": user_message,
                    "mode": mode,
                    "action": action,
                },
            )
        elif show_bubble:
            self.bridge.send(
                "main",
                "bubble-translation-error",
                {
                    "reqId": req_id,
                    "sourceText": source_text,
                    "resultText": partial,
                    "message": user_message,
                    "mode": mode,
                    "action": action,
                },
            )

        if show_bubble and current_show_bubble:
            bubble_text = partial.strip() or user_message
            if partial.strip():
                bubble_text = f"{partial}\n\n[生成中断] {user_message}"
            self._update_bubble(
                source_text,
                bubble_text,
                pending=False,
                action=action,
                mode=mode,
                refresh_auto_hide=False,
            )

    def copy_text(self, text: str) -> dict[str, Any]:
        ok = set_clipboard_text(str(text or ""))
        message = "已复制到剪贴板" if ok else "复制失败，请重试"
        self.set_status(message)
        return {"ok": ok, "message": message}

    def clear_history(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        scope = "all"
        if isinstance(payload, dict):
            scope = str(payload.get("scope", "all") or "all").strip().lower()
        if scope == "non_favorite":
            self.history.clear_non_favorite()
        elif scope == "favorites":
            self.history.clear_favorites()
        else:
            self.history.clear()
        history_rows = self.get_history_rows()
        with self.lock:
            self.ui_state.history = history_rows
        self.set_status("历史记录已清空")
        response = {"history": history_rows, "filters": self._history_filters_payload()}
        self.bridge.send("main", "history-updated", response)
        return response

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        prev_screenshot_enabled = bool(self.config.interaction.screenshot_enabled)
        prev_screenshot_hotkey = normalize_shortcut(str(self.config.interaction.screenshot_hotkey or ""))
        prev_restore_hotkey = normalize_shortcut(str(getattr(self.config.interaction, "bubble_restore_hotkey", "") or ""))
        prev_toggle_main_hotkey = normalize_shortcut(str(getattr(self.config.interaction, "main_toggle_hotkey", "") or ""))
        openai = payload.get("openai", {}) if isinstance(payload, dict) else {}
        interaction = payload.get("interaction", {}) if isinstance(payload, dict) else {}
        dictionary = payload.get("dictionary", {}) if isinstance(payload, dict) else {}
        theme_mode = str(payload.get("theme_mode", self.config.theme_mode) or self.config.theme_mode) if isinstance(payload, dict) else self.config.theme_mode
        startup_launch_enabled = bool(interaction.get("startup_launch_enabled", self.config.interaction.startup_launch_enabled))

        try:
            timeout_sec = max(5, int(openai.get("timeout_sec", self.config.openai.timeout_sec)))
        except Exception:
            timeout_sec = self.config.openai.timeout_sec

        self.config.openai.base_url = str(openai.get("base_url", self.config.openai.base_url) or "").strip()
        self.config.openai.api_key = str(openai.get("api_key", self.config.openai.api_key) or "").strip()
        self.config.openai.model = str(openai.get("model", self.config.openai.model) or "").strip()
        self.config.openai.timeout_sec = timeout_sec

        preferred_direction = str(dictionary.get("preferred_direction", self.config.dictionary.preferred_direction) or "auto").strip()
        self.config.dictionary.preferred_direction = preferred_direction or "auto"

        trigger_mode = str(
            interaction.get("selection_trigger_mode", self.config.interaction.selection_trigger_mode) or "double_ctrl"
        ).strip()
        if trigger_mode not in {"icon", "double_ctrl", "double_alt", "double_shift"}:
            trigger_mode = "double_ctrl"

        icon_trigger = str(interaction.get("selection_icon_trigger", self.config.interaction.selection_icon_trigger) or "click").strip()
        if icon_trigger not in {"click", "hover"}:
            icon_trigger = "click"

        try:
            icon_delay_ms = int(interaction.get("selection_icon_delay_ms", self.config.interaction.selection_icon_delay_ms))
        except Exception:
            icon_delay_ms = self.config.interaction.selection_icon_delay_ms
        try:
            drag_min_px = int(interaction.get("selection_drag_min_px", self.config.interaction.selection_drag_min_px))
        except Exception:
            drag_min_px = self.config.interaction.selection_drag_min_px
        try:
            click_pair_max_distance_px = int(
                interaction.get("selection_click_pair_max_distance_px", self.config.interaction.selection_click_pair_max_distance_px)
            )
        except Exception:
            click_pair_max_distance_px = self.config.interaction.selection_click_pair_max_distance_px
        try:
            hold_min_ms = int(interaction.get("selection_hold_min_ms", self.config.interaction.selection_hold_min_ms))
        except Exception:
            hold_min_ms = self.config.interaction.selection_hold_min_ms
        try:
            icon_arm_delay_ms = int(interaction.get("selection_icon_arm_delay_ms", self.config.interaction.selection_icon_arm_delay_ms))
        except Exception:
            icon_arm_delay_ms = self.config.interaction.selection_icon_arm_delay_ms
        try:
            verify_timeout_ms = int(interaction.get("selection_verify_timeout_ms", self.config.interaction.selection_verify_timeout_ms))
        except Exception:
            verify_timeout_ms = self.config.interaction.selection_verify_timeout_ms
        try:
            hover_dwell_ms = int(interaction.get("selection_hover_dwell_ms", self.config.interaction.selection_hover_dwell_ms))
        except Exception:
            hover_dwell_ms = self.config.interaction.selection_hover_dwell_ms
        try:
            hover_max_speed_px_s = int(
                interaction.get("selection_hover_max_speed_px_s", self.config.interaction.selection_hover_max_speed_px_s)
            )
        except Exception:
            hover_max_speed_px_s = self.config.interaction.selection_hover_max_speed_px_s
        try:
            candidate_dedupe_window_ms = int(
                interaction.get(
                    "selection_candidate_dedupe_window_ms",
                    self.config.interaction.selection_candidate_dedupe_window_ms,
                )
            )
        except Exception:
            candidate_dedupe_window_ms = self.config.interaction.selection_candidate_dedupe_window_ms
        try:
            candidate_max_age_sec = float(
                interaction.get("selection_candidate_max_age_sec", self.config.interaction.selection_candidate_max_age_sec)
            )
        except Exception:
            candidate_max_age_sec = self.config.interaction.selection_candidate_max_age_sec

        app_profiles_raw = interaction.get("app_profiles", self.config.interaction.app_profiles)
        normalized_profiles: list[SelectionAppProfile] = []
        if isinstance(app_profiles_raw, list):
            for item in app_profiles_raw:
                if isinstance(item, SelectionAppProfile):
                    normalized = ConfigStore._normalize_selection_profile_item(asdict(item))
                else:
                    normalized = ConfigStore._normalize_selection_profile_item(item)
                if normalized is not None:
                    normalized_profiles.append(normalized)

        screenshot_enabled = bool(interaction.get("screenshot_enabled", self.config.interaction.screenshot_enabled))
        screenshot_hotkey = normalize_shortcut(str(interaction.get("screenshot_hotkey", self.config.interaction.screenshot_hotkey) or ""))
        bubble_restore_hotkey = normalize_shortcut(str(interaction.get("bubble_restore_hotkey", getattr(self.config.interaction, "bubble_restore_hotkey", "")) or ""))
        main_toggle_hotkey = normalize_shortcut(str(interaction.get("main_toggle_hotkey", getattr(self.config.interaction, "main_toggle_hotkey", "")) or ""))
        has_fast_close_profile_key = isinstance(interaction, dict) and ("bubble_fast_close_profile" in interaction)
        fast_close_profile_raw = interaction.get(
            "bubble_fast_close_profile",
            getattr(self.config.interaction, "bubble_fast_close_profile", ""),
        )
        fast_close_profile = ConfigStore._normalize_bubble_fast_close_profile(
            fast_close_profile_raw,
            legacy_enabled=bool(interaction.get("bubble_close_on_fast_mouse_leave", self.config.interaction.bubble_close_on_fast_mouse_leave)),
        )
        bubble_close_on_fast_mouse_leave = bool(
            interaction.get("bubble_close_on_fast_mouse_leave", self.config.interaction.bubble_close_on_fast_mouse_leave)
        )
        bubble_close_on_click_outside = bool(
            interaction.get("bubble_close_on_click_outside", self.config.interaction.bubble_close_on_click_outside)
        )
        history_cfg = payload.get("history", {}) if isinstance(payload, dict) else {}
        try:
            retention_days = int(history_cfg.get("retention_days", self.config.history.retention_days))
        except Exception:
            retention_days = self.config.history.retention_days

        self.config.interaction.selection_enabled = bool(interaction.get("selection_enabled", self.config.interaction.selection_enabled))
        self.config.interaction.selection_trigger_mode = trigger_mode
        self.config.interaction.selection_icon_trigger = icon_trigger
        self.config.interaction.screenshot_enabled = screenshot_enabled
        self.config.interaction.screenshot_hotkey = screenshot_hotkey
        self.config.interaction.bubble_restore_hotkey = bubble_restore_hotkey
        self.config.interaction.main_toggle_hotkey = main_toggle_hotkey
        self.config.interaction.bubble_fast_close_profile = fast_close_profile
        self.config.interaction.bubble_close_on_fast_mouse_leave = bool(
            fast_close_profile != "off"
            if has_fast_close_profile_key
            else (fast_close_profile != "off" and bubble_close_on_fast_mouse_leave)
        )
        self.config.interaction.bubble_close_on_click_outside = bubble_close_on_click_outside
        self.config.interaction.selection_icon_delay_ms = max(0, min(5000, icon_delay_ms))
        self.config.interaction.selection_drag_min_px = max(3, min(40, drag_min_px))
        self.config.interaction.selection_click_pair_max_distance_px = max(4, min(40, click_pair_max_distance_px))
        self.config.interaction.selection_hold_min_ms = max(0, min(300, hold_min_ms))
        self.config.interaction.selection_icon_arm_delay_ms = max(0, min(800, icon_arm_delay_ms))
        self.config.interaction.selection_verify_timeout_ms = max(20, min(300, verify_timeout_ms))
        self.config.interaction.selection_hover_dwell_ms = max(0, min(500, hover_dwell_ms))
        self.config.interaction.selection_hover_max_speed_px_s = max(120, min(5000, hover_max_speed_px_s))
        self.config.interaction.selection_candidate_dedupe_window_ms = max(60, min(1500, candidate_dedupe_window_ms))
        self.config.interaction.selection_candidate_max_age_sec = max(2.0, min(30.0, float(candidate_max_age_sec)))
        self.config.interaction.app_profiles = normalized_profiles
        self.config.history.retention_days = retention_days if retention_days in {7, 30, 90} else 30

        startup_prev = bool(self.config.interaction.startup_launch_enabled)
        startup_status_message = ""
        if startup_launch_enabled != startup_prev:
            startup_ok, startup_actual = self._apply_startup_launch_enabled(startup_launch_enabled)
            if not startup_ok and startup_actual != startup_launch_enabled:
                startup_status_message = "开机自启动设置失败，请检查系统权限后重试"
            else:
                startup_status_message = "已启用开机自启动" if startup_actual else "已关闭开机自启动"
        else:
            self.config.interaction.startup_launch_enabled = bool(startup_launch_enabled)

        if theme_mode not in {"system", "light", "dark"}:
            theme_mode = "system"
        self.config.theme_mode = theme_mode
        self.ui_state.theme_mode = self._resolved_theme_mode(theme_mode)
        self.ui_state.direction = self._direction_label()
        self.config_store.save(self.config)
        self._apply_theme_backgrounds()
        self._prune_history_by_policy()
        screenshot_hotkey_changed = (
            prev_screenshot_enabled != bool(self.config.interaction.screenshot_enabled)
            or prev_screenshot_hotkey != normalize_shortcut(str(self.config.interaction.screenshot_hotkey or ""))
            or prev_restore_hotkey != normalize_shortcut(str(getattr(self.config.interaction, "bubble_restore_hotkey", "") or ""))
            or prev_toggle_main_hotkey != normalize_shortcut(str(getattr(self.config.interaction, "main_toggle_hotkey", "") or ""))
        )
        if screenshot_hotkey_changed:
            try:
                self.hotkeys.stop()
                self.hotkeys.start()
            except Exception:
                self.logger.exception("Failed to restart hotkey manager after settings update")

        if startup_status_message:
            self.set_status(startup_status_message)
        elif self._normalize_translation_mode(self.config.translation_mode) == "dictionary":
            self.set_status(self.service.dictionary_diagnostics())
        else:
            self.set_status("AI 设置已保存")

        response = self._config_event_payload()
        self.bridge.send("main", "config-updated", response)
        resolved = self._resolved_theme_mode()
        self.bridge.send("bubble", "theme-updated", {"themeMode": resolved, "bubble": self._bubble_state.to_payload()})
        self.bridge.send("icon", "theme-updated", {"themeMode": resolved, "mode": self.config.translation_mode})
        self._emit_tray_menu_updated()
        return response

    def test_ai_connection(self) -> None:
        self.set_status("正在测试 AI 连接...")

        def worker() -> None:
            ok, message = self.service.test_ai_connection()
            message = self._user_friendly_message(message, scene="ai_test")
            with self.lock:
                self._ai_available = bool(ok)
                self._ai_available_checked = True
                self._ai_availability_message = str(message or "")
                self._ai_availability_checked_at = time.time()
            self._save_ai_status_cache()
            self.set_status(message)
            self.bridge.send(
                "main",
                "ai-test-result",
                {
                    "ok": ok,
                    "message": message,
                    "checkedAt": float(self._ai_availability_checked_at or time.time()),
                },
            )
            self._emit_ai_availability()

        threading.Thread(target=worker, daemon=True).start()

    def import_dictionary_model(self, window) -> dict[str, Any]:
        if window is None:
            return {"ok": False, "message": "主窗口不可用"}
        if not self.service.dictionary_runtime_ready(probe=True):
            message = self.service.dictionary_runtime_hint(probe=True)
            self.set_status(message)
            return {"ok": False, "message": message}

        try:
            selected = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("词典模型 (*.argosmodel)",),
            )
        except Exception as exc:
            message = f"无法打开文件选择窗口，请重试（{exc}）"
            self.set_status(message)
            return {"ok": False, "message": message}

        if not selected:
            return {"ok": False, "message": "已取消导入"}

        file_path = selected[0] if isinstance(selected, (list, tuple)) else selected
        self.set_status("正在导入词典模型...")

        def worker() -> None:
            try:
                message = self.service.import_dictionary_model(str(file_path))
                self.set_status(message)
                payload = self._config_event_payload()
                self.bridge.send("main", "dictionary-models-updated", payload)
            except Exception as exc:
                message = self._user_friendly_message(str(exc), scene="dictionary_import")
                self.set_status(message)
                self.bridge.send("main", "dictionary-model-import-error", {"message": message})

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "已开始导入"}

    def _save_screenshot_background_image(self, image) -> None:
        runtime_dir = self.frontend_dir / "__runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        filename = f"bg_{uuid.uuid4().hex}.png"
        path = runtime_dir / filename
        image.save(path, format="PNG")
        old = self._screenshot_background_path
        self._screenshot_background_path = path
        if old is not None and old != path:
            try:
                old.unlink(missing_ok=True)
            except Exception:
                self.logger.exception("Failed to delete old screenshot background: %s", old)
        return None

    @staticmethod
    def _flush_dwm() -> None:
        try:
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass

    def _capture_selection_image_from_background(
        self,
        *,
        bounds: dict[str, int],
        left: int,
        top: int,
        right: int,
        bottom: int,
    ):
        path = self._screenshot_background_path
        if path is None or (not path.exists()):
            return None
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return None
        try:
            with Image.open(path) as bg:
                img_w, img_h = bg.size
                rel_left = max(0, min(int(img_w), int(left - bounds["left"])))
                rel_top = max(0, min(int(img_h), int(top - bounds["top"])))
                rel_right = max(0, min(int(img_w), int(right - bounds["left"])))
                rel_bottom = max(0, min(int(img_h), int(bottom - bounds["top"])))
                if (rel_right - rel_left) < MIN_CAPTURE_SIZE or (rel_bottom - rel_top) < MIN_CAPTURE_SIZE:
                    return None
                return bg.crop((rel_left, rel_top, rel_right, rel_bottom)).copy()
        except Exception:
            self.logger.exception("Failed to crop selection image from screenshot background")
            return None

    def _clear_screenshot_background_image(self) -> None:
        path = self._screenshot_background_path
        self._screenshot_background_path = None
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except Exception:
            self.logger.exception("Failed to clear screenshot background: %s", path)

    def start_screenshot_capture(self, show_bubble: bool) -> dict[str, Any]:
        self.logger.info("Screenshot capture request received show_bubble=%s", bool(show_bubble))
        now = time.time()
        with self.lock:
            if self._screenshot_starting or self._screenshot_session is not None:
                self.logger.info(
                    "Screenshot capture ignored: already active starting=%s session=%s",
                    bool(self._screenshot_starting),
                    bool(self._screenshot_session is not None),
                )
                return {"ok": False, "message": "截图已在进行中"}
            # Deduplicate hotkey events fired by multiple hook threads.
            if (now - self._last_screenshot_trigger_at) < 0.45:
                self.logger.info("Screenshot capture ignored: trigger dedup delta=%.3f", now - self._last_screenshot_trigger_at)
                return {"ok": False, "message": "截图触发过快"}
            self._last_screenshot_trigger_at = now
            self._screenshot_starting = True

        try:
            self._cancel_selection_icon_timer()
            self._cancel_selection_icon_hide_timer()
            self._native_icon_overlay.hide()
            self._selection_icon_anchor_pos = None
            self._selection_icon_shown_at = 0.0
            self._selection_icon_hover_armed = False
            self._selection_hover_entered_at = 0.0
            self._selection_hover_last_cursor = None
            self._selection_hover_last_at = 0.0
            self._set_global_cursor_crosshair(True)
            self._suppress_selection_events(1.0)

            main_hidden_before = bool(self.hidden) or (not self._is_main_window_visible())
            # Keep main window untouched during screenshot flow.
            self.hidden = bool(main_hidden_before)
            # Let the compositor settle so we don't capture stale frames from previous sessions.
            self._flush_dwm()
            time.sleep(0.022)

            try:
                image = capture_virtual_screen()
                self._save_screenshot_background_image(image)
            except Exception as exc:
                self.logger.exception("Screenshot capture failed while capturing virtual screen")
                self._set_global_cursor_crosshair(False)
                self._restore_main_after_screenshot(main_was_hidden=bool(main_hidden_before))
                message = self._user_friendly_message(str(exc), scene="screenshot")
                self.set_status(message)
                return {"ok": False, "message": message}

            bounds = get_virtual_screen_bounds()
            cursor_x, cursor_y = get_cursor_position()
            with self.lock:
                self._screenshot_session_seq += 1
                session_id = int(self._screenshot_session_seq)
                self._screenshot_session = ScreenshotSession(
                    session_id=session_id,
                    bounds=bounds.to_payload(),
                    show_bubble=bool(show_bubble),
                    main_was_hidden=main_hidden_before,
                    started_at=now,
                )
            try:
                overlay_ok = self._native_screenshot_overlay.show(
                    session_id=session_id,
                    image=image,
                    bounds=bounds.to_payload(),
                    hint_x=int(cursor_x),
                    hint_y=int(cursor_y),
                )
                if not overlay_ok:
                    raise RuntimeError("截图界面初始化失败，请重试")
                self.logger.info(
                    "Screenshot overlay shown session_id=%s cursor=(%s,%s)",
                    int(session_id),
                    int(cursor_x),
                    int(cursor_y),
                )
            except Exception as exc:
                self.logger.exception("Screenshot capture failed while showing native overlay")
                with self.lock:
                    session = self._screenshot_session
                    self._screenshot_session = None
                self._close_screenshot_window()
                self._clear_screenshot_background_image()
                self._set_global_cursor_crosshair(False)
                if session is not None:
                    self._restore_main_after_screenshot(main_was_hidden=self._main_hidden_flag_for_session(session))
                message = self._user_friendly_message(str(exc), scene="screenshot")
                self.set_status(message)
                return {"ok": False, "message": message}
            self.set_status("拖拽选择截图区域，右键或 Esc 取消")
            return {"ok": True}
        finally:
            with self.lock:
                self._screenshot_starting = False

    def _on_native_screenshot_selection(self, payload: dict[str, int]) -> None:
        try:
            self.finish_screenshot_selection(dict(payload))
        except Exception:
            self.logger.exception("Failed to finish screenshot selection from native overlay")

    def _on_native_screenshot_cancel(self, session_id: int) -> None:
        with self.lock:
            session = self._screenshot_session
            if session is None:
                return
            if int(session_id) != int(session.session_id):
                return
        try:
            self.cancel_screenshot_capture()
        except Exception:
            self.logger.exception("Failed to cancel screenshot capture from native overlay")

    def cancel_screenshot_capture(self) -> dict[str, Any]:
        with self.lock:
            session = self._screenshot_session
            self._screenshot_session = None
            self._screenshot_cancel_last_at = time.time()
        self._suppress_selection_events(0.7)
        self._close_screenshot_window()
        self._set_global_cursor_crosshair(False)
        self._clear_screenshot_background_image()
        if session is not None:
            self._restore_main_after_screenshot(main_was_hidden=self._main_hidden_flag_for_session(session))
        if session is not None:
            self.set_status("已取消截图")
        return {"ok": True}

    def finish_screenshot_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            session = self._screenshot_session
            if session is None:
                return {"ok": False, "message": "截图会话不存在，请重新开始截图"}
            try:
                payload_session_id = int(payload.get("sessionId", session.session_id))
            except Exception:
                payload_session_id = session.session_id
            if payload_session_id != session.session_id:
                return {"ok": False, "message": "截图会话已过期，请重新开始截图"}
            self._screenshot_session = None

        self._suppress_selection_events(0.7)
        self._close_screenshot_window()
        self._set_global_cursor_crosshair(False)
        self._flush_dwm()
        time.sleep(0.01)

        bounds = session.bounds
        left = int(payload.get("left", 0))
        top = int(payload.get("top", 0))
        right = int(payload.get("right", 0))
        bottom = int(payload.get("bottom", 0))

        left = max(bounds["left"], min(left, bounds["right"]))
        right = max(bounds["left"], min(right, bounds["right"]))
        top = max(bounds["top"], min(top, bounds["bottom"]))
        bottom = max(bounds["top"], min(bottom, bounds["bottom"]))

        width = right - left
        height = bottom - top
        if width < MIN_CAPTURE_SIZE or height < MIN_CAPTURE_SIZE:
            self._clear_screenshot_background_image()
            self._restore_main_after_screenshot(main_was_hidden=self._main_hidden_flag_for_session(session))
            self.set_status("截图区域过小，已取消")
            return {"ok": False, "message": "截图区域过小"}

        image = self._capture_selection_image_from_background(
            bounds=bounds,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )
        if image is None:
            try:
                image = capture_screen_region(ScreenRegion(left=left, top=top, right=right, bottom=bottom))
            except Exception as exc:
                self._clear_screenshot_background_image()
                self._restore_main_after_screenshot(main_was_hidden=self._main_hidden_flag_for_session(session))
                message = self._user_friendly_message(str(exc), scene="screenshot")
                self.set_status(message)
                return {"ok": False, "message": message}
        self._clear_screenshot_background_image()
        self._restore_main_after_screenshot(main_was_hidden=self._main_hidden_flag_for_session(session))
        self.set_status("正在识别截图文字...")
        try:
            anchor_x, anchor_y = get_cursor_position()
        except Exception:
            anchor_x = (left + right) // 2
            anchor_y = (top + bottom) // 2

        if session.show_bubble:
            self._show_bubble(
                source_text="截图 OCR",
                result_text="正在识别文字...",
                pending=True,
                action="截图翻译",
                mode=self.config.translation_mode,
                anchor=(int(anchor_x), int(anchor_y)),
            )

        self._begin_screenshot_ocr_and_translate(image=image, show_bubble=session.show_bubble)
        return {"ok": True}

    def _begin_screenshot_ocr_and_translate(self, image, *, show_bubble: bool) -> None:
        with self.lock:
            self._screenshot_ocr_request_seq += 1
            request_seq = int(self._screenshot_ocr_request_seq)

        def can_show_bubble() -> bool:
            with self.lock:
                if int(request_seq) != int(self._screenshot_ocr_request_seq):
                    return False
                return bool(
                    show_bubble
                    and int(request_seq) > int(self._screenshot_ocr_suppress_bubble_until_seq)
                )

        def worker() -> None:
            try:
                text = self.ocr_service.extract_text(image).strip()
                if not text:
                    raise RuntimeError("截图中未识别到可翻译文本")
                effective_show_bubble = can_show_bubble()
                if not effective_show_bubble:
                    self.bridge.send("main", "screenshot-ocr-ready", {"sourceText": text})
                self._start_translate(text=text, action="截图翻译", show_bubble=effective_show_bubble)
            except Exception as exc:
                message = self._user_friendly_message(str(exc), scene="screenshot")
                self.set_status(message)
                if can_show_bubble():
                    self._update_bubble(
                        "截图 OCR",
                        message,
                        pending=False,
                        action="截图翻译",
                        mode=self.config.translation_mode,
                        refresh_auto_hide=True,
                    )

        threading.Thread(target=worker, daemon=True).start()

    def _restore_main_after_screenshot(self, *, main_was_hidden: bool | None = None) -> None:
        if self.main_window is None:
            return
        if main_was_hidden is None:
            session = self._screenshot_session
            main_was_hidden = self._main_hidden_flag_for_session(session)
        if main_was_hidden:
            self.hidden = True
            return
        if self._is_main_window_visible():
            self.hidden = False
            return
        try:
            self._run_on_window_ui(
                self.main_window,
                lambda: self.main_window.show(),
                wait=True,
                timeout_sec=1.5,
                log_prefix="restore_main_after_screenshot",
            )
            self.hidden = False
        except Exception:
            self.logger.exception("Failed to restore main window after screenshot flow")

    @staticmethod
    def _main_hidden_flag_for_session(session: ScreenshotSession | None) -> bool:
        if session is None:
            return False
        return bool(session.main_was_hidden)

    def _close_screenshot_window(self) -> None:
        try:
            self._native_screenshot_overlay.hide()
        except Exception:
            self.logger.exception("Failed to hide native screenshot overlay")


    def _show_bubble(
        self,
        *,
        source_text: str,
        result_text: str,
        pending: bool,
        action: str,
        mode: str,
        anchor: tuple[int, int] | None = None,
        preserve_position: bool = False,
        preserve_height: bool = False,
        preserve_candidates: bool = False,
        refresh_auto_hide: bool = True,
    ) -> None:
        del refresh_auto_hide
        width = self.BUBBLE_WIDTH
        height = self._estimate_bubble_height(source_text, result_text)
        with self.lock:
            current_x = self._bubble_state.x
            current_y = self._bubble_state.y
            current_height = self._bubble_state.height
        if preserve_height and current_height:
            height = int(current_height)
        if preserve_position and current_x is not None and current_y is not None:
            x, y = int(current_x), int(current_y)
        else:
            x, y = self._bubble_position(anchor, height)

        with self.lock:
            candidate_pending = self._bubble_state.candidate_pending if preserve_candidates else False
            candidate_items = list(self._bubble_state.candidate_items) if preserve_candidates else []
            self._bubble_state = BubbleState(
                visible=True,
                pinned=self._bubble_state.pinned,
                pending=pending,
                action=action,
                mode=mode,
                source_text=source_text,
                result_text=result_text,
                candidate_pending=candidate_pending,
                candidate_items=candidate_items,
                x=x,
                y=y,
                width=width,
                height=height,
            )

        if self.bubble_window is None:
            self.bubble_window = self._create_window(
                kind="bubble",
                title="WordPack Bubble",
                width=width,
                height=height,
                min_size=(self.BUBBLE_WIDTH, self.BUBBLE_HEIGHT),
                x=x,
                y=y,
                frameless=True,
                shadow=True,
                resizable=False,
                on_top=True,
                focus=True,
                transparent=False,
            )
        else:
            try:
                # During streaming updates we preserve geometry and only need
                # to push data to frontend; repeated native move/resize/show
                # calls can cause visible drag jank.
                if preserve_position and preserve_height:
                    pass
                else:
                    self.bubble_window.resize(width, height)
                    self.bubble_window.move(x, y)
                self.bubble_window.show()
            except Exception:
                self.logger.exception("Failed to update bubble window geometry")

        self._emit_bubble_updated()

    def _emit_bubble_updated(self) -> None:
        payload = {
            "themeMode": self._resolved_theme_mode(),
            "bubble": self._bubble_state.to_payload(),
        }
        self.bridge.send("bubble", "bubble-updated", payload)
        self.bridge.send("main", "bubble-updated", payload)

    def _update_bubble(
        self,
        source_text: str,
        result_text: str,
        *,
        pending: bool,
        action: str,
        mode: str,
        refresh_auto_hide: bool = False,
    ) -> None:
        with self.lock:
            self._bubble_state.source_text = source_text
            self._bubble_state.result_text = result_text
            self._bubble_state.pending = pending
            self._bubble_state.action = action
            self._bubble_state.mode = mode

        self._show_bubble(
            source_text=source_text,
            result_text=result_text,
            pending=pending,
            action=action,
            mode=mode,
            preserve_position=True,
            preserve_height=True,
            preserve_candidates=True,
            refresh_auto_hide=refresh_auto_hide,
        )

    def _estimate_bubble_height(self, _source_text: str, result_text: str) -> int:
        del result_text
        return self.BUBBLE_HEIGHT

    def _bubble_position(self, anchor: tuple[int, int] | None, height: int) -> tuple[int, int]:
        bounds = get_virtual_screen_bounds()
        cursor_x, cursor_y = get_cursor_position()
        anchor_x = cursor_x
        anchor_y = cursor_y
        if anchor is not None:
            anchor_x = int(anchor[0])
            anchor_y = int(anchor[1])

        margin_x = 12
        margin_y = 12
        gap_x = 12
        gap_y = 16
        top_gap = 18

        max_x = max(bounds.left + margin_x, bounds.right - self.BUBBLE_WIDTH - margin_x)
        max_y = max(bounds.top + margin_y, bounds.bottom - height - top_gap)

        candidates = [
            (anchor_x + gap_x, anchor_y + gap_y),
            (anchor_x - self.BUBBLE_WIDTH - gap_x, anchor_y + gap_y),
            (anchor_x + gap_x, anchor_y - height - gap_y),
            (anchor_x - self.BUBBLE_WIDTH - gap_x, anchor_y - height - gap_y),
        ]

        best_x = max(bounds.left + margin_x, min(candidates[0][0], max_x))
        best_y = max(bounds.top + margin_y, min(candidates[0][1], max_y))
        best_penalty = None

        for cand_x, cand_y in candidates:
            clamped_x = max(bounds.left + margin_x, min(cand_x, max_x))
            clamped_y = max(bounds.top + margin_y, min(cand_y, max_y))
            penalty = abs(clamped_x - cand_x) + abs(clamped_y - cand_y)
            if best_penalty is None or penalty < best_penalty:
                best_penalty = penalty
                best_x = clamped_x
                best_y = clamped_y

        return int(best_x), int(best_y)

    def toggle_bubble_pin(self) -> dict[str, Any]:
        with self.lock:
            self._bubble_state.pinned = not self._bubble_state.pinned
            payload = self._bubble_state.to_payload()

        if self._bubble_state.pinned:
            self._cancel_bubble_hide_timer()
            self.set_status("已固定悬浮窗")
        else:
            self.set_status("已取消固定悬浮窗")

        self._emit_bubble_updated()
        return {"ok": True, "bubble": payload}

    def open_zoom_from_bubble(self) -> dict[str, Any]:
        self._hide_main_after_zoom_close = bool(self.hidden)
        self.open_zoom_panel()
        self.bridge.send(
            "main",
            "zoom-open",
            {
                "sourceText": self._bubble_state.source_text,
                "resultText": self._bubble_state.result_text,
                "action": self._bubble_state.action,
                "mode": self._bubble_state.mode,
                "origin": "bubble",
            },
        )
        return {"ok": True}

    def open_zoom_panel(self) -> dict[str, Any]:
        if self.main_window is None:
            return {"ok": False}
        if self._main_window_geometry_before_zoom is None:
            current = self._current_main_geometry()
            if current is not None:
                self._main_window_geometry_before_zoom = current

        bounds = get_virtual_screen_bounds()
        width = min(1180, max(820, bounds.width - 80))
        height = min(820, max(620, bounds.height - 80))
        # Zoom panel should open at geometric center (no upward bias).
        x = bounds.left + max(0, (bounds.width - width) // 2)
        y = bounds.top + max(0, (bounds.height - height) // 2)
        self._apply_main_geometry(x, y, width, height)
        return {"ok": True}

    def close_zoom_panel(self) -> dict[str, Any]:
        if self._main_window_geometry_before_zoom is None:
            return {"ok": True}
        x, y, width, height = self._main_window_geometry_before_zoom
        self._main_window_geometry_before_zoom = None
        self._apply_main_geometry(x, y, width, height)
        if self._hide_main_after_zoom_close and self.main_window is not None:
            try:
                self.main_window.hide()
                self.hidden = True
            except Exception:
                self.logger.exception("Failed to restore hidden main window after zoom")
        self._hide_main_after_zoom_close = False
        return {"ok": True}

    def _schedule_bubble_auto_hide(self, delay_sec: float = 4.2) -> None:
        # Bubble auto-hide timer is intentionally disabled now.
        # Bubble close behavior is governed by explicit settings and manual close.
        del delay_sec
        self._cancel_bubble_hide_timer()
        return

    def _cancel_bubble_hide_timer(self) -> None:
        if self._bubble_hide_timer is not None:
            self._bubble_hide_timer.cancel()
            self._bubble_hide_timer = None

    def _selection_candidate_max_age_sec(self) -> float:
        try:
            value = float(self.config.interaction.selection_candidate_max_age_sec or 12.0)
        except Exception:
            value = 12.0
        return max(2.0, min(30.0, value))

    def _is_selection_candidate_fresh(self, candidate: SelectionCandidate | None) -> bool:
        if candidate is None:
            return False
        return candidate.is_fresh(max_age_sec=self._selection_candidate_max_age_sec())

    def _dpi_scale(self) -> float:
        now = time.time()
        if self._dpi_scale_cached_at > 0 and (now - self._dpi_scale_cached_at) <= 5.0:
            return self._dpi_scale_cache
        try:
            scale = float(get_system_dpi_scale())
        except Exception:
            scale = 1.0
        if scale <= 0:
            scale = 1.0
        self._dpi_scale_cache = max(0.75, min(4.0, scale))
        self._dpi_scale_cached_at = now
        return self._dpi_scale_cache

    def _scale_px(self, value: int) -> int:
        base = max(1, int(value))
        return max(1, int(round(base * self._dpi_scale())))

    def _selection_drag_threshold_px(self) -> int:
        base = int(self.config.interaction.selection_drag_min_px or 9)
        return max(6, min(40, base))

    def _selection_click_pair_distance_px(self) -> int:
        return max(4, int(self.config.interaction.selection_click_pair_max_distance_px or 14))

    def _selection_icon_arm_delay_sec(self) -> float:
        try:
            value = int(self.config.interaction.selection_icon_arm_delay_ms or 150)
        except Exception:
            value = 150
        return max(0.0, min(0.8, value / 1000.0))

    def _selection_icon_effective_trigger(self, candidate: SelectionCandidate | None = None) -> str:
        icon_trigger = ""
        if candidate is not None and candidate.icon_trigger in {"click", "hover"}:
            icon_trigger = str(candidate.icon_trigger)
        if not icon_trigger:
            icon_trigger = str(self.config.interaction.selection_icon_trigger or "click")
        return "hover" if icon_trigger == "hover" else "click"

    @staticmethod
    def _normalize_executable_name(executable: str | None) -> str:
        raw = str(executable or "").strip().lower()
        if not raw:
            return ""
        try:
            return Path(raw).name.lower()
        except Exception:
            return raw

    def _resolve_selection_profile(self, executable: str | None = None) -> tuple[str, str, str]:
        global_mode = str(self.config.interaction.selection_trigger_mode or "double_ctrl").strip().lower()
        if global_mode not in {"icon", "double_ctrl", "double_alt", "double_shift"}:
            global_mode = "double_ctrl"
        global_icon_trigger = str(self.config.interaction.selection_icon_trigger or "click").strip().lower()
        if global_icon_trigger not in {"click", "hover"}:
            global_icon_trigger = "click"
        # User-selected global interaction mode is authoritative.
        exe = self._normalize_executable_name(executable or get_foreground_process_name())
        return global_mode, global_icon_trigger, exe

    @staticmethod
    def _selection_payload_int(payload: dict[str, Any], key: str, default: int = 0) -> int:
        try:
            return int(payload.get(key, default))
        except Exception:
            return int(default)

    def _selection_candidate_fingerprint(self, payload: dict[str, Any], *, executable: str, trigger_mode: str) -> str:
        x = self._selection_payload_int(payload, "x")
        y = self._selection_payload_int(payload, "y")
        down_x = self._selection_payload_int(payload, "down_x", x)
        down_y = self._selection_payload_int(payload, "down_y", y)
        moved = self._selection_payload_int(payload, "moved", abs(x - down_x) + abs(y - down_y))
        click_count = self._selection_payload_int(payload, "click_count", 0)
        hold_ms = self._selection_payload_int(payload, "hold_ms", 0)
        quant = max(1, self._scale_px(4))
        parts = [
            str(executable or ""),
            str(trigger_mode or ""),
            str(x // quant),
            str(y // quant),
            str(down_x // quant),
            str(down_y // quant),
            str(moved // quant),
            str(click_count),
            str(hold_ms // 20),
        ]
        return ":".join(parts)

    def _is_duplicate_selection_candidate(self, fingerprint: str, now: float) -> bool:
        if not fingerprint:
            return False
        try:
            dedupe_window = int(self.config.interaction.selection_candidate_dedupe_window_ms or 320)
        except Exception:
            dedupe_window = 320
        # Dedupe is only for near-simultaneous hook/poll duplicates, not for
        # separate user actions in quick succession.
        dedupe_sec = max(0.04, min(0.18, dedupe_window / 1000.0))
        if fingerprint == self._selection_last_fingerprint and (now - self._selection_last_fingerprint_at) <= dedupe_sec:
            return True
        self._selection_last_fingerprint = fingerprint
        self._selection_last_fingerprint_at = now
        return False

    def _set_selection_flow(self, phase: str, *, reason: str = "", candidate: SelectionCandidate | None = None) -> None:
        now = time.time()
        with self.lock:
            token = int(self._selection_flow.token or 0)
            if candidate is not None and candidate.fingerprint and candidate.fingerprint != self._selection_flow.candidate_fingerprint:
                token += 1
            elif phase == "captured":
                token += 1
            self._selection_flow = SelectionFlowState(
                phase=str(phase or "idle"),
                token=token,
                updated_at=now,
                candidate_fingerprint=(candidate.fingerprint if candidate is not None else self._selection_flow.candidate_fingerprint),
                reason=str(reason or ""),
            )

    def _is_candidate_signal_strong(self, candidate: SelectionCandidate) -> bool:
        moved = int(candidate.moved_px or 0)
        payload = candidate.payload or {}
        click_count = self._selection_payload_int(payload, "click_count", 0)
        drag_min = self._selection_drag_threshold_px()
        if moved >= drag_min:
            return True
        click_move_cap = max(self._selection_click_pair_distance_px() * 2, self._scale_px(16))
        if click_count >= 2 and moved <= click_move_cap:
            return True
        return False

    def _build_selection_candidate(self, payload: dict[str, Any] | None, *, now: float | None = None) -> SelectionCandidate | None:
        data: dict[str, Any] = payload if isinstance(payload, dict) else {}
        captured_at = time.time() if now is None else float(now)

        x = self._selection_payload_int(data, "x", 0)
        y = self._selection_payload_int(data, "y", 0)
        down_x = self._selection_payload_int(data, "down_x", x)
        down_y = self._selection_payload_int(data, "down_y", y)
        moved = self._selection_payload_int(data, "moved", abs(x - down_x) + abs(y - down_y))
        click_count = self._selection_payload_int(data, "click_count", 0)
        hold_ms = self._selection_payload_int(data, "hold_ms", 0)
        up_at = float(data.get("up_ts", captured_at) or captured_at)
        down_at_default = up_at - (max(0, hold_ms) / 1000.0)
        down_at = float(data.get("down_ts", down_at_default) or down_at_default)

        effective_mode, effective_icon_trigger, executable = self._resolve_selection_profile()
        if effective_mode == "disabled":
            self._set_selection_flow("cancelled", reason="profile-disabled")
            return None

        candidate_payload = {
            "x": int(x),
            "y": int(y),
            "down_x": int(down_x),
            "down_y": int(down_y),
            "moved": int(moved),
            "click_count": int(click_count),
            "hold_ms": int(hold_ms),
            "down_ts": float(down_at),
            "up_ts": float(up_at),
        }
        fingerprint = self._selection_candidate_fingerprint(
            candidate_payload,
            executable=executable,
            trigger_mode=effective_mode,
        )
        return SelectionCandidate(
            captured_at=captured_at,
            payload=candidate_payload,
            text="",
            down_at=float(down_at),
            up_at=float(up_at),
            moved_px=int(moved),
            executable=executable,
            trigger_mode=effective_mode,
            icon_trigger=effective_icon_trigger,
            fingerprint=fingerprint,
        )

    @staticmethod
    def _selection_trigger_mode_label(trigger_mode: str) -> str:
        mode = str(trigger_mode or "").strip().lower()
        mapping = {
            "double_ctrl": "双击 Ctrl",
            "double_alt": "双击 Alt",
            "double_shift": "双击 Shift",
        }
        return mapping.get(mode, "双击 Ctrl")

    def _can_trigger_selection_by_mode(self, trigger_mode: str) -> bool:
        expected_mode = str(trigger_mode or "").strip().lower()
        if expected_mode not in {"double_ctrl", "double_alt", "double_shift"}:
            return False
        if not bool(self.config.interaction.selection_enabled):
            return False
        candidate = self._selection_candidate
        if self._is_selection_candidate_fresh(candidate):
            return str(candidate.trigger_mode or "icon") == expected_mode
        mode, _icon_trigger, _exe = self._resolve_selection_profile()
        return mode == expected_mode

    def _ensure_selection_candidate_for_translate(self) -> SelectionCandidate | None:
        candidate = self._selection_candidate
        if self._is_selection_candidate_fresh(candidate):
            return candidate

        cursor_x, cursor_y = get_cursor_position()
        fallback_candidate = self._build_selection_candidate(
            {
                "x": int(cursor_x),
                "y": int(cursor_y),
                "down_x": int(cursor_x),
                "down_y": int(cursor_y),
                "moved": 0,
                "click_count": 0,
                "hold_ms": 0,
                "up_ts": time.time(),
            }
        )
        if fallback_candidate is None:
            return None
        with self.lock:
            self._selection_candidate = fallback_candidate
        return fallback_candidate

    def _fast_verify_selection_candidate(self, candidate: SelectionCandidate) -> bool:
        timeout_ms = max(20, min(300, int(self.config.interaction.selection_verify_timeout_ms or 80)))
        result = self.selection_capture.probe_fast(candidate.payload or {}, timeout_ms=timeout_ms)
        candidate.verify_reason = str(result.reason or "")
        candidate.verified_at = time.time()

        if result.has_text():
            candidate.text = str(result.text or "").strip()
            candidate.verified_has_text = True
            return True

        candidate.verified_has_text = False
        if result.reason in {"uia-module-missing", "uia-module-import-failed", "uia-internal-error"}:
            candidate.verified_has_text = None
        # Be conservative: no confirmed text -> no floating icon.
        return False

    def trigger_selection_translate(self) -> dict[str, Any]:
        self._selection_icon_hover_armed = False
        self.close_window("icon")
        return self._translate_pending_selection()

    def _translate_pending_selection(self) -> dict[str, Any]:
        candidate = self._ensure_selection_candidate_for_translate()
        if not self._is_selection_candidate_fresh(candidate):
            message = "未检测到最近选区，请重新划词"
            self.set_status(message)
            self._show_bubble(
                source_text="划词翻译",
                result_text=message,
                pending=False,
                action="划词翻译",
                mode=self.config.translation_mode,
            )
            return {"ok": False, "message": message}

        assert candidate is not None
        # For non-icon trigger modes (double Ctrl/Alt/Shift), bubble should
        # open near the latest cursor position at trigger time.
        if str(candidate.trigger_mode or "icon") != "icon":
            cursor_x, cursor_y = get_cursor_position()
            payload = dict(candidate.payload or {})
            payload["x"] = int(cursor_x)
            payload["y"] = int(cursor_y)
            payload["down_x"] = int(cursor_x)
            payload["down_y"] = int(cursor_y)
            payload["up_ts"] = float(time.time())
            candidate.payload = payload
            with self.lock:
                if self._selection_candidate is not None:
                    self._selection_candidate.payload = dict(payload)

        text = candidate.text.strip() or self._capture_selected_text(candidate.payload).strip()
        if not text:
            message = "未识别到可翻译文本，请重新划词后再试"
            self.set_status(message)
            self._show_bubble(
                source_text="划词翻译",
                result_text=message,
                pending=False,
                action="划词翻译",
                mode=self.config.translation_mode,
            )
            return {"ok": False, "message": message}

        self._selection_candidate.text = text
        self._set_selection_flow("triggered", reason="translate-start", candidate=candidate)
        return self._start_translate(text=text, action="划词翻译", show_bubble=True)

    @staticmethod
    def _is_transient_selection_failure(result: SelectionCaptureResult) -> bool:
        if result.has_text():
            return False
        final_reason = str(result.reason or "").strip().lower()
        uia_reason = str(result.uia_reason or "").strip().lower()
        clipboard_reason = str(result.clipboard_reason or "").strip().lower()
        transient_clipboard_reasons = {
            "clipboard-empty",
            "clipboard-unchanged",
            "clipboard-copy-without-seq-change",
        }
        transient_uia_reasons = {
            "empty-selection",
            "no-textpattern",
            "uia-module-missing",
            "uia-module-import-failed",
            "uia-internal-error",
        }
        if final_reason in transient_clipboard_reasons and uia_reason in transient_uia_reasons:
            return True
        if clipboard_reason in transient_clipboard_reasons and uia_reason in transient_uia_reasons:
            return True
        return False

    def _capture_selected_text(self, payload: dict[str, int] | None) -> str:
        self._wait_selection_runtime_warmup(timeout_sec=0.9)

        result = self.selection_capture.capture(
            self._capture_selection_by_ctrl_c,
            payload=payload,
            wait_sec=0.1,
            allow_unchanged=True,
        )
        if self._is_transient_selection_failure(result):
            self.logger.info("Selection capture transient failure, retrying once | %s", result.diagnostics_summary())
            time.sleep(0.06)
            result = self.selection_capture.capture(
                self._capture_selection_by_ctrl_c,
                payload=payload,
                wait_sec=0.12,
                allow_unchanged=True,
            )
        if result.has_text():
            self.logger.info(
                "Selection capture success scheme=%s control=%s reason=%s",
                result.source or "none",
                result.control_summary(),
                result.reason or "ok",
            )
        else:
            self.logger.warning("Selection capture failed | %s", result.diagnostics_summary())
        return result.text.strip()

    def _capture_selection_by_ctrl_c(
        self,
        *,
        wait_sec: float = 0.1,
        allow_unchanged: bool = False,
    ) -> ClipboardCaptureResult:
        from ctypes import windll

        try:
            backup_text = get_clipboard_text(raw=True)
        except Exception:
            self.logger.exception("Failed to read clipboard backup before Ctrl+C capture")
            backup_text = None
        try:
            seq_before = int(windll.user32.GetClipboardSequenceNumber())
        except Exception:
            seq_before = -1

        selected = ""
        selected_reason = ""
        attempt_notes: list[str] = []
        attempts = 0
        saw_nonempty_copy = False
        saw_unchanged_copy = False

        for attempt_idx in range(3):
            attempts = attempt_idx + 1
            copied = copy_selection_once(wait_sec=wait_sec).strip()
            try:
                seq_after = int(windll.user32.GetClipboardSequenceNumber())
            except Exception:
                seq_after = -1

            seq_changed = seq_before >= 0 and seq_after >= 0 and seq_after != seq_before
            if copied:
                saw_nonempty_copy = True

            if copied and seq_changed:
                selected = copied
                selected_reason = "clipboard-seq-changed"
                attempt_notes.append(f"attempt={attempts}:captured-seq-changed len={len(selected)}")
                break

            if allow_unchanged and copied and backup_text is not None and copied != backup_text.strip():
                selected = copied
                selected_reason = "clipboard-differs-from-backup"
                attempt_notes.append(f"attempt={attempts}:captured-different-from-backup len={len(selected)}")
                break

            if copied and not seq_changed:
                saw_unchanged_copy = True
                attempt_notes.append(f"attempt={attempts}:copied-but-seq-unchanged len={len(copied)}")
            else:
                attempt_notes.append(f"attempt={attempts}:empty")
            time.sleep(0.04)

        restored = True
        if backup_text is not None:
            restored = set_clipboard_text(backup_text)

        detail_parts = []
        if attempt_notes:
            detail_parts.append("; ".join(attempt_notes))
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
        if saw_unchanged_copy:
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

    def _on_hook_event(self, event: str, payload) -> None:
        if self._shutting_down:
            return

        if event == "status":
            self.set_status(str(payload))
            return

        if event in {"double_ctrl_selection", "double_alt_selection", "double_shift_selection"}:
            if self._screenshot_session is not None or self._selection_events_suppressed():
                return
            trigger_event_mode = {
                "double_ctrl_selection": "double_ctrl",
                "double_alt_selection": "double_alt",
                "double_shift_selection": "double_shift",
            }.get(str(event), "double_ctrl")
            if self._can_trigger_selection_by_mode(trigger_event_mode) and not self._is_our_window_foreground():
                self._trigger_selection_translate_debounced(source=f"hook:{event}")
            return

        if event == "selection_mouse_up":
            if self._screenshot_session is not None or self._selection_events_suppressed():
                return
            self._emit_selection_candidate(payload)
            return

        if event == "screenshot_translate":
            screenshot_enabled = bool(self.config.interaction.screenshot_enabled)
            screenshot_hotkey = normalize_shortcut(self.config.interaction.screenshot_hotkey)
            self.logger.info(
                "Hotkey event received event=screenshot_translate enabled=%s hotkey=%s session_active=%s",
                screenshot_enabled,
                screenshot_hotkey or "none",
                bool(self._screenshot_session is not None),
            )
            if self._screenshot_session is not None:
                return
            if screenshot_enabled and screenshot_hotkey:
                self.start_screenshot_capture(show_bubble=True)
            return

        if event == "restore_bubble":
            self.restore_recent_fast_closed_bubble()
            return

        if event == "toggle_main_window":
            self.toggle_main_window_visibility()
            return

    def _selection_translate_trigger_allowed(self, *, min_interval_sec: float = 0.32) -> bool:
        now = time.time()
        with self.lock:
            last = float(getattr(self, "_last_selection_translate_trigger_at", 0.0) or 0.0)
            if (now - last) < max(0.08, float(min_interval_sec)):
                return False
            self._last_selection_translate_trigger_at = now
        return True

    def _trigger_selection_translate_debounced(self, *, source: str) -> None:
        if not self._selection_translate_trigger_allowed():
            self.logger.info("Selection translate trigger suppressed by debounce source=%s", source)
            return
        try:
            self._translate_pending_selection()
        except Exception:
            self.logger.exception("Selection translate trigger failed source=%s", source)

    def _handle_selection_candidate(self, candidate: SelectionCandidate) -> None:
        if self._screenshot_session is not None:
            return
        if self._selection_events_suppressed():
            return
        if not self.config.interaction.selection_enabled:
            return
        if time.time() - self._last_window_interaction_at <= 0.75 and self._is_our_window_foreground():
            return

        with self.lock:
            self._selection_candidate = candidate
        self._set_selection_flow("captured", reason=f"mode={candidate.trigger_mode}", candidate=candidate)
        self._selection_icon_retry = 0

        if candidate.trigger_mode == "icon":
            # Replace stale/previous icon immediately when a new selection is captured.
            if self._native_icon_overlay.is_visible():
                self.close_window("icon")
            self.set_status("已捕获选区，正在确认文本")
            self._schedule_selection_icon()
        else:
            self.set_status(f"已捕获选区，{self._selection_trigger_mode_label(candidate.trigger_mode)} 触发翻译")
            self.close_window("icon")

    def _emit_selection_candidate(self, payload) -> None:
        now = time.time()
        if self._selection_events_suppressed(now):
            return
        if now - self._last_selection_trigger_at < 0.03:
            return
        candidate = self._build_selection_candidate(payload, now=now)
        if candidate is None:
            return
        if not self._is_candidate_signal_strong(candidate):
            self._set_selection_flow("cancelled", reason="weak-signal", candidate=candidate)
            return
        if self._is_duplicate_selection_candidate(candidate.fingerprint, now):
            self._set_selection_flow("cancelled", reason="deduped", candidate=candidate)
            return
        self._last_selection_trigger_at = now
        self._handle_selection_candidate(candidate)

    def _schedule_selection_icon(self, delay_sec: float | None = None) -> None:
        self._cancel_selection_icon_timer()
        delay = delay_sec if delay_sec is not None else max(0.0, min(5.0, self.config.interaction.selection_icon_delay_ms / 1000.0))
        if delay <= 0.001:
            self._maybe_show_selection_icon()
            return
        self._selection_icon_timer = threading.Timer(delay, self._maybe_show_selection_icon)
        self._selection_icon_timer.daemon = True
        self._selection_icon_timer.start()

    def _cancel_selection_icon_timer(self) -> None:
        if self._selection_icon_timer is not None:
            self._selection_icon_timer.cancel()
            self._selection_icon_timer = None

    def _schedule_selection_icon_auto_hide(self) -> None:
        self._cancel_selection_icon_hide_timer()
        self._selection_icon_hide_timer = threading.Timer(4.2, lambda: self.close_window("icon"))
        self._selection_icon_hide_timer.daemon = True
        self._selection_icon_hide_timer.start()

    def _cancel_selection_icon_hide_timer(self) -> None:
        if self._selection_icon_hide_timer is not None:
            self._selection_icon_hide_timer.cancel()
            self._selection_icon_hide_timer = None

    def _maybe_show_selection_icon(self) -> None:
        self._selection_icon_timer = None
        candidate = self._selection_candidate
        if not self._is_selection_candidate_fresh(candidate):
            self._set_selection_flow("cancelled", reason="candidate-expired", candidate=candidate)
            return
        if str(candidate.trigger_mode or "icon") != "icon":
            return
        if self._native_icon_overlay.is_visible():
            return

        arm_delay = self._selection_icon_arm_delay_sec()
        anchor_time = float(candidate.up_at or candidate.captured_at or time.time())
        remaining = arm_delay - max(0.0, time.time() - anchor_time)
        if remaining > 0.01:
            self._schedule_selection_icon(remaining)
            return

        if candidate.verified_at <= 0:
            verify_ok = self._fast_verify_selection_candidate(candidate)
            if not verify_ok:
                self._set_selection_flow(
                    "cancelled",
                    reason=f"preverify-failed:{candidate.verify_reason or 'unknown'}",
                    candidate=candidate,
                )
                return
            self._set_selection_flow("verified", reason=candidate.verify_reason or "ok", candidate=candidate)

        if time.time() - self._last_window_interaction_at <= 0.75 and self._is_our_window_foreground():
            if self._selection_icon_retry < 18:
                self._selection_icon_retry += 1
                self._schedule_selection_icon(0.12)
            return

        shown = self._show_selection_icon(candidate.payload or {})
        if shown:
            self._selection_icon_retry = 0
            self._set_selection_flow("icon_shown", reason="icon-visible", candidate=candidate)
            return
        if self._selection_icon_retry < 10:
            self._selection_icon_retry += 1
            self._schedule_selection_icon(0.08)
            return
        self._set_selection_flow("cancelled", reason="icon-show-failed", candidate=candidate)

    def _show_selection_icon(self, payload: dict[str, int]) -> bool:
        bounds = get_virtual_screen_bounds()
        cursor_x, cursor_y = get_cursor_position()
        anchor_x = int(payload.get("x", cursor_x))
        anchor_y = int(payload.get("y", cursor_y))
        if anchor_x == 0 and anchor_y == 0:
            anchor_x, anchor_y = cursor_x, cursor_y
        x = anchor_x + 12
        y = anchor_y - (self.ICON_HEIGHT // 2)
        x = max(bounds.left + 4, min(x, bounds.right - self.ICON_WIDTH - 4))
        y = max(bounds.top + 4, min(y, bounds.bottom - self.ICON_HEIGHT - 4))
        self.close_window("icon")
        self._native_icon_overlay.show(int(x), int(y))
        if not self._native_icon_overlay.is_visible():
            for _ in range(4):
                time.sleep(0.03)
                self._native_icon_overlay.show(int(x), int(y))
                if self._native_icon_overlay.is_visible():
                    break
        if not self._native_icon_overlay.is_visible():
            return False
        self._selection_icon_anchor_pos = (int(x + (self.ICON_WIDTH / 2)), int(y + (self.ICON_HEIGHT / 2)))
        self._selection_icon_shown_at = time.time()
        self._selection_icon_hover_armed = True
        self._selection_hover_entered_at = 0.0
        self._selection_hover_last_cursor = None
        self._selection_hover_last_at = 0.0
        self._schedule_selection_icon_auto_hide()
        return True

    def close_window(self, kind: str) -> dict[str, Any]:
        if kind == "main":
            if self.main_window is not None:
                try:
                    self._run_on_window_ui(
                        self.main_window,
                        lambda: self.main_window.hide(),
                        wait=True,
                        timeout_sec=1.2,
                        log_prefix="close_window.main.hide",
                    )
                    self.hidden = True
                    # On Windows 11, switching extended styles while the window is
                    # still visible can cause a one-frame flash. Hide first, then
                    # update taskbar visibility.
                    self._set_main_window_taskbar_visibility(False, wait=True)
                    return {"ok": True, "hidden": True}
                except Exception:
                    self.logger.exception("Failed to hide main window")
                    return {"ok": False, "hidden": False}
            return {"ok": True, "hidden": True}

        if kind == "bubble":
            with self.lock:
                self._active_translate_shows_bubble = False
                self._bubble_state.visible = False
                self._bubble_state.pending = False
                self._bubble_state.pinned = False
                self._bubble_fast_close_pending_at = 0.0
                self._bubble_fast_close_pending_distance = 0
                self._screenshot_ocr_suppress_bubble_until_seq = max(
                    int(self._screenshot_ocr_suppress_bubble_until_seq),
                    int(self._screenshot_ocr_request_seq),
                )
                window = self.bubble_window
                self.bubble_window = None
        elif kind == "tray":
            self._cancel_tray_show_timer()
            if self.tray_window is not None:
                try:
                    self.tray_window.hide()
                    return {"ok": True, "hidden": True}
                except Exception:
                    self.logger.exception("Failed to hide tray window")
                    return {"ok": False, "hidden": False}
            return {"ok": True, "hidden": True}
        elif kind == "icon":
            self._cancel_selection_icon_hide_timer()
            self._native_icon_overlay.hide()
            window = self.icon_window
            self.icon_window = None
            self._selection_icon_anchor_pos = None
            self._selection_icon_shown_at = 0.0
            self._selection_icon_hover_armed = False
            self._selection_hover_entered_at = 0.0
            self._selection_hover_last_cursor = None
            self._selection_hover_last_at = 0.0
        elif kind == "screenshot":
            session = self._screenshot_session
            self._close_screenshot_window()
            self._set_global_cursor_crosshair(False)
            window = None
            if session is not None:
                self._restore_main_after_screenshot(main_was_hidden=self._main_hidden_flag_for_session(session))
            self._screenshot_session = None
            self._clear_screenshot_background_image()
        else:
            window = None

        if window is not None:
            try:
                window.destroy()
            except Exception:
                self.logger.exception("Failed to close %s window", kind)

        return {"ok": True}

    def toggle_main_window_visibility(self) -> dict[str, Any]:
        if self.main_window is None:
            return {"ok": False, "message": "主窗口不可用"}
        if self._is_main_window_visible():
            result = self.close_window("main")
            self.set_status("主窗口已隐藏到托盘")
            return result
        try:
            self._set_main_window_taskbar_visibility(True, wait=True)
            self._run_on_window_ui(
                self.main_window,
                lambda: self.main_window.show(),
                wait=True,
                timeout_sec=1.2,
                log_prefix="toggle_main_window_visibility.show",
            )
            self.hidden = False
            try:
                self.close_window("tray")
            except Exception:
                pass
            self.set_status("主窗口已显示")
            return {"ok": True, "hidden": False}
        except Exception:
            self.logger.exception("Failed to show main window via hotkey")
            return {"ok": False, "message": "主窗口显示失败"}

    def _start_input_polling(self) -> None:
        if self._input_poll_thread is not None and self._input_poll_thread.is_alive():
            return
        self._input_poll_stop.clear()
        self._input_poll_thread = threading.Thread(target=self._poll_input_loop, name="wordpack-input-poll", daemon=True)
        self._input_poll_thread.start()

    def _selection_icon_cancel_distance(self) -> int:
        return max(220, self._scale_px(320))

    def _selection_icon_trigger_distance(self) -> int:
        base = max(10, int(self.config.interaction.selection_click_pair_max_distance_px or 14))
        return max(10, self._scale_px(base))

    def _is_cursor_on_selection_anchor(self, cursor_pos: tuple[int, int], payload: dict[str, int] | None) -> bool:
        if not payload:
            return False

        up_x = int(payload.get("x", 0))
        up_y = int(payload.get("y", 0))
        down_x = int(payload.get("down_x", up_x))
        down_y = int(payload.get("down_y", up_y))

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

    def _get_window_rect(self, window, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        native = getattr(window, "native", None) if window is not None else None
        handle = getattr(native, "Handle", None)
        if handle is None:
            return fallback

        rect = wintypes.RECT()
        hwnd = self._native_handle_to_int(handle)
        if not hwnd:
            return fallback
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        return fallback

    def _window_hwnd(self, window) -> int | None:
        native = getattr(window, "native", None) if window is not None else None
        handle = getattr(native, "Handle", None)
        if handle is None:
            return None
        return self._native_handle_to_int(handle)

    def _is_window_visible(self, window) -> bool:
        hwnd = self._window_hwnd(window)
        if not hwnd:
            return False
        try:
            visible = bool(ctypes.windll.user32.IsWindowVisible(hwnd))
            minimized = bool(ctypes.windll.user32.IsIconic(hwnd))
            return visible and (not minimized)
        except Exception:
            return False

    def _is_main_window_visible(self) -> bool:
        if self.main_window is None:
            return False
        if self._window_hwnd(self.main_window):
            return self._is_window_visible(self.main_window)
        # Keep a soft fallback when native visibility probing is unavailable.
        return (not bool(self.hidden))

    def _is_our_window_foreground(self) -> bool:
        try:
            foreground_hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            return False
        if foreground_hwnd <= 0:
            return False

        for window in (self.main_window, self.bubble_window, self.icon_window):
            hwnd = self._window_hwnd(window)
            if hwnd and hwnd == foreground_hwnd:
                return True
        return False

    def _is_cursor_inside_bubble(self) -> bool:
        with self.lock:
            window = self.bubble_window
            fallback = (
                int(self._bubble_state.x or 0),
                int(self._bubble_state.y or 0),
                int((self._bubble_state.x or 0) + (self._bubble_state.width or self.BUBBLE_WIDTH)),
                int((self._bubble_state.y or 0) + (self._bubble_state.height or 0)),
            )
            pinned = self._bubble_state.pinned

        if window is None or pinned:
            return False

        left, top, right, bottom = self._get_window_rect(window, fallback)
        cursor_x, cursor_y = get_cursor_position()
        return left <= cursor_x <= right and top <= cursor_y <= bottom

    def _bubble_close_on_fast_mouse_leave_enabled(self) -> bool:
        return self._bubble_fast_close_profile() != "off"

    def _bubble_fast_close_profile(self) -> str:
        value = str(getattr(self.config.interaction, "bubble_fast_close_profile", "") or "").strip().lower()
        if value in {"off", "loose", "standard", "aggressive"}:
            return value
        legacy = bool(getattr(self.config.interaction, "bubble_close_on_fast_mouse_leave", False))
        return "standard" if legacy else "off"

    def _bubble_fast_close_params(self) -> dict[str, float]:
        profile = self._bubble_fast_close_profile()
        defaults = {
            "edge_padding": 16.0,
            "min_speed_px_s": 1050.0,
            "min_distance_px": 80.0,
            "min_step_px": 30.0,
            "min_distance_growth_px": 6.0,
            "min_direction_cos": 0.18,
            "confirm_delay_sec": 0.09,
            "confirm_window_sec": 0.42,
            "confirm_min_distance_ratio": 0.68,
            "interaction_cooldown_sec": 0.95,
        }
        if profile == "aggressive":
            return {
                **defaults,
                "edge_padding": 10.0,
                "min_speed_px_s": 850.0,
                "min_distance_px": 60.0,
                "min_step_px": 24.0,
                "min_distance_growth_px": 4.0,
                "min_direction_cos": 0.08,
                "confirm_delay_sec": 0.07,
                "confirm_window_sec": 0.36,
                "confirm_min_distance_ratio": 0.55,
                "interaction_cooldown_sec": 0.75,
            }
        if profile == "loose":
            return {
                **defaults,
                "edge_padding": 24.0,
                "min_speed_px_s": 1200.0,
                "min_distance_px": 96.0,
                "min_step_px": 36.0,
                "min_distance_growth_px": 7.0,
                "min_direction_cos": 0.24,
                "confirm_delay_sec": 0.1,
                "confirm_window_sec": 0.48,
                "confirm_min_distance_ratio": 0.75,
                "interaction_cooldown_sec": 1.1,
            }
        return defaults

    def _bubble_is_interactive_now(self) -> bool:
        with self.lock:
            if self.bubble_window is None:
                return False
            if bool(self._bubble_state.pending or self._bubble_state.candidate_pending):
                return True
        return False

    def _bubble_close_on_click_outside_enabled(self) -> bool:
        return bool(getattr(self.config.interaction, "bubble_close_on_click_outside", False))

    def _maybe_close_bubble_by_outside_click(self, cursor_pos: tuple[int, int]) -> None:
        if not self._bubble_close_on_click_outside_enabled():
            return
        with self.lock:
            if self.bubble_window is None or self._bubble_state.pinned:
                return
        if self._cursor_distance_to_bubble(cursor_pos) > 0:
            self._close_bubble_with_restore_hint()

    def _cursor_distance_to_bubble(self, cursor_pos: tuple[int, int], *, edge_padding: int = 0) -> int:
        with self.lock:
            window = self.bubble_window
            fallback = (
                int(self._bubble_state.x or 0),
                int(self._bubble_state.y or 0),
                int((self._bubble_state.x or 0) + (self._bubble_state.width or self.BUBBLE_WIDTH)),
                int((self._bubble_state.y or 0) + (self._bubble_state.height or 0)),
            )
            pinned = self._bubble_state.pinned

        if window is None or pinned:
            return 0

        left, top, right, bottom = self._get_window_rect(window, fallback)
        if edge_padding > 0:
            left -= int(edge_padding)
            top -= int(edge_padding)
            right += int(edge_padding)
            bottom += int(edge_padding)
        cx, cy = int(cursor_pos[0]), int(cursor_pos[1])
        dx = 0 if left <= cx <= right else (left - cx if cx < left else cx - right)
        dy = 0 if top <= cy <= bottom else (top - cy if cy < top else cy - bottom)
        return dx + dy

    def _maybe_hide_selection_icon_by_cursor(self, cursor_pos: tuple[int, int]) -> None:
        if not self._native_icon_overlay.is_visible() or self._selection_icon_anchor_pos is None:
            return
        if self._selection_icon_shown_at > 0 and (time.time() - self._selection_icon_shown_at) < 0.5:
            return

        dx = abs(int(cursor_pos[0]) - int(self._selection_icon_anchor_pos[0]))
        dy = abs(int(cursor_pos[1]) - int(self._selection_icon_anchor_pos[1]))
        if dx + dy >= self._selection_icon_cancel_distance():
            self.close_window("icon")

    def _maybe_trigger_selection_icon_hover(self, cursor_pos: tuple[int, int]) -> None:
        if not self._selection_icon_hover_armed:
            return
        if not self._native_icon_overlay.is_visible() or self._selection_icon_anchor_pos is None:
            return

        candidate = self._selection_candidate
        if self._selection_icon_effective_trigger(candidate) != "hover":
            return

        dx = int(cursor_pos[0]) - int(self._selection_icon_anchor_pos[0])
        dy = int(cursor_pos[1]) - int(self._selection_icon_anchor_pos[1])
        # Hover should match "move to icon": use icon-body rectangle with tiny
        # tolerance instead of large radius.
        tol = 2
        half_w = max(8, int(self.ICON_WIDTH // 2) + tol)
        half_h = max(8, int(self.ICON_HEIGHT // 2) + tol)
        inside = abs(dx) <= half_w and abs(dy) <= half_h
        now = time.time()
        if not inside:
            self._selection_hover_entered_at = 0.0
            self._selection_hover_last_cursor = cursor_pos
            self._selection_hover_last_at = now
            return

        speed_px_s = 0.0
        if self._selection_hover_last_cursor is not None and self._selection_hover_last_at > 0:
            step = abs(int(cursor_pos[0]) - int(self._selection_hover_last_cursor[0])) + abs(
                int(cursor_pos[1]) - int(self._selection_hover_last_cursor[1])
            )
            dt = now - self._selection_hover_last_at
            if dt > 0:
                speed_px_s = step / max(dt, 0.001)
        self._selection_hover_last_cursor = cursor_pos
        self._selection_hover_last_at = now

        max_speed = max(120, int(self.config.interaction.selection_hover_max_speed_px_s or 900))
        if speed_px_s > max_speed:
            self._selection_hover_entered_at = 0.0
            return

        dwell_sec = max(0.0, min(0.5, int(self.config.interaction.selection_hover_dwell_ms or 130) / 1000.0))
        if self._selection_hover_entered_at <= 0:
            self._selection_hover_entered_at = now
            return
        if dwell_sec > 0 and (now - self._selection_hover_entered_at) < dwell_sec:
            return

        self._selection_icon_hover_armed = False
        self._selection_hover_entered_at = 0.0
        threading.Thread(target=self.trigger_selection_translate, daemon=True).start()

    def _capture_bubble_snapshot(self) -> dict[str, Any] | None:
        with self.lock:
            if self.bubble_window is None or not bool(self._bubble_state.visible):
                return None
            return {
                "source_text": str(self._bubble_state.source_text or ""),
                "result_text": str(self._bubble_state.result_text or ""),
                "pending": bool(self._bubble_state.pending),
                "action": str(self._bubble_state.action or "划词翻译"),
                "mode": str(self._bubble_state.mode or self.config.translation_mode),
                "x": int(self._bubble_state.x or 0),
                "y": int(self._bubble_state.y or 0),
                "height": int(self._bubble_state.height or self.BUBBLE_HEIGHT),
                "candidate_pending": bool(self._bubble_state.candidate_pending),
                "candidate_items": list(self._bubble_state.candidate_items or []),
            }

    def _close_bubble_by_fast_leave(self) -> None:
        self._close_bubble_with_restore_hint()

    def _close_bubble_with_restore_hint(self) -> None:
        snapshot = self._capture_bubble_snapshot()
        if snapshot is not None:
            with self.lock:
                self._bubble_last_fast_closed_snapshot = snapshot
                self._bubble_last_fast_closed_at = time.time()
        self.close_window("bubble")
        restore_hotkey = normalize_shortcut(str(getattr(self.config.interaction, "bubble_restore_hotkey", "") or ""))
        if restore_hotkey:
            self.set_status(f"气泡已关闭（可按 {restore_hotkey} 恢复）")
        else:
            self.set_status("气泡已关闭")

    def restore_recent_fast_closed_bubble(self) -> dict[str, Any]:
        with self.lock:
            snapshot = dict(self._bubble_last_fast_closed_snapshot or {}) if self._bubble_last_fast_closed_snapshot else None
            closed_at = float(self._bubble_last_fast_closed_at or 0.0)
            already_visible = self.bubble_window is not None
        if snapshot is None:
            return {"ok": False, "message": "没有可恢复的气泡"}
        if already_visible:
            return {"ok": False, "message": "当前已有气泡"}
        if (time.time() - closed_at) > 2.0:
            return {"ok": False, "message": "可撤销窗口已过期"}

        self._show_bubble(
            source_text=str(snapshot.get("source_text", "") or ""),
            result_text=str(snapshot.get("result_text", "") or ""),
            pending=bool(snapshot.get("pending", False)),
            action=str(snapshot.get("action", "划词翻译") or "划词翻译"),
            mode=str(snapshot.get("mode", self.config.translation_mode) or self.config.translation_mode),
            anchor=(int(snapshot.get("x", 0) or 0), int(snapshot.get("y", 0) or 0)),
        )
        with self.lock:
            self._bubble_state.candidate_pending = bool(snapshot.get("candidate_pending", False))
            self._bubble_state.candidate_items = list(snapshot.get("candidate_items", []) or [])
            self._bubble_last_fast_closed_snapshot = None
            self._bubble_last_fast_closed_at = 0.0
        self._emit_bubble_updated()
        self.set_status("已恢复气泡")
        return {"ok": True}

    def _maybe_hide_bubble_by_cursor(
        self,
        cursor_pos: tuple[int, int],
        cursor_step: int,
        cursor_dt: float,
        cursor_dx: int,
        cursor_dy: int,
        *,
        lbtn_down: bool,
    ) -> None:
        if not self._bubble_close_on_fast_mouse_leave_enabled():
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return
        # Dragging bubble (left button pressed) should never be treated as
        # "quick mouse leave", otherwise window-drag can close the bubble.
        if lbtn_down:
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return
        params = self._bubble_fast_close_params()
        # Skip a short cooldown right after any window interaction (e.g. drag/select/scroll start).
        if (time.time() - float(self._last_window_interaction_at or 0.0)) <= float(params["interaction_cooldown_sec"]) and self._is_our_window_foreground():
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return
        if self._bubble_is_interactive_now():
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return
        with self.lock:
            if self.bubble_window is None or self._bubble_state.pinned:
                self._bubble_fast_close_pending_at = 0.0
                self._bubble_fast_close_pending_distance = 0
                return

        edge_padding = int(params["edge_padding"])
        if self._cursor_distance_to_bubble(cursor_pos, edge_padding=edge_padding) <= 0:
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return
        if cursor_step <= 0 or cursor_dt <= 0 or (cursor_dx == 0 and cursor_dy == 0):
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return

        distance = self._cursor_distance_to_bubble(cursor_pos, edge_padding=edge_padding)
        previous_cursor = (int(cursor_pos[0]) - int(cursor_dx), int(cursor_pos[1]) - int(cursor_dy))
        prev_distance = self._cursor_distance_to_bubble(previous_cursor, edge_padding=edge_padding)
        speed = cursor_step / max(cursor_dt, 0.001)
        now = time.time()
        pending_at = float(self._bubble_fast_close_pending_at or 0.0)
        if pending_at > 0.0:
            elapsed = now - pending_at
            min_hold_distance = max(24.0, float(params["min_distance_px"]) * float(params["confirm_min_distance_ratio"]))
            if elapsed > float(params["confirm_window_sec"]) or distance < min_hold_distance:
                self._bubble_fast_close_pending_at = 0.0
                self._bubble_fast_close_pending_distance = 0
                return
            if elapsed >= float(params["confirm_delay_sec"]):
                self._bubble_fast_close_pending_at = 0.0
                self._bubble_fast_close_pending_distance = 0
                self._close_bubble_by_fast_leave()
            return

        if speed < float(params["min_speed_px_s"]):
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return
        if cursor_step < float(params["min_step_px"]) or distance < float(params["min_distance_px"]):
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return
        if (distance - prev_distance) < float(params["min_distance_growth_px"]):
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return

        with self.lock:
            bubble_center_x = int((self._bubble_state.x or 0) + (self._bubble_state.width or self.BUBBLE_WIDTH) // 2)
            bubble_center_y = int((self._bubble_state.y or 0) + (self._bubble_state.height or self.BUBBLE_HEIGHT) // 2)
        vec_x = int(cursor_pos[0]) - bubble_center_x
        vec_y = int(cursor_pos[1]) - bubble_center_y
        move_norm = max(1.0, (float(cursor_dx * cursor_dx + cursor_dy * cursor_dy) ** 0.5))
        away_norm = max(1.0, (float(vec_x * vec_x + vec_y * vec_y) ** 0.5))
        direction_cos = ((float(cursor_dx * vec_x + cursor_dy * vec_y)) / (move_norm * away_norm))
        if direction_cos < float(params["min_direction_cos"]):
            self._bubble_fast_close_pending_at = 0.0
            self._bubble_fast_close_pending_distance = 0
            return

        self._bubble_fast_close_pending_at = now
        self._bubble_fast_close_pending_distance = int(distance)

    def _shortcut_is_active(
        self,
        shortcut: str,
        *,
        ctrl_down: bool,
        alt_down: bool,
        shift_down: bool,
        key_state_getter,
    ) -> bool:
        parsed = parse_shortcut(shortcut)
        if not parsed:
            return False
        modifiers, vk, _label = parsed
        if bool(modifiers & 0x0002) != bool(ctrl_down):
            return False
        if bool(modifiers & 0x0001) != bool(alt_down):
            return False
        if bool(modifiers & 0x0004) != bool(shift_down):
            return False
        return bool(key_state_getter(vk) & 0x8000)

    @staticmethod
    def _ctrl_combo_in_progress(key_state_getter) -> bool:
        combo_vks = (
            # Common editing and navigation shortcuts.
            0x08, 0x09, 0x0D, 0x1B, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
            # 0-9
            *range(0x30, 0x3A),
            # A-Z
            *range(0x41, 0x5B),
            # Function keys
            *range(0x70, 0x7C),
        )
        for vk in combo_vks:
            if key_state_getter(vk) & 0x8000:
                return True
        return False

    def _auto_close_bubble_if_allowed(self) -> None:
        # Bubble auto-hide timer is intentionally disabled now.
        self._bubble_hide_timer = None
        return

    def _poll_input_loop(self) -> None:
        from ctypes import Structure, byref, windll
        from ctypes import wintypes

        class POINT(Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        VK_LBUTTON = 0x01
        VK_RBUTTON = 0x02
        VK_CONTROL = 0x11
        VK_ESCAPE = 0x1B
        VK_MENU = 0x12
        VK_SHIFT = 0x10

        while not self._input_poll_stop.is_set():
            try:
                now = time.time()
                point = POINT()
                has_pos = bool(windll.user32.GetCursorPos(byref(point)))
                cursor_x = int(point.x) if has_pos else 0
                cursor_y = int(point.y) if has_pos else 0
                current_cursor = (cursor_x, cursor_y)
                cursor_step = 0
                cursor_dt = 0.0
                cursor_dx = 0
                cursor_dy = 0
                if self._cursor_last_pos is None:
                    self._cursor_last_pos = current_cursor
                    self._cursor_last_move_at = now
                elif current_cursor != self._cursor_last_pos:
                    cursor_dx = int(current_cursor[0] - self._cursor_last_pos[0])
                    cursor_dy = int(current_cursor[1] - self._cursor_last_pos[1])
                    cursor_step = abs(cursor_dx) + abs(cursor_dy)
                    cursor_dt = now - self._cursor_last_move_at if self._cursor_last_move_at > 0 else 0.0
                    self._cursor_last_pos = current_cursor
                    self._cursor_last_move_at = now

                lbtn_down = bool(windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
                rbtn_down = bool(windll.user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
                ctrl_down = bool(windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                escape_down = bool(windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
                alt_down = bool(windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                shift_down = bool(windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                screenshot_session = self._screenshot_session
                screenshot_active = screenshot_session is not None
                screenshot_guard = screenshot_active or self._screenshot_starting or self._selection_events_suppressed(now)
                if screenshot_active:
                    self._set_global_cursor_crosshair(True)

                if screenshot_active and float(screenshot_session.started_at or 0.0) > 0:
                    if (now - float(screenshot_session.started_at)) >= 45.0:
                        self.logger.warning("poll_cancel_screenshot: watchdog-timeout")
                        self.cancel_screenshot_capture()
                        screenshot_active = False

                if not self._fb_last_lbtn_down and lbtn_down:
                    if not screenshot_guard:
                        self._maybe_close_bubble_by_outside_click(current_cursor)
                    self._fb_lbtn_down_pos = (cursor_x, cursor_y)
                    self._fb_lbtn_down_at = now

                if self._fb_last_lbtn_down and not lbtn_down:
                    if (not screenshot_guard) and self.config.interaction.selection_enabled:
                        down = self._fb_lbtn_down_pos
                        dx = abs(cursor_x - down[0]) if down else 0
                        dy = abs(cursor_y - down[1]) if down else 0
                        moved = dx + dy
                        hold_ms = int(max(0.0, (now - float(self._fb_lbtn_down_at or now))) * 1000.0)
                        pair_distance = 0
                        if self._fb_last_lbtn_up_pos is not None:
                            pair_distance = abs(cursor_x - int(self._fb_last_lbtn_up_pos[0])) + abs(
                                cursor_y - int(self._fb_last_lbtn_up_pos[1])
                            )

                        if (
                            now - self._fb_last_lbtn_up_at <= 0.35
                            and pair_distance <= self._selection_click_pair_distance_px()
                        ):
                            self._fb_lbtn_click_count += 1
                        else:
                            self._fb_lbtn_click_count = 1
                        self._fb_last_lbtn_up_at = now
                        self._fb_last_lbtn_up_pos = (cursor_x, cursor_y)

                        drag_min = self._selection_drag_threshold_px()
                        click_move_cap = max(self._selection_click_pair_distance_px() * 2, self._scale_px(16))
                        strong_drag = moved >= drag_min
                        strong_double_click = self._fb_lbtn_click_count >= 2 and moved <= click_move_cap
                        if strong_drag or strong_double_click:
                            down_x = int(down[0]) if down else int(cursor_x)
                            down_y = int(down[1]) if down else int(cursor_y)
                            self._emit_selection_candidate(
                                {
                                    "x": int(cursor_x),
                                    "y": int(cursor_y),
                                    "down_x": down_x,
                                    "down_y": down_y,
                                    "moved": int(moved),
                                    "click_count": int(self._fb_lbtn_click_count),
                                    "down_ts": float(self._fb_lbtn_down_at or 0.0),
                                    "up_ts": float(now),
                                    "hold_ms": int(hold_ms),
                                }
                            )
                            self._fb_lbtn_click_count = 0
                    self._fb_lbtn_down_pos = None
                    self._fb_lbtn_down_at = 0.0

                if (not screenshot_guard) and ctrl_down and not self._fb_last_ctrl_down:
                    self._fb_ctrl_combo_used = False

                if (not screenshot_guard) and ctrl_down and self._ctrl_combo_in_progress(windll.user32.GetAsyncKeyState):
                    self._fb_ctrl_combo_used = True

                if (not screenshot_guard) and self._fb_last_ctrl_down and not ctrl_down:
                    if not self._fb_ctrl_combo_used and now - self._fb_last_ctrl_tap_at <= 0.35:
                        if self._can_trigger_selection_by_mode("double_ctrl") and not self._is_our_window_foreground():
                            self._trigger_selection_translate_debounced(source="poll:double_ctrl")
                    if not self._fb_ctrl_combo_used:
                        self._fb_last_ctrl_tap_at = now

                if (not screenshot_guard) and alt_down and not self._fb_last_alt_down:
                    self._fb_alt_combo_used = False

                if (not screenshot_guard) and alt_down and self._ctrl_combo_in_progress(windll.user32.GetAsyncKeyState):
                    self._fb_alt_combo_used = True

                if (not screenshot_guard) and self._fb_last_alt_down and not alt_down:
                    if not self._fb_alt_combo_used and now - self._fb_last_alt_tap_at <= 0.35:
                        if self._can_trigger_selection_by_mode("double_alt") and not self._is_our_window_foreground():
                            self._trigger_selection_translate_debounced(source="poll:double_alt")
                    if not self._fb_alt_combo_used:
                        self._fb_last_alt_tap_at = now

                if (not screenshot_guard) and shift_down and not self._fb_last_shift_down:
                    self._fb_shift_combo_used = False

                if (not screenshot_guard) and shift_down and self._ctrl_combo_in_progress(windll.user32.GetAsyncKeyState):
                    self._fb_shift_combo_used = True

                if (not screenshot_guard) and self._fb_last_shift_down and not shift_down:
                    if not self._fb_shift_combo_used and now - self._fb_last_shift_tap_at <= 0.35:
                        if self._can_trigger_selection_by_mode("double_shift") and not self._is_our_window_foreground():
                            self._trigger_selection_translate_debounced(source="poll:double_shift")
                    if not self._fb_shift_combo_used:
                        self._fb_last_shift_tap_at = now

                combo_s = False
                if (not screenshot_guard) and self.config.interaction.screenshot_enabled:
                    combo_s = self._shortcut_is_active(
                        self.config.interaction.screenshot_hotkey,
                        ctrl_down=ctrl_down,
                        alt_down=alt_down,
                        shift_down=shift_down,
                        key_state_getter=windll.user32.GetAsyncKeyState,
                    )
                if combo_s and not self._fb_last_combo_s:
                    if self.config.interaction.screenshot_hotkey:
                        self.start_screenshot_capture(show_bubble=True)
                self._fb_last_combo_s = combo_s

                if screenshot_active:
                    should_cancel_session = bool(escape_down or (rbtn_down and not self._fb_last_rbtn_down))
                    if should_cancel_session and (now - self._screenshot_cancel_last_at) >= 0.12:
                        self.cancel_screenshot_capture()

                if not screenshot_guard:
                    self._maybe_trigger_selection_icon_hover(current_cursor)
                    self._maybe_hide_selection_icon_by_cursor(current_cursor)
                self._maybe_hide_bubble_by_cursor(
                    current_cursor,
                    cursor_step,
                    cursor_dt,
                    cursor_dx,
                    cursor_dy,
                    lbtn_down=lbtn_down,
                )

                self._fb_last_lbtn_down = lbtn_down
                self._fb_last_rbtn_down = rbtn_down
                self._fb_last_ctrl_down = ctrl_down
                self._fb_last_alt_down = alt_down
                self._fb_last_shift_down = shift_down
                self._fb_last_escape_down = escape_down
            except Exception:
                now = time.time()
                if now - self._last_poll_input_error_log_at >= 5.0:
                    self._last_poll_input_error_log_at = now
                    self.logger.exception("Error in input fallback polling")

            self._input_poll_stop.wait(0.012)
