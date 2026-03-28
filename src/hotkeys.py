from __future__ import annotations

import threading
import time
from ctypes import POINTER, WINFUNCTYPE, Structure, byref, cast, c_void_p, windll
from ctypes import wintypes


user32 = windll.user32
kernel32 = windll.kernel32


WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002

HOTKEY_ID_TRANSLATE = 1001
HOTKEY_ID_TOGGLE_WINDOW = 1002
VK_T = 0x54
VK_H = 0x48


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
    def __init__(self, callback) -> None:
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._stop_event = threading.Event()
        self._keyboard_hook = None
        self._keyboard_proc = None
        self._last_ctrl_at = 0.0

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

        if not user32.RegisterHotKey(None, HOTKEY_ID_TRANSLATE, MOD_CONTROL | MOD_ALT, VK_T):
            self.callback("status", "全局热键注册失败：Ctrl+Alt+T 已被占用")
        if not user32.RegisterHotKey(None, HOTKEY_ID_TOGGLE_WINDOW, MOD_CONTROL | MOD_ALT, VK_H):
            self.callback("status", "全局热键注册失败：Ctrl+Alt+H 已被占用")

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

            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_TRANSLATE:
                self.callback("translate_selection", None)
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_TOGGLE_WINDOW:
                self.callback("toggle_window", None)

            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))

        if self._keyboard_hook:
            user32.UnhookWindowsHookEx(self._keyboard_hook)
            self._keyboard_hook = None
        user32.UnregisterHotKey(None, HOTKEY_ID_TRANSLATE)
        user32.UnregisterHotKey(None, HOTKEY_ID_TOGGLE_WINDOW)

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            kb = cast(l_param, POINTER(KBDLLHOOKSTRUCT)).contents
            if kb.vkCode in (VK_LCONTROL, VK_RCONTROL):
                now = time.time()
                if now - self._last_ctrl_at <= 0.35:
                    self.callback("double_ctrl_selection", None)
                self._last_ctrl_at = now

        return user32.CallNextHookEx(self._keyboard_hook, n_code, w_param, l_param)

