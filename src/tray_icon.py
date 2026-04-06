from __future__ import annotations

import ctypes
import threading
from ctypes import Structure, WINFUNCTYPE, byref, sizeof, windll
from ctypes import wintypes
from typing import Callable


user32 = windll.user32
kernel32 = windll.kernel32
shell32 = windll.shell32

WM_APP = 0x8000
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_CONTEXTMENU = 0x007B
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_NULL = 0x0000

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

NOTIFYICON_VERSION_3 = 3

MF_STRING = 0x00000000
MF_CHECKED = 0x00000008
MF_BYPOSITION = 0x00000400

TPM_LEFTALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

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
    ID_EXIT = 1001
    ID_MAIN = 1002
    ID_HISTORY = 1003
    ID_SETTINGS = 1004
    ID_SELECTION = 1005
    ID_SCREENSHOT = 1006
    ID_STARTUP = 1007

    def __init__(
        self,
        *,
        title: str,
        icon_path: str,
        on_action: Callable[[str], None],
        state_getter: Callable[[], dict[str, bool]],
        logger,
    ) -> None:
        self._title = str(title or "WordPack")
        self._icon_path = str(icon_path or "")
        self._on_action = on_action
        self._state_getter = state_getter
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
                    self._show_context_menu(hwnd)
                    return 0
                if raw_event == WM_LBUTTONDBLCLK or event_code == WM_LBUTTONDBLCLK:
                    self._dispatch_action("show_main")
                    return 0
            elif message == WM_COMMAND:
                command_id = int(w_param) & 0xFFFF
                self._handle_command(command_id)
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

    def _show_context_menu(self, hwnd: int) -> None:
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        state = self._state_getter() or {}
        startup_enabled = bool(state.get("startup_launch_enabled", False))
        selection_enabled = bool(state.get("selection_enabled", True))
        screenshot_enabled = bool(state.get("screenshot_enabled", True))

        # Menu appears top->bottom.
        # Keep "开机自启动" as the first item for quick access.
        user32.AppendMenuW(
            menu,
            MF_BYPOSITION | MF_STRING | (MF_CHECKED if startup_enabled else 0),
            self.ID_STARTUP,
            "开机自启动",
        )
        user32.AppendMenuW(menu, MF_BYPOSITION | MF_STRING, self.ID_MAIN, "主界面")
        user32.AppendMenuW(menu, MF_BYPOSITION | MF_STRING, self.ID_HISTORY, "历史")
        user32.AppendMenuW(menu, MF_BYPOSITION | MF_STRING, self.ID_SETTINGS, "设置")
        user32.AppendMenuW(
            menu,
            MF_BYPOSITION | MF_STRING | (MF_CHECKED if selection_enabled else 0),
            self.ID_SELECTION,
            "划词",
        )
        user32.AppendMenuW(
            menu,
            MF_BYPOSITION | MF_STRING | (MF_CHECKED if screenshot_enabled else 0),
            self.ID_SCREENSHOT,
            "截图",
        )
        user32.AppendMenuW(menu, MF_BYPOSITION | MF_STRING, self.ID_EXIT, "退出")

        pt = POINT()
        user32.GetCursorPos(byref(pt))
        user32.SetForegroundWindow(hwnd)
        command = user32.TrackPopupMenu(
            menu,
            TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            int(pt.x),
            int(pt.y),
            0,
            hwnd,
            None,
        )
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if command:
            self._handle_command(int(command))

    def _handle_command(self, command_id: int) -> None:
        action_map = {
            self.ID_EXIT: "exit",
            self.ID_MAIN: "show_main",
            self.ID_HISTORY: "open_history",
            self.ID_SETTINGS: "open_settings",
            self.ID_SELECTION: "toggle_selection",
            self.ID_SCREENSHOT: "toggle_screenshot",
            self.ID_STARTUP: "toggle_startup",
        }
        action = action_map.get(int(command_id))
        if not action:
            return
        self._dispatch_action(action)

    def _dispatch_action(self, action: str) -> None:
        try:
            self._on_action(action)
        except Exception:
            self._logger.exception("Tray action failed: %s", action)
