from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path
from typing import Any

import webview

from src.app_logging import APP_RUNTIME_DIR, LEGACY_USER_DIR, get_logger
from src.branding import APP_TITLE, ensure_icon_ico, icon_data_url, icon_path
from src.config import AppConfig, ConfigStore
from src.hotkeys import HotkeyManager, normalize_shortcut, parse_shortcut
from src.mouse_hooks import MouseHookManager
from src.ocr import ScreenshotOCRService
from src.selection_capture import ClipboardCaptureResult, SelectionCaptureService
from src.storage import HistoryStore
from src.translator import TranslationService
from src.native_icon_overlay import NativeIconOverlay
from src.tray_icon import TrayIconManager
from src.ui_webview.api import WindowApi
from src.ui_webview.backend import (
    capture_virtual_screen,
    copy_selection_once,
    get_clipboard_text,
    get_cursor_position,
    get_system_theme_preference,
    get_virtual_screen_bounds,
    image_to_data_url,
    set_clipboard_text,
)
from src.ui_webview.bridge import FrontendBridge
from src.ui_webview.state import BubbleState, ScreenshotSession, SelectionCandidate, UiState


class WordPackWebviewApp:
    MAIN_WINDOW_BG = "#c4c6ca"
    BUBBLE_WINDOW_BG = "#c4c6ca"
    DARK_WINDOW_BG = "#1b2028"
    MAIN_WIDTH = 468
    MAIN_HEIGHT = 680
    MAIN_MIN_HEIGHT = 360
    MAIN_COMPACT_HEIGHT = 430
    MAIN_STARTUP_Y_SHIFT = 24
    BUBBLE_WIDTH = 408
    BUBBLE_HEIGHT = 272
    ICON_WIDTH = 34
    ICON_HEIGHT = 34
    AI_PROBE_INTERVAL_SEC = 3600
    AI_STARTUP_CACHE_MAX_AGE_SEC = 86400

    def __init__(self) -> None:
        if getattr(sys, "frozen", False):
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
            exe_dir = Path(sys.executable).resolve().parent
            self.base_dir = bundle_root
            legacy_data_candidates = [LEGACY_USER_DIR / "data", exe_dir.parent / "data", exe_dir / "data"]
        else:
            self.base_dir = Path(__file__).resolve().parent.parent.parent
            legacy_data_candidates = [LEGACY_USER_DIR / "data", self.base_dir / "data"]
        self.logger = get_logger(__name__)
        self.lock = threading.RLock()
        self.data_dir = self._resolve_data_dir(legacy_data_candidates)

        self.config_store = ConfigStore(self.data_dir / "config.json")
        self.config: AppConfig = self.config_store.load()

        self.history = HistoryStore(self.data_dir / "history.db")
        self._prune_history_by_policy()
        self.service = TranslationService(self.get_config, data_dir=self.data_dir)
        self.ocr_service = ScreenshotOCRService()
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
        self.mouse_hooks = MouseHookManager(self._on_hook_event)

        self.main_window = None
        self.bubble_window = None
        self.icon_window = None
        self.overlay_window = None
        self.window_apis: dict[str, WindowApi] = {}
        self._native_icon_overlay = NativeIconOverlay(
            icon_path=icon_path("app-icon.png"),
            logger=self.logger,
            on_click=lambda: self.trigger_selection_translate(),
            window_size=self.ICON_WIDTH,
            icon_size=18,
        )
        tray_icon = self._app_icon_ico or icon_path("app-icon.ico")
        self.tray_icon = TrayIconManager(
            title=APP_TITLE,
            icon_path=str(tray_icon),
            on_action=self._on_tray_action,
            state_getter=lambda: {
                "selection_enabled": bool(self.config.interaction.selection_enabled),
                "screenshot_enabled": bool(self.config.interaction.screenshot_enabled),
            },
            logger=self.logger,
        )

        self.hidden = False
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
        self._selection_icon_timer: threading.Timer | None = None
        self._selection_icon_hide_timer: threading.Timer | None = None
        self._selection_icon_anchor_pos: tuple[int, int] | None = None
        self._selection_icon_retry = 0
        self._bubble_hide_timer: threading.Timer | None = None
        self._selection_icon_hover_armed = False
        self._screenshot_session: ScreenshotSession | None = None
        self._last_window_interaction_at = 0.0
        self._input_poll_stop = threading.Event()
        self._input_poll_thread: threading.Thread | None = None
        self._cursor_last_pos: tuple[int, int] | None = None
        self._cursor_last_move_at = 0.0
        self._fb_last_lbtn_down = False
        self._fb_last_rbtn_down = False
        self._fb_lbtn_down_pos: tuple[int, int] | None = None
        self._fb_last_lbtn_up_at = 0.0
        self._fb_lbtn_click_count = 0
        self._fb_last_ctrl_down = False
        self._fb_ctrl_combo_used = False
        self._fb_last_escape_down = False
        self._fb_last_ctrl_tap_at = 0.0
        self._fb_last_combo_s = False
        self._last_selection_trigger_at = 0.0
        self._last_poll_input_error_log_at = 0.0
        self._main_window_geometry_before_zoom: tuple[int, int, int, int] | None = None
        self._hide_main_after_zoom_close = False
        self._main_compact = True

    def _resolve_data_dir(self, legacy_candidates: list[Path]) -> Path:
        def first_legacy_dir() -> Path:
            for item in legacy_candidates:
                try:
                    candidate = Path(item)
                    candidate.mkdir(parents=True, exist_ok=True)
                    return candidate
                except Exception:
                    continue
            fallback = self.base_dir / "data"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

        override_raw = str(os.environ.get("WORDPACK_DATA_DIR") or "").strip()
        if override_raw:
            override = Path(override_raw).expanduser()
            override.mkdir(parents=True, exist_ok=True)
            return override

        shared = APP_RUNTIME_DIR
        try:
            shared.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.logger.warning("Shared data dir unavailable (%s), fallback to legacy: %s", exc, shared)
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
        )
        self.logger.info("Main webview window created")
        try:
            self.tray_icon.start()
        except Exception:
            self.logger.exception("Failed to start tray icon")
        webview.start(
            self._on_webview_started,
            http_server=True,
            debug=False,
            storage_path=str(self.webview_storage_dir),
        )

    def get_config(self) -> AppConfig:
        return self.config

    def _initial_status(self) -> str:
        if self.config.translation_mode == "ai":
            return "已切换到 AI 模式"
        return self.service.offline_diagnostics()

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
        if kind in {"icon", "overlay"}:
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

    def _apply_theme_backgrounds(self) -> None:
        main_bg = self._window_background_color("main")
        bubble_bg = self._window_background_color("bubble")
        self._apply_window_background(self.main_window, main_bg)
        self._apply_window_background(self.bubble_window, bubble_bg)

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
        return {
            "screenshot_translate": screenshot_hotkey,
        }

    def _shortcut_descriptions(self) -> list[str]:
        screenshot = ""
        if self.config.interaction.screenshot_enabled:
            screenshot = normalize_shortcut(self.config.interaction.screenshot_hotkey)
        items: list[str] = []
        if screenshot:
            items.append(f"{screenshot} 截图翻译")
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
            # icon/overlay windows must keep native background fully transparent,
            # otherwise a gray/black rectangular plate appears behind the floating icon.
            "background_color": self._window_background_color(kind),
            "transparent": transparent,
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

                hwnd = int(handle.ToInt32())
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

    def _on_webview_started(self) -> None:
        self.logger.info("Starting background input services")
        try:
            if self.main_window is not None:
                show = getattr(self.main_window, "show", None)
                current = self._current_main_geometry()
                if current is not None:
                    x, y, width, _height = current
                    self._apply_main_geometry(x, y, width, self.MAIN_COMPACT_HEIGHT)
                    self._main_compact = True
                elif callable(show):
                    show()
                self.hidden = False
        except Exception:
            self.logger.exception("Failed to reveal main window on startup")
        self._start_input_polling()
        if self._should_probe_ai_on_startup():
            self._probe_ai_availability_async()
        self._start_ai_probe_loop()
        try:
            self.hotkeys.start()
            self.mouse_hooks.start()
        except Exception:
            self.logger.exception("Failed to start global hook services")

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
            elif kind == "icon":
                self.icon_window = None
                self._native_icon_overlay.hide()
            elif kind == "overlay":
                self.overlay_window = None

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

        for kind in ("bubble", "icon", "overlay", "main"):
            window = getattr(self, f"{kind}_window", None)
            if window is None:
                continue
            try:
                window.destroy()
            except Exception:
                self.logger.exception("Failed to destroy %s window", kind)

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
        if kind == "overlay":
            session = self._screenshot_session
            return {
                "view": "overlay",
                "branding": self._branding_payload(),
                "themeMode": self._resolved_theme_mode(),
                "overlay": {
                    "backgroundDataUrl": session.background_data_url if session else "",
                    "bounds": session.bounds if session else {},
                },
            }
        return {"view": kind}

    def mark_window_ready(self, kind: str) -> None:
        self.bridge.mark_ready(kind)

    def note_window_interaction(self, kind: str) -> None:
        del kind
        self._last_window_interaction_at = time.time()
        if self._native_icon_overlay.is_visible():
            self.close_window("icon")

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
        return asdict(self.config)

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
        if mode not in {"all", "argos", "ai"}:
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

    def get_settings_payload(self, probe_runtime: bool = False) -> dict[str, Any]:
        offline_status = self.service.offline_status(probe=probe_runtime)
        offline_ready = bool(offline_status.get("runtime_ready", False))
        return {
            "config": self._serialize_config(),
            "offlineModels": list(offline_status.get("models", [])) if offline_ready else [],
            "offlineRuntimeReady": offline_ready,
            "offlineRuntimeHint": str(offline_status.get("runtime_hint", "")),
            "offlineDiagnostics": str(offline_status.get("diagnostics", "")),
            "effectiveTheme": self._resolved_theme_mode(),
            "historyFilters": self._history_filters_payload(),
        }

    def set_translation_mode(self, mode: str) -> dict[str, Any]:
        with self.lock:
            value = str(mode or "").strip().lower()
            if value not in {"argos", "ai"}:
                value = "argos"
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

        if value == "argos":
            self.set_status(self.service.offline_diagnostics())
        else:
            self.set_status("已切换到 AI 模式")

        payload = self._config_event_payload()
        self.bridge.send("main", "config-updated", payload)
        self.bridge.send("bubble", "theme-updated", {"themeMode": self._resolved_theme_mode(), "mode": value})
        self._emit_bubble_updated()
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
        self.bridge.send("overlay", "theme-updated", {"themeMode": resolved})
        return payload

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
        return {"ok": True, "enabled": enabled}

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
        return {"ok": True, "enabled": enabled}

    def _on_tray_action(self, action: str) -> None:
        key = str(action or "").strip().lower()
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
        if key == "toggle_screenshot":
            self._toggle_screenshot_enabled_from_tray()
            return

    def cycle_direction(self) -> dict[str, Any]:
        options = ["auto", "en->zh", "zh->en"]
        with self.lock:
            current = str(self.config.offline.preferred_direction or "auto").strip() or "auto"
            if current not in options:
                current = "auto"
            next_value = options[(options.index(current) + 1) % len(options)]
            self.config.offline.preferred_direction = next_value
            self.ui_state.direction = self._direction_label(next_value)
            self.config_store.save(self.config)

        self.set_status(f"离线方向已切换: {next_value}")
        payload = self._config_event_payload()
        self.bridge.send("main", "config-updated", payload)
        return payload

    def _direction_label(self, value: str | None = None) -> str:
        current = str(value if value is not None else self.config.offline.preferred_direction or "auto").strip() or "auto"
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

    def _cancel_active_translation(self, message: str = "Translation cancelled") -> bool:
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
        cancelled = self._cancel_active_translation("Translation cancelled")
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
        mode = str(self.config.translation_mode or "argos")
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
                error_text = str(exc) or "候选生成失败"
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
            self.set_status("Please enter text")
            return {"ok": False, "message": "Please enter text"}

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
            self._show_bubble(
                source_text=source_text,
                result_text="",
                pending=True,
                action=action,
                mode=mode,
                preserve_position=preserve_existing_bubble,
                preserve_height=preserve_existing_bubble,
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
            direction=str(self.config.offline.preferred_direction or "auto"),
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

        self.set_status(message)
        if update_main:
            self.bridge.send(
                "main",
                "translation-error",
                {
                    "reqId": req_id,
                    "sourceText": source_text,
                    "resultText": partial,
                    "message": message,
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
                    "message": message,
                    "mode": mode,
                    "action": action,
                },
            )

        if show_bubble and current_show_bubble:
            bubble_text = partial.strip() or message
            if partial.strip():
                bubble_text = f"{partial}\n\n[生成中断] {message}"
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
        message = "Copied" if ok else "Copy failed"
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
        self.set_status("History cleared")
        response = {"history": history_rows, "filters": self._history_filters_payload()}
        self.bridge.send("main", "history-updated", response)
        return response

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        openai = payload.get("openai", {}) if isinstance(payload, dict) else {}
        interaction = payload.get("interaction", {}) if isinstance(payload, dict) else {}
        offline = payload.get("offline", {}) if isinstance(payload, dict) else {}
        theme_mode = str(payload.get("theme_mode", self.config.theme_mode) or self.config.theme_mode) if isinstance(payload, dict) else self.config.theme_mode

        try:
            timeout_sec = max(5, int(openai.get("timeout_sec", self.config.openai.timeout_sec)))
        except Exception:
            timeout_sec = self.config.openai.timeout_sec

        self.config.openai.base_url = str(openai.get("base_url", self.config.openai.base_url) or "").strip()
        self.config.openai.api_key = str(openai.get("api_key", self.config.openai.api_key) or "").strip()
        self.config.openai.model = str(openai.get("model", self.config.openai.model) or "").strip()
        self.config.openai.timeout_sec = timeout_sec

        preferred_direction = str(offline.get("preferred_direction", self.config.offline.preferred_direction) or "auto").strip()
        self.config.offline.preferred_direction = preferred_direction or "auto"

        trigger_mode = str(interaction.get("selection_trigger_mode", self.config.interaction.selection_trigger_mode) or "icon").strip()
        if trigger_mode not in {"icon", "double_ctrl"}:
            trigger_mode = "icon"

        icon_trigger = str(interaction.get("selection_icon_trigger", self.config.interaction.selection_icon_trigger) or "click").strip()
        if icon_trigger not in {"click", "hover"}:
            icon_trigger = "click"

        try:
            icon_delay_ms = int(interaction.get("selection_icon_delay_ms", self.config.interaction.selection_icon_delay_ms))
        except Exception:
            icon_delay_ms = self.config.interaction.selection_icon_delay_ms

        screenshot_enabled = bool(interaction.get("screenshot_enabled", self.config.interaction.screenshot_enabled))
        screenshot_hotkey = normalize_shortcut(str(interaction.get("screenshot_hotkey", self.config.interaction.screenshot_hotkey) or ""))
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
        self.config.interaction.selection_icon_delay_ms = max(300, min(5000, icon_delay_ms))
        self.config.history.retention_days = retention_days if retention_days in {7, 30, 90} else 30

        if theme_mode not in {"system", "light", "dark"}:
            theme_mode = "system"
        self.config.theme_mode = theme_mode
        self.ui_state.theme_mode = self._resolved_theme_mode(theme_mode)
        self.ui_state.direction = self._direction_label()
        self.config_store.save(self.config)
        self._apply_theme_backgrounds()
        self._prune_history_by_policy()
        try:
            self.hotkeys.stop()
            self.hotkeys.start()
        except Exception:
            self.logger.exception("Failed to restart hotkey manager after settings update")

        if self.config.translation_mode == "argos":
            self.set_status(self.service.offline_diagnostics())
        else:
            self.set_status("AI settings saved")

        response = self._config_event_payload()
        self.bridge.send("main", "config-updated", response)
        resolved = self._resolved_theme_mode()
        self.bridge.send("bubble", "theme-updated", {"themeMode": resolved, "bubble": self._bubble_state.to_payload()})
        self.bridge.send("icon", "theme-updated", {"themeMode": resolved, "mode": self.config.translation_mode})
        self._probe_ai_availability_async()
        return response

    def test_ai_connection(self) -> None:
        self.set_status("正在测试 AI 连接...")

        def worker() -> None:
            ok, message = self.service.test_ai_connection()
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

    def import_offline_model(self, window) -> dict[str, Any]:
        if window is None:
            return {"ok": False, "message": "主窗口不可用"}
        if not self.service.offline_runtime_ready(probe=True):
            message = self.service.offline_runtime_hint(probe=True)
            self.set_status(message)
            return {"ok": False, "message": message}

        try:
            selected = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Argos model (*.argosmodel)",),
            )
        except Exception as exc:
            message = f"无法打开文件选择器: {exc}"
            self.set_status(message)
            return {"ok": False, "message": message}

        if not selected:
            return {"ok": False, "message": "Import cancelled"}

        file_path = selected[0] if isinstance(selected, (list, tuple)) else selected
        self.set_status("正在导入离线模型...")

        def worker() -> None:
            try:
                message = self.service.import_offline_model(str(file_path))
                self.set_status(message)
                payload = self._config_event_payload()
                self.bridge.send("main", "offline-models-updated", payload)
            except Exception as exc:
                message = str(exc)
                self.set_status(f"离线模型导入失败: {message}")
                self.bridge.send("main", "offline-model-import-error", {"message": message})

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "message": "started"}

    def start_screenshot_capture(self, show_bubble: bool) -> dict[str, Any]:
        if self.overlay_window is not None:
            return {"ok": False, "message": "Screenshot capture is already running"}

        main_hidden_before = self.hidden
        if self.main_window is not None and not self.hidden:
            try:
                self.main_window.hide()
                self.hidden = True
            except Exception:
                self.logger.exception("Failed to temporarily hide main window before screenshot")

        time.sleep(0.08)

        try:
            image = capture_virtual_screen()
        except Exception as exc:
            if self.main_window is not None and not main_hidden_before:
                try:
                    self.main_window.show()
                    self.hidden = False
                except Exception:
                    self.logger.exception("Failed to restore main window after screenshot startup error")
            message = str(exc)
            self.set_status(message)
            return {"ok": False, "message": message}

        bounds = get_virtual_screen_bounds()

        self._screenshot_session = ScreenshotSession(
            background_data_url=image_to_data_url(image),
            bounds=bounds.to_payload(),
            image=image,
            show_bubble=bool(show_bubble),
            main_was_hidden=main_hidden_before,
        )
        self.overlay_window = self._create_window(
            kind="overlay",
            title="WordPack Overlay",
            width=max(300, bounds.width),
            height=max(200, bounds.height),
            x=bounds.left,
            y=bounds.top,
            frameless=True,
            shadow=False,
            resizable=False,
            on_top=True,
            focus=True,
        )
        self.set_status("拖拽选择截图区域，右键或 Esc 取消")
        return {"ok": True}

    def cancel_screenshot_capture(self) -> dict[str, Any]:
        if self.overlay_window is not None:
            try:
                self.overlay_window.destroy()
            except Exception:
                self.logger.exception("Failed to close overlay window")
        self.overlay_window = None
        self._restore_main_after_screenshot()
        self._screenshot_session = None
        self.set_status("Screenshot cancelled")
        return {"ok": True}

    def finish_screenshot_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._screenshot_session
        if session is None:
            return {"ok": False, "message": "Screenshot session not found"}

        if self.overlay_window is not None:
            try:
                self.overlay_window.destroy()
            except Exception:
                self.logger.exception("Failed to close overlay window after selection")
        self.overlay_window = None
        self._restore_main_after_screenshot()

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
        if width < 12 or height < 12:
            self._screenshot_session = None
            self.set_status("截图区域过小，已取消")
            return {"ok": False, "message": "截图区域过小"}

        crop_box = (
            left - bounds["left"],
            top - bounds["top"],
            right - bounds["left"],
            bottom - bounds["top"],
        )
        image = session.image.crop(crop_box)
        self._screenshot_session = None
        self.set_status("截图 OCR 中...")

        if session.show_bubble:
            self._show_bubble(
                source_text="截图 OCR",
                result_text="正在识别文字...",
                pending=True,
                action="截图翻译",
                mode=self.config.translation_mode,
                anchor=(left, top),
            )

        def worker() -> None:
            try:
                text = self.ocr_service.extract_text(image).strip()
                if not text:
                    raise RuntimeError("截图中未识别到可翻译文本")
                if not session.show_bubble:
                    self.bridge.send("main", "screenshot-ocr-ready", {"sourceText": text})
                self._start_translate(text=text, action="截图翻译", show_bubble=session.show_bubble)
            except Exception as exc:
                message = str(exc)
                self.set_status(message)
                self.bridge.send("main", "screenshot-ocr-error", {"message": message})
                if session.show_bubble:
                    self._update_bubble(
                        "截图 OCR",
                        message,
                        pending=False,
                        action="截图翻译",
                        mode=self.config.translation_mode,
                        refresh_auto_hide=True,
                    )

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def _restore_main_after_screenshot(self) -> None:
        if self.main_window is None:
            return
        if self._screenshot_session and self._screenshot_session.main_was_hidden:
            self.hidden = True
            return
        try:
            self.main_window.show()
            self.hidden = False
        except Exception:
            self.logger.exception("Failed to restore main window after screenshot flow")

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
            self.set_status("Bubble pinned")
        else:
            self.set_status("Bubble unpinned")
            if not self._bubble_state.pending:
                self._schedule_bubble_auto_hide()

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
        x, y = self._centered_position(width, height)
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
        self._cancel_bubble_hide_timer()
        self._bubble_hide_timer = threading.Timer(delay_sec, self._auto_close_bubble_if_allowed)
        self._bubble_hide_timer.daemon = True
        self._bubble_hide_timer.start()

    def _cancel_bubble_hide_timer(self) -> None:
        if self._bubble_hide_timer is not None:
            self._bubble_hide_timer.cancel()
            self._bubble_hide_timer = None

    def trigger_selection_translate(self) -> dict[str, Any]:
        self._selection_icon_hover_armed = False
        self.close_window("icon")
        return self._translate_pending_selection()

    def _translate_pending_selection(self) -> dict[str, Any]:
        candidate = self._selection_candidate
        if not candidate.is_fresh():
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

        text = candidate.text.strip() or self._capture_selected_text(candidate.payload).strip()
        if not text:
            message = "No translatable text detected"
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
        return self._start_translate(text=text, action="划词翻译", show_bubble=True)

    def _capture_selected_text(self, payload: dict[str, int] | None) -> str:
        result = self.selection_capture.capture(
            self._capture_selection_by_ctrl_c,
            payload=payload,
            wait_sec=0.1,
            allow_unchanged=False,
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

        backup_text = get_clipboard_text(raw=True)
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

        if event == "double_ctrl_selection":
            if (
                self.config.interaction.selection_enabled
                and self.config.interaction.selection_trigger_mode == "double_ctrl"
                and not self._is_our_window_foreground()
            ):
                self._translate_pending_selection()
            return

        if event == "selection_mouse_up":
            self._emit_selection_candidate(payload)
            return

        if event == "screenshot_translate":
            if self.config.interaction.screenshot_enabled and normalize_shortcut(self.config.interaction.screenshot_hotkey):
                self.start_screenshot_capture(show_bubble=True)
            return

    def _handle_selection_candidate(self, payload) -> None:
        if not self.config.interaction.selection_enabled:
            return
        if time.time() - self._last_window_interaction_at <= 0.75:
            return

        data = payload if isinstance(payload, dict) else {}
        candidate = SelectionCandidate(
            captured_at=time.time(),
            payload={
                "x": int(data.get("x", 0)),
                "y": int(data.get("y", 0)),
                "down_x": int(data.get("down_x", data.get("x", 0))),
                "down_y": int(data.get("down_y", data.get("y", 0))),
            },
            text="",
        )
        with self.lock:
            self._selection_candidate = candidate
        self._selection_icon_retry = 0

        if self.config.interaction.selection_trigger_mode == "icon":
            self.set_status("已捕获选区，静置后显示图标")
            self._schedule_selection_icon()
        else:
            self.set_status("已捕获选区，双击 Ctrl 触发翻译")
            self.close_window("icon")

    def _emit_selection_candidate(self, payload) -> None:
        now = time.time()
        if now - self._last_selection_trigger_at < 0.08:
            return
        self._last_selection_trigger_at = now
        self._handle_selection_candidate(payload)

    def _schedule_selection_icon(self, delay_sec: float | None = None) -> None:
        self._cancel_selection_icon_timer()
        delay = delay_sec if delay_sec is not None else max(0.3, min(5.0, self.config.interaction.selection_icon_delay_ms / 1000.0))
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
        if not candidate.is_fresh():
            return
        if time.time() - self._last_window_interaction_at <= 0.75:
            return
        delay_sec = max(0.3, min(5.0, self.config.interaction.selection_icon_delay_ms / 1000.0))
        if self._cursor_last_move_at <= 0 or (time.time() - self._cursor_last_move_at) < delay_sec:
            self._schedule_selection_icon(0.12)
            return

        cursor_pos = get_cursor_position()
        if not self._is_cursor_on_selection_anchor(cursor_pos, candidate.payload):
            self._schedule_selection_icon(0.12)
            return

        probe = candidate.text.strip()
        if not probe:
            try:
                probe = self._capture_selected_text(candidate.payload).strip()
            except Exception:
                self.logger.exception("Selection probe failed before showing icon")
                probe = ""
        if not probe:
            if self._selection_icon_retry < 8:
                self._selection_icon_retry += 1
                self._schedule_selection_icon(0.12)
            return

        self._selection_icon_retry = 0
        self._selection_candidate.text = probe
        self._show_selection_icon(candidate.payload or {})

    def _show_selection_icon(self, payload: dict[str, int]) -> None:
        bounds = get_virtual_screen_bounds()
        cursor_x, cursor_y = get_cursor_position()
        anchor_x = cursor_x if cursor_x or cursor_y else int(payload.get("x", 0))
        anchor_y = cursor_y if cursor_x or cursor_y else int(payload.get("y", 0))
        x = anchor_x + 12
        y = anchor_y - (self.ICON_HEIGHT // 2)
        x = max(bounds.left + 4, min(x, bounds.right - self.ICON_WIDTH - 4))
        y = max(bounds.top + 4, min(y, bounds.bottom - self.ICON_HEIGHT - 4))
        self.close_window("icon")
        self._native_icon_overlay.show(int(x), int(y))
        self._selection_icon_anchor_pos = (int(x + (self.ICON_WIDTH / 2)), int(y + (self.ICON_HEIGHT / 2)))
        self._selection_icon_hover_armed = True
        self._schedule_selection_icon_auto_hide()

    def close_window(self, kind: str) -> dict[str, Any]:
        if kind == "main":
            if self.main_window is not None:
                try:
                    self.main_window.hide()
                    self.hidden = True
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
                window = self.bubble_window
                self.bubble_window = None
        elif kind == "icon":
            self._cancel_selection_icon_hide_timer()
            self._native_icon_overlay.hide()
            window = self.icon_window
            self.icon_window = None
            self._selection_icon_anchor_pos = None
            self._selection_icon_hover_armed = False
        elif kind == "overlay":
            window = self.overlay_window
            self.overlay_window = None
        else:
            window = None

        if window is not None:
            try:
                window.destroy()
            except Exception:
                self.logger.exception("Failed to close %s window", kind)

        return {"ok": True}

    def _start_input_polling(self) -> None:
        if self._input_poll_thread is not None and self._input_poll_thread.is_alive():
            return
        self._input_poll_stop.clear()
        self._input_poll_thread = threading.Thread(target=self._poll_input_loop, name="wordpack-input-poll", daemon=True)
        self._input_poll_thread.start()

    def _selection_icon_cancel_distance(self) -> int:
        return 90

    @staticmethod
    def _selection_icon_trigger_distance() -> int:
        return 14

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
        hwnd = int(handle.ToInt32())
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        return fallback

    def _window_hwnd(self, window) -> int | None:
        native = getattr(window, "native", None) if window is not None else None
        handle = getattr(native, "Handle", None)
        if handle is None:
            return None
        try:
            return int(handle.ToInt32())
        except Exception:
            return None

    def _is_our_window_foreground(self) -> bool:
        try:
            foreground_hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            return False
        if foreground_hwnd <= 0:
            return False

        for window in (self.main_window, self.bubble_window, self.icon_window, self.overlay_window):
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

    def _cursor_distance_to_bubble(self, cursor_pos: tuple[int, int]) -> int:
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
        cx, cy = int(cursor_pos[0]), int(cursor_pos[1])
        dx = 0 if left <= cx <= right else (left - cx if cx < left else cx - right)
        dy = 0 if top <= cy <= bottom else (top - cy if cy < top else cy - bottom)
        return dx + dy

    def _maybe_hide_selection_icon_by_cursor(self, cursor_pos: tuple[int, int]) -> None:
        if not self._native_icon_overlay.is_visible() or self._selection_icon_anchor_pos is None:
            return

        dx = abs(int(cursor_pos[0]) - int(self._selection_icon_anchor_pos[0]))
        dy = abs(int(cursor_pos[1]) - int(self._selection_icon_anchor_pos[1]))
        if dx + dy >= self._selection_icon_cancel_distance():
            self.close_window("icon")

    def _maybe_trigger_selection_icon_hover(self, cursor_pos: tuple[int, int]) -> None:
        if self.config.interaction.selection_icon_trigger != "hover":
            return
        if not self._selection_icon_hover_armed:
            return
        if not self._native_icon_overlay.is_visible() or self._selection_icon_anchor_pos is None:
            return

        dx = int(cursor_pos[0]) - int(self._selection_icon_anchor_pos[0])
        dy = int(cursor_pos[1]) - int(self._selection_icon_anchor_pos[1])
        trigger_radius = max(10, (self.ICON_WIDTH // 2) + 2)
        if (dx * dx + dy * dy) > (trigger_radius * trigger_radius):
            return

        self._selection_icon_hover_armed = False
        threading.Thread(target=self.trigger_selection_translate, daemon=True).start()

    def _maybe_hide_bubble_by_cursor(self, cursor_pos: tuple[int, int], cursor_step: int, cursor_dt: float) -> None:
        with self.lock:
            if self.bubble_window is None or self._bubble_state.pinned:
                return

        if self._is_cursor_inside_bubble():
            return
        if cursor_step <= 0 or cursor_dt <= 0:
            return

        distance = self._cursor_distance_to_bubble(cursor_pos)
        speed = cursor_step / max(cursor_dt, 0.001)
        quick_slide = cursor_step >= 36 and cursor_dt <= 0.06 and distance >= 90
        quickly_away = speed >= 1100 and distance >= 70
        if quick_slide or quickly_away:
            self.close_window("bubble")

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
        with self.lock:
            if self.bubble_window is None or self._bubble_state.pinned:
                self._bubble_hide_timer = None
                return

        if self._is_cursor_inside_bubble():
            self._schedule_bubble_auto_hide(0.35)
            return

        self._bubble_hide_timer = None
        self.close_window("bubble")

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
                if self._cursor_last_pos is None:
                    self._cursor_last_pos = current_cursor
                    self._cursor_last_move_at = now
                elif current_cursor != self._cursor_last_pos:
                    cursor_step = abs(current_cursor[0] - self._cursor_last_pos[0]) + abs(current_cursor[1] - self._cursor_last_pos[1])
                    cursor_dt = now - self._cursor_last_move_at if self._cursor_last_move_at > 0 else 0.0
                    self._cursor_last_pos = current_cursor
                    self._cursor_last_move_at = now

                lbtn_down = bool(windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
                rbtn_down = bool(windll.user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
                ctrl_down = bool(windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                escape_down = bool(windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
                alt_down = bool(windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                shift_down = bool(windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)

                if not self._fb_last_lbtn_down and lbtn_down:
                    self._fb_lbtn_down_pos = (cursor_x, cursor_y)

                if self._fb_last_lbtn_down and not lbtn_down:
                    if self.config.interaction.selection_enabled:
                        down = self._fb_lbtn_down_pos
                        dx = abs(cursor_x - down[0]) if down else 0
                        dy = abs(cursor_y - down[1]) if down else 0
                        moved = dx + dy

                        if now - self._fb_last_lbtn_up_at <= 0.35:
                            self._fb_lbtn_click_count += 1
                        else:
                            self._fb_lbtn_click_count = 1
                        self._fb_last_lbtn_up_at = now

                        if moved >= 3 or self._fb_lbtn_click_count >= 2:
                            down_x = int(down[0]) if down else int(cursor_x)
                            down_y = int(down[1]) if down else int(cursor_y)
                            self._emit_selection_candidate(
                                {
                                    "x": int(cursor_x),
                                    "y": int(cursor_y),
                                    "down_x": down_x,
                                    "down_y": down_y,
                                }
                            )
                            self._fb_lbtn_click_count = 0
                    self._fb_lbtn_down_pos = None

                if ctrl_down and not self._fb_last_ctrl_down:
                    self._fb_ctrl_combo_used = False

                if ctrl_down and self._ctrl_combo_in_progress(windll.user32.GetAsyncKeyState):
                    self._fb_ctrl_combo_used = True

                if self._fb_last_ctrl_down and not ctrl_down:
                    if not self._fb_ctrl_combo_used and now - self._fb_last_ctrl_tap_at <= 0.35:
                        if (
                            self.config.interaction.selection_enabled
                            and self.config.interaction.selection_trigger_mode == "double_ctrl"
                            and not self._is_our_window_foreground()
                        ):
                            self._translate_pending_selection()
                    if not self._fb_ctrl_combo_used:
                        self._fb_last_ctrl_tap_at = now

                combo_s = False
                if self.config.interaction.screenshot_enabled:
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

                if self.overlay_window is not None:
                    if (rbtn_down and not self._fb_last_rbtn_down) or (escape_down and not self._fb_last_escape_down):
                        self.cancel_screenshot_capture()

                self._maybe_trigger_selection_icon_hover(current_cursor)
                self._maybe_hide_selection_icon_by_cursor(current_cursor)
                self._maybe_hide_bubble_by_cursor(current_cursor, cursor_step, cursor_dt)

                self._fb_last_lbtn_down = lbtn_down
                self._fb_last_rbtn_down = rbtn_down
                self._fb_last_ctrl_down = ctrl_down
                self._fb_last_escape_down = escape_down
            except Exception:
                now = time.time()
                if now - self._last_poll_input_error_log_at >= 5.0:
                    self._last_poll_input_error_log_at = now
                    self.logger.exception("Error in input fallback polling")

            self._input_poll_stop.wait(0.012)
