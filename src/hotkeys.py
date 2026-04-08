from __future__ import annotations

import threading
import time
from ctypes import POINTER, WINFUNCTYPE, Structure, byref, cast, c_void_p, windll
from ctypes import wintypes
from typing import Callable


user32 = windll.user32
kernel32 = windll.kernel32


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

HOTKEY_ID_SCREENSHOT = 1003

HOTKEY_EVENT_MAP = {
    HOTKEY_ID_SCREENSHOT: "screenshot_translate",
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


class HotkeyManager:
    def __init__(self, callback, shortcut_getter: Callable[[], dict[str, str]] | None = None) -> None:
        self.callback = callback
        self.shortcut_getter = shortcut_getter or (lambda: {})
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

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.2)

    def _loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        shortcut_map = {
            "screenshot_translate": "",
        }
        shortcut_map.update(self.shortcut_getter() or {})

        for hotkey_id, event_name in HOTKEY_EVENT_MAP.items():
            parsed = parse_shortcut(shortcut_map.get(event_name, ""))
            if not parsed:
                continue
            modifiers, vk, label = parsed
            if user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                self._registered_hotkeys.append(hotkey_id)
            else:
                self.callback("status", f"全局热键注册失败：{label} 已被占用")

        self._keyboard_proc = LowLevelKeyboardProc(self._keyboard_callback)
        self._keyboard_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._keyboard_proc,
            kernel32.GetModuleHandleW(None),
            0,
        )

        msg = MSG()
        while not self._stop_event.is_set():
            code = user32.GetMessageW(byref(msg), None, 0, 0)
            if code <= 0:
                break

            if msg.message == WM_HOTKEY:
                event_name = HOTKEY_EVENT_MAP.get(int(msg.wParam))
                if event_name:
                    self.callback(event_name, None)

            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))

        if self._keyboard_hook:
            user32.UnhookWindowsHookEx(self._keyboard_hook)
            self._keyboard_hook = None
        for hotkey_id in self._registered_hotkeys:
            user32.UnregisterHotKey(None, hotkey_id)
        self._registered_hotkeys.clear()

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
            kb = cast(l_param, POINTER(KBDLLHOOKSTRUCT)).contents
            is_key_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_ctrl = kb.vkCode in (VK_LCONTROL, VK_RCONTROL)
            is_alt = kb.vkCode in (VK_LMENU, VK_RMENU)
            is_shift = kb.vkCode in (VK_LSHIFT, VK_RSHIFT)

            if is_key_down and is_ctrl:
                if not self._ctrl_pressed:
                    self._ctrl_combo_used = False
                self._ctrl_pressed.add(int(kb.vkCode))
            elif is_key_down and not is_ctrl:
                if self._ctrl_pressed:
                    self._ctrl_combo_used = True
            elif not is_key_down and is_ctrl:
                self._ctrl_pressed.discard(int(kb.vkCode))
                if not self._ctrl_pressed:
                    if not self._ctrl_combo_used:
                        now = time.time()
                        if now - self._last_ctrl_at <= 0.35:
                            self.callback("double_ctrl_selection", None)
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
                self._alt_pressed.discard(int(kb.vkCode))
                if not self._alt_pressed:
                    if not self._alt_combo_used:
                        now = time.time()
                        if now - self._last_alt_at <= 0.35:
                            self.callback("double_alt_selection", None)
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
                self._shift_pressed.discard(int(kb.vkCode))
                if not self._shift_pressed:
                    if not self._shift_combo_used:
                        now = time.time()
                        if now - self._last_shift_at <= 0.35:
                            self.callback("double_shift_selection", None)
                        self._last_shift_at = now
                    self._shift_combo_used = False

        return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)
