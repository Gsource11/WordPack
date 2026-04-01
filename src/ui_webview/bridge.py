from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from typing import Any


class FrontendBridge:
    def __init__(self, logger) -> None:
        self.logger = logger
        self._lock = threading.RLock()
        self._windows: dict[str, Any] = {}
        self._ready: dict[str, bool] = defaultdict(bool)
        self._pending: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)

    def register_window(self, kind: str, window: Any) -> None:
        with self._lock:
            self._windows[kind] = window
            self._ready[kind] = False

    def unregister_window(self, kind: str) -> None:
        with self._lock:
            self._windows.pop(kind, None)
            self._ready.pop(kind, None)
            self._pending.pop(kind, None)

    def mark_ready(self, kind: str) -> None:
        with self._lock:
            self._ready[kind] = True
            pending = list(self._pending.get(kind, []))
            self._pending[kind].clear()

        if not pending:
            return

        # pywebview's evaluate_js is synchronous on Windows. Flushing queued
        # events inside a JS API call like bootstrap() can deadlock the UI
        # thread, so defer delivery until after the call unwinds.
        def flush() -> None:
            time.sleep(0.05)
            for event, payload in pending:
                self.send(kind, event, payload)

        threading.Thread(target=flush, daemon=True).start()

    def send(self, kind: str, event: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        with self._lock:
            window = self._windows.get(kind)
            if window is None:
                return
            if not self._ready.get(kind):
                self._pending[kind].append((event, payload))
                return

        script = (
            "window.WordPack && window.WordPack.receive("
            f"{json.dumps(event, ensure_ascii=False)}, "
            f"{json.dumps(payload, ensure_ascii=False)});"
        )
        try:
            window.evaluate_js(script)
        except Exception:
            self.logger.exception("Failed to deliver frontend event kind=%s event=%s", kind, event)

    def broadcast(self, event: str, payload: dict[str, Any] | None = None, kinds: list[str] | None = None) -> None:
        targets = kinds or list(self._windows.keys())
        for kind in targets:
            self.send(kind, event, payload)
