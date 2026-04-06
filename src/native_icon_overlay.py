from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_LBUTTONUP = 0x0202
WM_SETCURSOR = 0x0020
WM_APP = 0x8000
WM_APP_SHOW = WM_APP + 301
WM_APP_HIDE = WM_APP + 302

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
IDC_ARROW = 32512

BI_RGB = 0
DIB_RGB_COLORS = 0

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class NativeIconOverlay:
    def __init__(
        self,
        *,
        icon_path: Path,
        logger,
        on_click: Callable[[], None],
        window_size: int = 34,
        icon_size: int = 22,
    ) -> None:
        self._icon_path = icon_path
        self._logger = logger
        self._on_click = on_click
        self._window_size = int(max(18, window_size))
        self._icon_size = int(max(12, min(icon_size, window_size)))
        self._class_name = "WordPackNativeIconOverlay"
        self._wndproc_ref = None
        self._thread: threading.Thread | None = None
        self._thread_ready = threading.Event()
        self._hwnd: int | None = None
        self._lock = threading.RLock()
        self._x = 0
        self._y = 0
        self._visible = False
        self._bgra = self._load_icon_surface()

    def show(self, x: int, y: int) -> None:
        with self._lock:
            self._x = int(x)
            self._y = int(y)
        self._ensure_thread()
        hwnd = self._wait_hwnd(timeout=1.2)
        if not hwnd:
            # Retry once on slow window creation paths.
            self._ensure_thread()
            hwnd = self._wait_hwnd(timeout=0.8)
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_SHOW, 0, 0)

    def hide(self) -> None:
        hwnd = self._wait_hwnd(timeout=0.02)
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_HIDE, 0, 0)
        with self._lock:
            self._visible = False

    def destroy(self) -> None:
        hwnd = self._wait_hwnd(timeout=0.05)
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        with self._lock:
            self._thread = None
            self._hwnd = None
            self._visible = False
            self._thread_ready.clear()

    def is_visible(self) -> bool:
        with self._lock:
            return bool(self._visible and self._hwnd)

    def _wait_hwnd(self, timeout: float = 0.3) -> int | None:
        self._thread_ready.wait(timeout=max(0.0, timeout))
        with self._lock:
            return self._hwnd

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread_ready.clear()
            self._thread = threading.Thread(target=self._run_loop, name="wordpack-native-icon-overlay", daemon=True)
            self._thread.start()

    def _run_loop(self) -> None:
        try:
            h_instance = kernel32.GetModuleHandleW(None)
            wndproc = WNDPROC(self._wnd_proc)
            self._wndproc_ref = wndproc
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = wndproc
            wc.cbClsExtra = 0
            wc.cbWndExtra = 0
            wc.hInstance = h_instance
            wc.hIcon = 0
            wc.hCursor = user32.LoadCursorW(0, ctypes.c_void_p(IDC_ARROW))
            wc.hbrBackground = 0
            wc.lpszMenuName = None
            wc.lpszClassName = self._class_name
            user32.RegisterClassW(ctypes.byref(wc))

            hwnd = user32.CreateWindowExW(
                WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                self._class_name,
                "WordPack Native Icon",
                WS_POPUP,
                0,
                0,
                self._window_size,
                self._window_size,
                0,
                0,
                h_instance,
                0,
            )
            if not hwnd:
                return

            with self._lock:
                self._hwnd = int(hwnd)
            self._thread_ready.set()

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self._logger.exception("Native icon overlay loop failed")
        finally:
            with self._lock:
                self._visible = False
                self._hwnd = None
            self._thread_ready.set()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001
        if msg == WM_APP_SHOW:
            self._show_internal(hwnd)
            return 0
        if msg == WM_APP_HIDE:
            user32.ShowWindow(hwnd, SW_HIDE)
            with self._lock:
                self._visible = False
            return 0
        if msg == WM_LBUTTONUP:
            threading.Thread(target=self._safe_click, daemon=True).start()
            return 0
        if msg == WM_SETCURSOR:
            user32.SetCursor(user32.LoadCursorW(0, ctypes.c_void_p(IDC_ARROW)))
            return 1
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _safe_click(self) -> None:
        try:
            self._on_click()
        except Exception:
            self._logger.exception("Native icon overlay click callback failed")

    def _show_internal(self, hwnd: int) -> None:
        try:
            with self._lock:
                x = int(self._x)
                y = int(self._y)
            self._update_layered(hwnd, x, y)
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            with self._lock:
                self._visible = True
        except Exception:
            self._logger.exception("Failed to show native icon overlay")

    def _update_layered(self, hwnd: int, x: int, y: int) -> None:
        w = self._window_size
        h = self._window_size
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbitmap = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), 0, 0)
        if not hbitmap or not bits:
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            if hdc_screen:
                user32.ReleaseDC(0, hdc_screen)
            return

        ctypes.memmove(bits, self._bgra, len(self._bgra))
        old_obj = gdi32.SelectObject(hdc_mem, hbitmap)

        pt_dst = POINT(int(x), int(y))
        size = SIZE(w, h)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(
            hwnd,
            hdc_screen,
            ctypes.byref(pt_dst),
            ctypes.byref(size),
            hdc_mem,
            ctypes.byref(pt_src),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )

        gdi32.SelectObject(hdc_mem, old_obj)
        gdi32.DeleteObject(hbitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

    def _load_icon_surface(self) -> bytes:
        w = self._window_size
        h = self._window_size
        rgba: bytes
        try:
            from PIL import Image
            from PIL import ImageDraw

            # Draw in supersampled space then downscale to smooth circle edges.
            ss = 4
            hw = int(w * ss)
            hh = int(h * ss)
            canvas = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
            draw = ImageDraw.Draw(canvas)
            # Visual token: warm light circle that improves contrast for orange icon.
            bg_d = max(20, min(30, self._icon_size + 9))
            bg_d_hi = int(bg_d * ss)
            bg_left = (hw - bg_d_hi) // 2
            bg_top = (hh - bg_d_hi) // 2
            bg_box = (bg_left, bg_top, bg_left + bg_d_hi, bg_top + bg_d_hi)
            border_width = max(2, int(round(1.6 * ss)))
            draw.ellipse(
                bg_box,
                fill=(245, 247, 250, 238),
                outline=(196, 205, 218, 245),
                width=border_width,
            )
            if self._icon_path.exists():
                with Image.open(self._icon_path) as image:
                    icon = image.convert("RGBA").resize((self._icon_size * ss, self._icon_size * ss), Image.LANCZOS)
                offset = ((hw - (self._icon_size * ss)) // 2, (hh - (self._icon_size * ss)) // 2)
                canvas.alpha_composite(icon, dest=offset)
            canvas = canvas.resize((w, h), Image.LANCZOS)
            rgba = canvas.tobytes("raw", "RGBA")
        except Exception:
            # Fallback glyph (circle token + orange ring/dot) when PIL/icon asset is unavailable.
            data = bytearray(w * h * 4)
            cx = w // 2
            cy = h // 2
            bg_r = max(10, min(14, (self._icon_size + 8) // 2))
            outer = max(5, self._icon_size // 2)
            inner = max(4, outer - 2)
            for y in range(h):
                for x in range(w):
                    dx = x - cx
                    dy = y - cy
                    dist2 = dx * dx + dy * dy
                    idx = (y * w + x) * 4
                    if dist2 <= bg_r * bg_r:
                        data[idx:idx + 4] = bytes((250, 247, 245, 235))
                    if dist2 <= outer * outer and dist2 >= inner * inner:
                        data[idx:idx + 4] = bytes((255, 122, 33, 255))
                    elif dist2 < inner * inner and dist2 <= 4:
                        data[idx:idx + 4] = bytes((255, 122, 33, 255))
            rgba = bytes(data)

        data = bytearray(w * h * 4)
        for i in range(0, len(rgba), 4):
            r = rgba[i]
            g = rgba[i + 1]
            b = rgba[i + 2]
            a = rgba[i + 3]
            rp = (r * a) // 255
            gp = (g * a) // 255
            bp = (b * a) // 255
            data[i] = bp
            data[i + 1] = gp
            data[i + 2] = rp
            data[i + 3] = a
        return bytes(data)
