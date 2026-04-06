from __future__ import annotations

import logging
import threading
import time
from ctypes import POINTER, WINFUNCTYPE, Structure, byref, cast, c_void_p, windll
from ctypes import wintypes


user32 = windll.user32
kernel32 = windll.kernel32

WM_QUIT = 0x0012
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202


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


class MSLLHOOKSTRUCT(Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", c_void_p),
    ]


LowLevelMouseProc = WINFUNCTYPE(wintypes.LPARAM, wintypes.INT, wintypes.WPARAM, wintypes.LPARAM)


class MouseHookManager:
    def __init__(self, callback, logger: logging.Logger | None = None) -> None:
        self.callback = callback
        self.logger = logger or logging.getLogger(__name__)
        self._thread: threading.Thread | None = None
        self._thread_id: int = 0
        self._stop_event = threading.Event()
        self._mouse_hook = None
        self._mouse_proc = None

        self._last_left_up = 0.0
        self._last_down_pos: tuple[int, int] | None = None
        self._last_down_at = 0.0
        self._last_up_pos: tuple[int, int] | None = None
        self._click_count = 0

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

        self._mouse_proc = LowLevelMouseProc(self._mouse_callback)
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self._mouse_proc,
            kernel32.GetModuleHandleW(None),
            0,
        )

        msg = MSG()
        while not self._stop_event.is_set():
            code = user32.GetMessageW(byref(msg), None, 0, 0)
            if code <= 0:
                break
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))

        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None

    def _mouse_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0:
            ms = cast(l_param, POINTER(MSLLHOOKSTRUCT)).contents
            now = time.time()
            x, y = int(ms.pt.x), int(ms.pt.y)

            if w_param == WM_LBUTTONDOWN:
                self._last_down_pos = (x, y)
                self._last_down_at = now

            if w_param == WM_LBUTTONUP:
                pair_distance = 0
                if self._last_up_pos is not None:
                    pair_distance = abs(x - int(self._last_up_pos[0])) + abs(y - int(self._last_up_pos[1]))
                if now - self._last_left_up <= 0.35 and pair_distance <= 28:
                    self._click_count += 1
                else:
                    self._click_count = 1

                down = self._last_down_pos
                moved = abs(x - down[0]) + abs(y - down[1]) if down else 0
                hold_ms = int(max(0.0, (now - float(self._last_down_at or now))) * 1000.0)
                should_emit = moved >= 1 or self._click_count >= 2
                if should_emit:
                    down_x = int(down[0]) if down else int(x)
                    down_y = int(down[1]) if down else int(y)
                    payload = {
                        "x": int(x),
                        "y": int(y),
                        "down_x": down_x,
                        "down_y": down_y,
                        "moved": int(moved),
                        "click_count": int(self._click_count),
                        "down_ts": float(self._last_down_at or 0.0),
                        "up_ts": float(now),
                        "hold_ms": int(hold_ms),
                        "ts": now,
                    }
                    try:
                        self.callback("selection_mouse_up", payload)
                    except Exception:
                        # Keep hook callback resilient; one app-side exception should not break future mouse events.
                        self.logger.exception("Mouse hook callback failed for selection_mouse_up")
                    self._click_count = 0

                self._last_left_up = now
                self._last_up_pos = (x, y)
                self._last_down_pos = None
                self._last_down_at = 0.0

        return user32.CallNextHookEx(self._mouse_hook, n_code, w_param, l_param)
