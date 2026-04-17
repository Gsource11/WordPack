from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass
class TtsState:
    available: bool = False
    status: str = "stopped"  # stopped | playing
    source_key: str = ""
    text: str = ""
    voice_name: str = ""
    message: str = ""

    def to_payload(self) -> dict:
        return asdict(self)


class SapTtsService:
    _SVS_FLAGS_ASYNC = 1
    _SVS_FLAGS_PURGE = 2

    def __init__(self, logger, on_state: Callable[[dict], None] | None = None) -> None:
        self._logger = logger
        self._on_state = on_state
        self._lock = threading.RLock()
        self._queue: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._voice = None
        self._state = TtsState()
        self._ready = False
        self._failed_reason = ""
        self._active_stream_id = 0
        self._play_started_at = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="wordpack-tts", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._queue.put(("shutdown", {}))
        self._stop_event.set()
        thread = None
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def get_state(self) -> dict:
        with self._lock:
            return self._state.to_payload()

    def play(self, text: str, source_key: str) -> dict:
        normalized = str(text or "").strip()
        if not normalized:
            return {"ok": False, "message": "No text to speak", "ttsState": self.get_state()}
        self.start()
        self._queue.put(("play", {"text": normalized, "source_key": str(source_key or "").strip()}))
        return {"ok": True}

    def stop_playback(self) -> dict:
        self._queue.put(("stop", {}))
        return {"ok": True}

    def toggle(self, text: str, source_key: str) -> dict:
        with self._lock:
            status = str(self._state.status or "stopped")
            active_key = str(self._state.source_key or "")
        next_source = str(source_key or "").strip()
        if status == "playing" and active_key == next_source:
            return self.stop_playback()
        return self.play(text, next_source)

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

    def _set_state(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)

    def _emit_state(self) -> None:
        callback = self._on_state
        if callback is None:
            return
        try:
            callback(self.get_state())
        except Exception:
            self._logger.exception("Failed to dispatch TTS state update")

    def _pick_voice_token(self, text: str):
        try:
            if self._voice is None:
                return None
            voices = self._voice.GetVoices()
            count = int(getattr(voices, "Count", 0) or 0)
            if count <= 0:
                return None
            want_cn = self._contains_cjk(text)
            primary = []
            fallback = []
            for i in range(count):
                token = voices.Item(i)
                desc = str(token.GetDescription() or "")
                lowered = desc.lower()
                if want_cn:
                    if any(k in lowered for k in ("chinese", "huihui", "xiaoxiao")):
                        primary.append((token, desc))
                    elif "zh" in lowered:
                        fallback.append((token, desc))
                else:
                    if any(k in lowered for k in ("english", "zira", "david")):
                        primary.append((token, desc))
                    elif "en" in lowered:
                        fallback.append((token, desc))
            return primary[0] if primary else (fallback[0] if fallback else None)
        except Exception:
            self._logger.exception("Failed to pick SAPI voice token")
            return None

    def _safe_stop(self) -> None:
        if self._voice is None:
            return
        try:
            self._voice.Speak("", self._SVS_FLAGS_ASYNC | self._SVS_FLAGS_PURGE)
        except Exception:
            pass

    def _run_loop(self) -> None:
        try:
            import pythoncom  # type: ignore[import-not-found]
            import win32com.client  # type: ignore[import-not-found]
        except Exception as exc:
            self._failed_reason = f"TTS dependency unavailable: {exc}"
            self._set_state(available=False, status="stopped", message=self._failed_reason)
            self._emit_state()
            return

        class _VoiceEvents:
            def OnEndStream(inner_self, StreamNumber, StreamPosition):  # noqa: N802
                del inner_self, StreamPosition
                try:
                    self._on_end_event(int(StreamNumber or 0))
                except Exception:
                    self._on_end_event(0)

        pythoncom.CoInitialize()
        try:
            self._voice = win32com.client.DispatchWithEvents("SAPI.SpVoice", _VoiceEvents)
            self._ready = True
            self._set_state(available=True, message="")
            self._emit_state()
        except Exception as exc:
            self._ready = False
            self._failed_reason = f"SAPI init failed: {exc}"
            self._set_state(available=False, status="stopped", message=self._failed_reason)
            self._emit_state()
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            return

        while not self._stop_event.is_set():
            try:
                command, payload = self._queue.get(timeout=0.04)
                self._handle_command(command, payload)
            except queue.Empty:
                pass
            except Exception:
                self._logger.exception("TTS command loop failed")
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                self._logger.exception("TTS message pump failed")
                time.sleep(0.06)
            self._poll_playback_done()

        try:
            self._safe_stop()
        finally:
            self._ready = False
            self._set_state(available=False, status="stopped", source_key="", text="")
            self._emit_state()
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _handle_command(self, command: str, payload: dict) -> None:
        if command == "shutdown":
            self._stop_event.set()
            return
        if not self._ready or self._voice is None:
            self._set_state(available=False, status="stopped", message=self._failed_reason or "TTS unavailable")
            self._emit_state()
            return

        if command == "play":
            text = str(payload.get("text") or "").strip()
            source_key = str(payload.get("source_key") or "").strip()
            if not text:
                return
            with self._lock:
                self._active_stream_id = -1
            self._safe_stop()
            selected = self._pick_voice_token(text)
            if selected is not None:
                try:
                    token, desc = selected
                    self._voice.Voice = token
                    self._set_state(voice_name=str(desc or ""))
                except Exception:
                    self._logger.exception("Failed to set selected SAPI voice")
            self._set_state(
                available=True,
                status="playing",
                source_key=source_key,
                text=text,
                message="",
            )
            self._play_started_at = time.monotonic()
            self._emit_state()
            try:
                stream_id = int(self._voice.Speak(text, self._SVS_FLAGS_ASYNC | self._SVS_FLAGS_PURGE) or 0)
            except Exception as exc:
                with self._lock:
                    self._active_stream_id = 0
                self._set_state(status="stopped", source_key="", text="", message=f"TTS play failed: {exc}")
                self._emit_state()
                return
            with self._lock:
                self._active_stream_id = stream_id
            return

        if command == "stop":
            with self._lock:
                self._active_stream_id = -1
            self._safe_stop()
            with self._lock:
                self._active_stream_id = 0
            self._set_state(status="stopped", source_key="", text="")
            self._emit_state()

    def _on_end_event(self, stream_number: int) -> None:
        if time.monotonic() - self._play_started_at < 0.2:
            return
        with self._lock:
            if self._active_stream_id and int(stream_number or 0) != int(self._active_stream_id):
                return
            self._active_stream_id = 0
        self._set_state(status="stopped", source_key="", text="")
        self._emit_state()

    def _poll_playback_done(self) -> None:
        with self._lock:
            status = str(self._state.status or "stopped")
            voice = self._voice
            ready = self._ready
        if status != "playing" or not ready or voice is None:
            return
        if time.monotonic() - self._play_started_at < 0.35:
            return
        try:
            running_state = int(getattr(voice.Status, "RunningState", 0) or 0)
        except Exception:
            return
        # SAPI SpeechRunState: 1 = Done, 2 = IsSpeaking.
        # Some environments may briefly report 0 while still speaking, so only
        # transition to stopped when state is explicitly "Done".
        if running_state != 1:
            return
        with self._lock:
            self._active_stream_id = 0
        self._set_state(status="stopped", source_key="", text="")
        self._emit_state()
