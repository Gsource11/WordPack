from __future__ import annotations

import time
from ctypes import POINTER, Structure, byref, c_size_t, memmove, sizeof, windll, wstring_at
from ctypes import wintypes
from dataclasses import dataclass
import winreg

from src.screenshot import capture_screen_region, get_virtual_screen_region


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
VK_C = 0x43
VK_CONTROL = 0x11


user32 = windll.user32
kernel32 = windll.kernel32


class POINT(Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


@dataclass(frozen=True)
class ScreenBounds:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return int(self.left + self.width)

    @property
    def bottom(self) -> int:
        return int(self.top + self.height)

    def to_payload(self) -> dict[str, int]:
        return {
            "left": int(self.left),
            "top": int(self.top),
            "width": int(self.width),
            "height": int(self.height),
            "right": int(self.right),
            "bottom": int(self.bottom),
        }


def get_virtual_screen_bounds() -> ScreenBounds:
    region = get_virtual_screen_region()
    return ScreenBounds(
        left=int(region.left),
        top=int(region.top),
        width=int(region.width),
        height=int(region.height),
    )


def capture_virtual_screen():
    region = get_virtual_screen_region()
    # On some systems the first frame after hotkey/activation can be stale.
    # Flush compositor + take warm-up frames, and use the last frame.
    try:
        windll.dwmapi.DwmFlush()
    except Exception:
        pass
    latest = capture_screen_region(region)
    try:
        for _ in range(2):
            time.sleep(0.010)
            try:
                windll.dwmapi.DwmFlush()
            except Exception:
                pass
            latest = capture_screen_region(region)
        return latest
    except Exception:
        return latest


def get_cursor_position() -> tuple[int, int]:
    point = POINT()
    if bool(user32.GetCursorPos(byref(point))):
        return int(point.x), int(point.y)
    return 0, 0


def _open_clipboard() -> bool:
    for _ in range(10):
        if bool(user32.OpenClipboard(0)):
            return True
        time.sleep(0.02)
    return False


def get_clipboard_text(raw: bool = False) -> str | None:
    if not _open_clipboard():
        return None if raw else ""

    text: str | None = None
    try:
        handle = int(user32.GetClipboardData(CF_UNICODETEXT))
        if handle:
            ptr = int(kernel32.GlobalLock(handle))
            if ptr:
                try:
                    text = wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()

    if text is None:
        return None if raw else ""
    return text if raw else text.strip()


def set_clipboard_text(text: str | None) -> bool:
    if text is None:
        return False

    payload = text.encode("utf-16-le") + b"\x00\x00"

    for _ in range(10):
        if not _open_clipboard():
            time.sleep(0.02)
            continue

        try:
            if not bool(user32.EmptyClipboard()):
                continue

            hmem = int(kernel32.GlobalAlloc(GMEM_MOVEABLE, c_size_t(len(payload))))
            if hmem == 0:
                continue

            ptr = int(kernel32.GlobalLock(hmem))
            if ptr == 0:
                kernel32.GlobalFree(hmem)
                continue

            try:
                memmove(ptr, payload, len(payload))
            finally:
                kernel32.GlobalUnlock(hmem)

            if bool(user32.SetClipboardData(CF_UNICODETEXT, hmem)):
                return True

            kernel32.GlobalFree(hmem)
        finally:
            user32.CloseClipboard()

        time.sleep(0.02)

    return False


def copy_selection_once(wait_sec: float = 0.1) -> str:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(wait_sec)
    return (get_clipboard_text() or "").strip()


def get_system_theme_preference() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(value) else "dark"
    except Exception:
        return "light"
