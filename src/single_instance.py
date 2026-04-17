from __future__ import annotations

import ctypes
import getpass
import threading
import time
from multiprocessing.connection import Client, Listener
from typing import Callable

from src.app_logging import get_logger


logger = get_logger(__name__)
kernel32 = ctypes.windll.kernel32
ERROR_ALREADY_EXISTS = 183
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_bool
kernel32.GetLastError.restype = ctypes.c_ulong
kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
kernel32.WaitNamedPipeW.restype = ctypes.c_bool


class SingleInstanceManager:
    def __init__(self, app_id: str = "WordPack") -> None:
        token = self._build_user_token()
        self._mutex_name = f"Local\\{app_id}.SingleInstance.{token}"
        self._pipe_address = rf"\\.\pipe\{app_id}.SingleInstance.{token}"
        self._authkey = b"wordpack-single-instance-v1"
        self._mutex_handle: int | None = None
        self._listener: Listener | None = None
        self._listener_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._handler_lock = threading.RLock()
        self._command_handler: Callable[[str], None] | None = None

    @staticmethod
    def _build_user_token() -> str:
        username = str(getpass.getuser() or "user").strip().lower()
        safe = "".join(ch if ch.isalnum() else "_" for ch in username)
        return safe or "user"

    def acquire(self) -> bool:
        handle = kernel32.CreateMutexW(None, False, self._mutex_name)
        if not handle:
            raise OSError(f"CreateMutexW failed for {self._mutex_name}")
        already_exists = int(kernel32.GetLastError()) == ERROR_ALREADY_EXISTS
        if already_exists:
            kernel32.CloseHandle(handle)
            return False
        self._mutex_handle = int(handle)
        self._start_listener()
        return True

    def set_command_handler(self, handler: Callable[[str], None]) -> None:
        with self._handler_lock:
            self._command_handler = handler

    def send_command(self, command: str, timeout_sec: float = 1.5) -> bool:
        payload = str(command or "").strip()
        if not payload:
            return False
        timeout_ms = max(120, min(4000, int(float(timeout_sec) * 1000)))
        try:
            if not bool(kernel32.WaitNamedPipeW(self._pipe_address, int(timeout_ms))):
                logger.warning("Single-instance pipe not ready within timeout command=%s timeout_ms=%s", payload, timeout_ms)
                return False
            with Client(self._pipe_address, family="AF_PIPE", authkey=self._authkey) as conn:
                conn.send_bytes(payload.encode("utf-8", errors="ignore"))
            return True
        except Exception:
            logger.exception("Failed to send single-instance command=%s timeout=%.2f", payload, float(timeout_sec))
            return False

    def stop(self) -> None:
        self._stop_event.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass
        thread = self._listener_thread
        self._listener_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.8)
        handle = self._mutex_handle
        self._mutex_handle = None
        if handle:
            try:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
            except Exception:
                pass

    def _start_listener(self) -> None:
        if self._listener_thread is not None and self._listener_thread.is_alive():
            return
        self._stop_event.clear()
        self._listener_thread = threading.Thread(
            target=self._listener_loop,
            name="wordpack-single-instance-listener",
            daemon=True,
        )
        self._listener_thread.start()

    def _listener_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                listener = Listener(self._pipe_address, family="AF_PIPE", authkey=self._authkey)
                self._listener = listener
                while not self._stop_event.is_set():
                    conn = listener.accept()
                    with conn:
                        raw = conn.recv_bytes(256)
                        command = raw.decode("utf-8", errors="ignore").strip()
                        if command:
                            self._dispatch_command(command)
            except Exception:
                if self._stop_event.is_set():
                    break
                logger.exception("Single-instance pipe listener failed; restarting")
                time.sleep(0.25)
            finally:
                listener = self._listener
                self._listener = None
                if listener is not None:
                    try:
                        listener.close()
                    except Exception:
                        pass

    def _dispatch_command(self, command: str) -> None:
        with self._handler_lock:
            handler = self._command_handler
            if handler is None:
                logger.info("Single-instance command dropped because handler not ready: %s", command)
                return
        try:
            handler(command)
        except Exception:
            logger.exception("Single-instance command handler failed command=%s", command)
