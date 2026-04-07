from __future__ import annotations

import ctypes
import threading
from ctypes import Structure, WINFUNCTYPE, byref, sizeof, windll
from ctypes import wintypes
from typing import Any, Callable


user32 = windll.user32
kernel32 = windll.kernel32
shell32 = windll.shell32

WM_APP = 0x8000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_CONTEXTMENU = 0x007B
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

NOTIFYICON_VERSION_3 = 3

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512


class GUID(Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


LRESULT = ctypes.c_ssize_t
WndProcType = WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WndProcType),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


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


shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class TrayIconManager:
    def __init__(
        self,
        *,
        title: str,
        icon_path: str,
        on_action: Callable[[str, dict[str, Any] | None], None],
        logger,
    ) -> None:
        self._title = str(title or "WordPack")
        self._icon_path = str(icon_path or "")
        self._on_action = on_action
        self._logger = logger

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._thread_id = 0
        self._hwnd = 0
        self._icon_handle = 0
        self._wnd_class_name = "WordPackTrayIconWindow"
        self._msg_callback = WM_APP + 0x31
        self._nid: NOTIFYICONDATAW | None = None
        self._wnd_proc = WndProcType(self._window_proc)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="wordpack-tray-icon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_CLOSE, 0, 0)
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.2)

    def _run_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        instance = kernel32.GetModuleHandleW(None)
        self._icon_handle = self._load_icon()

        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wnd_proc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = instance
        wc.hIcon = self._icon_handle
        wc.hCursor = 0
        wc.hbrBackground = 0
        wc.lpszMenuName = None
        wc.lpszClassName = self._wnd_class_name

        atom = user32.RegisterClassW(byref(wc))
        if not atom:
            self._logger.exception("Failed to register tray icon window class")
            return

        hwnd = user32.CreateWindowExW(
            0,
            self._wnd_class_name,
            self._title,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            instance,
            None,
        )
        if not hwnd:
            self._logger.exception("Failed to create tray icon message window")
            return
        self._hwnd = int(hwnd)

        self._add_tray_icon()
        msg = MSG()
        while not self._stop_event.is_set():
            code = user32.GetMessageW(byref(msg), None, 0, 0)
            if code <= 0:
                break
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))

        self._remove_tray_icon()
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = 0
        user32.UnregisterClassW(self._wnd_class_name, instance)

    def _load_icon(self) -> int:
        if self._icon_path:
            handle = user32.LoadImageW(
                None,
                self._icon_path,
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if handle:
                return int(handle)
        fallback = user32.LoadIconW(None, IDI_APPLICATION)
        return int(fallback or 0)

    def _add_tray_icon(self) -> None:
        if not self._hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self._msg_callback
        nid.hIcon = self._icon_handle
        nid.szTip = self._title[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, byref(nid)):
            self._logger.error("Failed to add tray icon")
            return
        nid.uTimeoutOrVersion = NOTIFYICON_VERSION_3
        shell32.Shell_NotifyIconW(NIM_SETVERSION, byref(nid))
        self._nid = nid

    def _remove_tray_icon(self) -> None:
        if self._nid is None:
            return
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, byref(self._nid))
        except Exception:
            self._logger.exception("Failed to remove tray icon")
        self._nid = None

    @staticmethod
    def _loword(value: int) -> int:
        return int(value) & 0xFFFF

    def _window_proc(self, hwnd, message, w_param, l_param):
        try:
            if message == self._msg_callback:
                raw_event = int(l_param)
                event_code = self._loword(raw_event)
                if raw_event in (WM_RBUTTONUP, WM_RBUTTONDOWN, WM_CONTEXTMENU) or event_code in (
                    WM_RBUTTONUP,
                    WM_RBUTTONDOWN,
                    WM_CONTEXTMENU,
                ):
                    pt = POINT()
                    user32.GetCursorPos(byref(pt))
                    self._dispatch_action("show_tray_menu", {"x": int(pt.x), "y": int(pt.y)})
                    return 0
                if raw_event == WM_LBUTTONDBLCLK or event_code == WM_LBUTTONDBLCLK:
                    self._dispatch_action("show_main")
                    return 0
            elif message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            elif message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
        except Exception:
            self._logger.exception("Error in tray window proc")
        return user32.DefWindowProcW(hwnd, message, w_param, l_param)

    def _dispatch_action(self, action: str, payload: dict[str, Any] | None = None) -> None:
        try:
            self._on_action(action, payload)
        except Exception:
            self._logger.exception("Tray action failed: %s", action)
