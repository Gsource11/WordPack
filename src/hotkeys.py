from __future__ import annotations

import threading
import time
import logging
from ctypes import POINTER, WINFUNCTYPE, Structure, byref, cast, c_int, c_void_p, windll
from ctypes import wintypes
from typing import Callable


user32 = windll.user32
kernel32 = windll.kernel32
logger = logging.getLogger("wordpack.hotkeys")


WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

HOTKEY_ID_SCREENSHOT = 1003
HOTKEY_ID_RESTORE_BUBBLE = 1004
HOTKEY_ID_TOGGLE_MAIN = 1005

HOTKEY_EVENT_MAP = {
    HOTKEY_ID_SCREENSHOT: "screenshot_translate",
    HOTKEY_ID_RESTORE_BUBBLE: "restore_bubble",
    HOTKEY_ID_TOGGLE_MAIN: "toggle_main_window",
}

KEY_NAME_TO_VK = {chr(code): code for code in range(ord("A"), ord("Z") + 1)}
KEY_NAME_TO_VK.update({str(num): ord(str(num)) for num in range(10)})
KEY_NAME_TO_VK.update({f"F{index}": 0x6F + index for index in range(1, 13)})


def normalize_shortcut(shortcut: str) -> str:
    raw = str(shortcut or "").strip()
    if not raw:
        return ""

    tokens = [token.strip().upper() for token in raw.replace(" ", "").split("+") if token.strip()]
    if not tokens:
        return ""

    modifiers: list[str] = []
    key = ""
    for token in tokens:
        alias = {"CONTROL": "CTRL", "CMD": "CTRL", "OPTION": "ALT"}.get(token, token)
        if alias in {"CTRL", "ALT", "SHIFT"}:
            if alias not in modifiers:
                modifiers.append(alias)
            continue
        if key:
            return ""
        if alias not in KEY_NAME_TO_VK:
            return ""
        key = alias

    if not key or not modifiers:
        return ""

    ordered = [name for name in ("CTRL", "ALT", "SHIFT") if name in modifiers]
    return "+".join([*ordered, key])


def parse_shortcut(shortcut: str) -> tuple[int, int, str] | None:
    normalized = normalize_shortcut(shortcut)
    if not normalized:
        return None

    tokens = normalized.split("+")
    key = tokens[-1]
    modifiers = 0
    for token in tokens[:-1]:
        if token == "CTRL":
            modifiers |= MOD_CONTROL
        elif token == "ALT":
            modifiers |= MOD_ALT
        elif token == "SHIFT":
            modifiers |= MOD_SHIFT

    vk = KEY_NAME_TO_VK.get(key)
    if vk is None:
        return None
    return modifiers, vk, normalized


class POINT(Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class KBDLLHOOKSTRUCT(Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", c_void_p),
    ]


LowLevelKeyboardProc = WINFUNCTYPE(wintypes.LPARAM, wintypes.INT, wintypes.WPARAM, wintypes.LPARAM)


def _configure_winapi_signatures() -> None:
    try:
        user32.SetWindowsHookExW.argtypes = [c_int, LowLevelKeyboardProc, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = wintypes.LPARAM
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        # Do not bind GetMessage/TranslateMessage/DispatchMessage argtypes to
        # module-local MSG pointers. Other modules define compatible MSG
        # structs; strict argtypes causes cross-module ctypes type mismatch on
        # Python 3.12+ (expected LP_MSG instance).
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.restype = wintypes.LPARAM
    except Exception:
        logger.exception("Failed to configure WinAPI signatures for hotkeys")


_configure_winapi_signatures()


class HotkeyManager:
    def __init__(self, callback, shortcut_getter: Callable[[], dict[str, str]] | None = None) -> None:
        self.callback = callback
        self.shortcut_getter = shortcut_getter or (lambda: {})
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._stop_event = threading.Event()
        self._keyboard_hook = None
        self._keyboard_proc = None
        self._last_ctrl_at = 0.0
        self._ctrl_pressed: set[int] = set()
        self._ctrl_combo_used = False
        self._last_alt_at = 0.0
        self._alt_pressed: set[int] = set()
        self._alt_combo_used = False
        self._last_shift_at = 0.0
        self._shift_pressed: set[int] = set()
        self._shift_combo_used = False
        self._registered_hotkeys: list[int] = []
        self._registered_hotkey_events: set[str] = set()
        self._configured_hotkeys: dict[str, tuple[int, int, str]] = {}
        self._hook_active_events: set[str] = set()
        self._last_hotkey_event_at: dict[str, float] = {}
        self._double_tap_window_sec_cache = 0.35
        self._double_tap_window_cached_at = 0.0

    def is_running(self) -> bool:
        with self._state_lock:
            thread = self._thread
            return bool(thread and thread.is_alive() and not self._stop_event.is_set())

    def _double_tap_window_sec(self) -> float:
        now = time.time()
        if self._double_tap_window_cached_at > 0 and (now - self._double_tap_window_cached_at) <= 5.0:
            return self._double_tap_window_sec_cache
        try:
            value_ms = int(user32.GetDoubleClickTime())
        except Exception:
            value_ms = 350
        value_sec = max(0.20, min(0.80, float(value_ms) / 1000.0))
        self._double_tap_window_sec_cache = value_sec
        self._double_tap_window_cached_at = now
        return value_sec

    def _safe_callback(self, event: str, payload) -> None:
        try:
            self.callback(event, payload)
        except Exception:
            logger.exception("Hotkey callback failed event=%s", event)

    def _dispatch_callback(self, event: str, payload) -> None:
        threading.Thread(
            target=self._safe_callback,
            args=(event, payload),
            name=f"wordpack-hotkey-{event}",
            daemon=True,
        ).start()

    def _emit_hotkey_event(self, event: str, *, source: str) -> None:
        now = time.time()
        last_at = float(self._last_hotkey_event_at.get(event, 0.0) or 0.0)
        if (now - last_at) < 0.25:
            logger.info(
                "Hotkey duplicate suppressed event=%s source=%s delta=%.3f",
                event,
                source,
                now - last_at,
            )
            return
        self._last_hotkey_event_at[event] = now
        logger.info("Hotkey activated event=%s source=%s", event, source)
        self._dispatch_callback(event, None)

    def _current_modifier_mask(self) -> int:
        modifiers = 0
        if self._ctrl_pressed:
            modifiers |= MOD_CONTROL
        if self._alt_pressed:
            modifiers |= MOD_ALT
        if self._shift_pressed:
            modifiers |= MOD_SHIFT
        return modifiers

    def _handle_hook_hotkeys(self, vk_code: int, *, is_key_down: bool) -> None:
        configured_hotkeys = self._configured_hotkeys
        if not configured_hotkeys:
            return
        if not is_key_down:
            for event_name, (_modifiers, key_vk, _label) in configured_hotkeys.items():
                if int(key_vk) == int(vk_code):
                    self._hook_active_events.discard(event_name)
            return

        if vk_code in (VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LSHIFT, VK_RSHIFT):
            return

        active_modifiers = self._current_modifier_mask()
        for event_name, (modifiers, key_vk, _label) in configured_hotkeys.items():
            if event_name in self._registered_hotkey_events:
                self._hook_active_events.discard(event_name)
                continue
            if int(key_vk) != int(vk_code):
                continue
            if int(modifiers) != int(active_modifiers):
                self._hook_active_events.discard(event_name)
                continue
            if event_name in self._hook_active_events:
                continue
            self._hook_active_events.add(event_name)
            self._emit_hotkey_event(event_name, source="low_level_hook")

    def start(self) -> None:
        thread: threading.Thread | None
        with self._state_lock:
            thread = self._thread
        if thread and thread.is_alive():
            if self._stop_event.is_set():
                thread.join(timeout=1.5)
            else:
                return

        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._state_lock:
            self._stop_event.set()
            thread_id = int(self._thread_id or 0)
            thread = self._thread
        if thread_id:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        if thread:
            thread.join(timeout=1.2)
        with self._state_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
                self._thread_id = 0

    def _loop(self) -> None:
        with self._state_lock:
            self._thread_id = kernel32.GetCurrentThreadId()
        try:
            shortcut_map = {
                "screenshot_translate": "",
                "restore_bubble": "",
                "toggle_main_window": "",
            }
            shortcut_map.update(self.shortcut_getter() or {})
            self._registered_hotkeys.clear()
            self._registered_hotkey_events.clear()
            self._configured_hotkeys = {}
            self._hook_active_events.clear()
            self._last_hotkey_event_at.clear()
            self._ctrl_pressed.clear()
            self._alt_pressed.clear()
            self._shift_pressed.clear()
            self._ctrl_combo_used = False
            self._alt_combo_used = False
            self._shift_combo_used = False
            self._last_ctrl_at = 0.0
            self._last_alt_at = 0.0
            self._last_shift_at = 0.0

            for hotkey_id, event_name in HOTKEY_EVENT_MAP.items():
                parsed = parse_shortcut(shortcut_map.get(event_name, ""))
                if not parsed:
                    continue
                modifiers, vk, label = parsed
                self._configured_hotkeys[event_name] = (modifiers, vk, label)
                if user32.RegisterHotKey(None, hotkey_id, int(modifiers | MOD_NOREPEAT), vk):
                    self._registered_hotkeys.append(hotkey_id)
                    self._registered_hotkey_events.add(event_name)
                    logger.info("Registered hotkey %s (norepeat=on)", label)
                elif user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                    self._registered_hotkeys.append(hotkey_id)
                    self._registered_hotkey_events.add(event_name)
                    logger.info("Registered hotkey %s (norepeat=off)", label)
                else:
                    self._safe_callback("status", f"全局热键注册失败：{label} 已被占用")

            self._keyboard_proc = LowLevelKeyboardProc(self._keyboard_callback)
            self._keyboard_hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL,
                self._keyboard_proc,
                kernel32.GetModuleHandleW(None),
                0,
            )
            if not self._keyboard_hook:
                # Retry with null module handle for environments where binding to
                # current module fails for low-level keyboard hook.
                self._keyboard_hook = user32.SetWindowsHookExW(
                    WH_KEYBOARD_LL,
                    self._keyboard_proc,
                    None,
                    0,
                )
            if not self._keyboard_hook:
                try:
                    err = int(kernel32.GetLastError())
                except Exception:
                    err = -1
                logger.warning("Keyboard hook install failed, last_error=%s", err)

            msg = MSG()
            while not self._stop_event.is_set():
                code = user32.GetMessageW(byref(msg), None, 0, 0)
                if code <= 0:
                    break

                if msg.message == WM_HOTKEY:
                    event_name = HOTKEY_EVENT_MAP.get(int(msg.wParam))
                    if event_name:
                        self._emit_hotkey_event(event_name, source="wm_hotkey")

                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))
        finally:
            if self._keyboard_hook:
                user32.UnhookWindowsHookEx(self._keyboard_hook)
                self._keyboard_hook = None
            for hotkey_id in self._registered_hotkeys:
                user32.UnregisterHotKey(None, hotkey_id)
            self._registered_hotkeys.clear()
            self._registered_hotkey_events.clear()
            self._configured_hotkeys.clear()
            self._hook_active_events.clear()
            self._last_hotkey_event_at.clear()
            with self._state_lock:
                if self._thread and (threading.current_thread() is self._thread):
                    self._thread = None
                self._thread_id = 0

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        try:
            if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
                kb = cast(l_param, POINTER(KBDLLHOOKSTRUCT)).contents
                is_key_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_ctrl = kb.vkCode in (VK_LCONTROL, VK_RCONTROL)
                is_alt = kb.vkCode in (VK_LMENU, VK_RMENU)
                is_shift = kb.vkCode in (VK_LSHIFT, VK_RSHIFT)
                double_tap_window = self._double_tap_window_sec()

                if is_key_down and is_ctrl:
                    if not self._ctrl_pressed:
                        self._ctrl_combo_used = False
                    self._ctrl_pressed.add(int(kb.vkCode))
                elif is_key_down and not is_ctrl:
                    if self._ctrl_pressed:
                        self._ctrl_combo_used = True
                elif not is_key_down and is_ctrl:
                    had_ctrl_pressed = bool(self._ctrl_pressed)
                    self._ctrl_pressed.discard(int(kb.vkCode))
                    if had_ctrl_pressed and not self._ctrl_pressed:
                        if not self._ctrl_combo_used:
                            now = time.time()
                            if now - self._last_ctrl_at <= double_tap_window:
                                self._dispatch_callback("double_ctrl_selection", None)
                            self._last_ctrl_at = now
                        self._ctrl_combo_used = False

                if is_key_down and is_alt:
                    if not self._alt_pressed:
                        self._alt_combo_used = False
                    self._alt_pressed.add(int(kb.vkCode))
                elif is_key_down and not is_alt:
                    if self._alt_pressed:
                        self._alt_combo_used = True
                elif not is_key_down and is_alt:
                    had_alt_pressed = bool(self._alt_pressed)
                    self._alt_pressed.discard(int(kb.vkCode))
                    if had_alt_pressed and not self._alt_pressed:
                        if not self._alt_combo_used:
                            now = time.time()
                            if now - self._last_alt_at <= double_tap_window:
                                self._dispatch_callback("double_alt_selection", None)
                            self._last_alt_at = now
                        self._alt_combo_used = False

                if is_key_down and is_shift:
                    if not self._shift_pressed:
                        self._shift_combo_used = False
                    self._shift_pressed.add(int(kb.vkCode))
                elif is_key_down and not is_shift:
                    if self._shift_pressed:
                        self._shift_combo_used = True
                elif not is_key_down and is_shift:
                    had_shift_pressed = bool(self._shift_pressed)
                    self._shift_pressed.discard(int(kb.vkCode))
                    if had_shift_pressed and not self._shift_pressed:
                        if not self._shift_combo_used:
                            now = time.time()
                            if now - self._last_shift_at <= double_tap_window:
                                self._dispatch_callback("double_shift_selection", None)
                            self._last_shift_at = now
                        self._shift_combo_used = False

                self._handle_hook_hotkeys(int(kb.vkCode), is_key_down=is_key_down)
        except Exception:
            logger.exception("Keyboard hook callback failed")
        try:
            return int(user32.CallNextHookEx(self._keyboard_hook or 0, n_code, w_param, l_param))
        except Exception:
            logger.exception("CallNextHookEx failed in keyboard callback; fall back to pass-through")
            return 0
