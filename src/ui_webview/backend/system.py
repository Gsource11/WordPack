from __future__ import annotations

import ctypes
import time
from ctypes import POINTER, Structure, byref, c_size_t, memmove, sizeof, windll, wstring_at
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
import winreg

from src.screenshot import capture_screen_region, get_virtual_screen_region


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
VK_C = 0x43
VK_CONTROL = 0x11


user32 = windll.user32
kernel32 = windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _configure_winapi_signatures() -> None:
    # Use pointer-sized signatures for clipboard/global-memory APIs.
    # Without explicit arg/restype, ctypes defaults to c_int and can overflow on 64-bit handles.
    try:
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
    except Exception:
        pass

    try:
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
    except Exception:
        pass


_configure_winapi_signatures()


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


def get_foreground_window_handle() -> int:
    try:
        hwnd = int(user32.GetForegroundWindow())
    except Exception:
        return 0
    return hwnd if hwnd > 0 else 0


def get_foreground_process_name() -> str:
    hwnd = get_foreground_window_handle()
    if hwnd <= 0:
        return ""

    pid = wintypes.DWORD(0)
    try:
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), byref(pid))
    except Exception:
        return ""
    if int(pid.value or 0) <= 0:
        return ""

    process_handle = 0
    try:
        process_handle = int(kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid.value)))
        if process_handle <= 0:
            return ""
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(int(size.value))
        query = getattr(kernel32, "QueryFullProcessImageNameW", None)
        if query is None:
            return ""
        ok = bool(query(wintypes.HANDLE(process_handle), 0, buffer, byref(size)))
        if not ok:
            return ""
        full_path = str(buffer.value or "").strip()
        if not full_path:
            return ""
        return Path(full_path).name.lower()
    except Exception:
        return ""
    finally:
        if process_handle:
            try:
                kernel32.CloseHandle(wintypes.HANDLE(process_handle))
            except Exception:
                pass


def get_system_dpi_scale() -> float:
    try:
        get_dpi_for_system = getattr(user32, "GetDpiForSystem", None)
        if get_dpi_for_system is not None:
            dpi = int(get_dpi_for_system())
            if dpi > 0:
                return max(0.75, min(4.0, float(dpi) / 96.0))
    except Exception:
        pass

    try:
        hdc = user32.GetDC(0)
        if hdc:
            dpi_x = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
            user32.ReleaseDC(0, hdc)
            if dpi_x > 0:
                return max(0.75, min(4.0, float(dpi_x) / 96.0))
    except Exception:
        pass

    return 1.0


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
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if handle:
            ptr = kernel32.GlobalLock(handle)
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

            hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, c_size_t(len(payload)))
            if not hmem:
                continue

            ptr = kernel32.GlobalLock(hmem)
            if not ptr:
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
