from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


CREATE_NO_WINDOW = 0x08000000


@dataclass
class ClipboardCaptureResult:
    text: str = ""
    reason: str = ""
    detail: str = ""
    attempts: int = 0
    restore_ok: bool = True


@dataclass
class SelectionCaptureResult:
    text: str = ""
    source: str = "none"
    reason: str = ""
    detail: str = ""
    strategy: str = ""
    control_type: str = ""
    class_name: str = ""
    framework_id: str = ""
    stability: str = "unknown"
    is_password: bool = False
    used_clipboard_fallback: bool = False
    fallback_reason: str = ""
    fallback_detail: str = ""
    uia_reason: str = ""
    uia_detail: str = ""
    clipboard_reason: str = ""
    clipboard_detail: str = ""

    def has_text(self) -> bool:
        return bool(self.text.strip())

    def control_summary(self) -> str:
        parts = [part for part in (self.control_type, self.framework_id, self.class_name) if part]
        return " / ".join(parts) if parts else "unknown-control"

    def diagnostics_summary(self) -> str:
        parts = [
            f"final_source={self.source or 'none'}",
            f"final_reason={self.reason or 'n/a'}",
            f"strategy={self.strategy or 'n/a'}",
            f"control={self.control_summary()}",
        ]
        if self.uia_reason:
            parts.append(f"uia_reason={self.uia_reason}")
        if self.uia_detail:
            parts.append(f"uia_detail={self.uia_detail}")
        if self.clipboard_reason:
            parts.append(f"clipboard_reason={self.clipboard_reason}")
        if self.clipboard_detail:
            parts.append(f"clipboard_detail={self.clipboard_detail}")
        return " | ".join(parts)


class SelectionCaptureService:
    def __init__(self) -> None:
        self.script_path = self._resolve_script_path()

    def capture(
        self,
        clipboard_capture: Callable[..., ClipboardCaptureResult],
        payload: dict | None = None,
        *,
        wait_sec: float = 0.1,
        allow_unchanged: bool = False,
    ) -> SelectionCaptureResult:
        uia_result = self.capture_by_uia(payload)
        if uia_result.has_text():
            return uia_result

        if not self.should_fallback_to_clipboard(uia_result):
            return uia_result

        clipboard_result = clipboard_capture(wait_sec=wait_sec, allow_unchanged=allow_unchanged)
        text = clipboard_result.text.strip()
        if text:
            return SelectionCaptureResult(
                text=text,
                source="clipboard",
                reason="ok",
                detail=clipboard_result.detail,
                strategy="ctrl_c_fallback",
                control_type=uia_result.control_type,
                class_name=uia_result.class_name,
                framework_id=uia_result.framework_id,
                stability=uia_result.stability or "fallback",
                is_password=uia_result.is_password,
                used_clipboard_fallback=True,
                fallback_reason=uia_result.reason or "uia-empty",
                fallback_detail=uia_result.detail,
                uia_reason=uia_result.reason or "uia-empty",
                uia_detail=uia_result.detail,
                clipboard_reason=clipboard_result.reason or "clipboard-ok",
                clipboard_detail=clipboard_result.detail,
            )

        return SelectionCaptureResult(
            text="",
            source="none",
            reason=clipboard_result.reason or "clipboard-empty",
            detail=clipboard_result.detail,
            strategy="ctrl_c_fallback",
            control_type=uia_result.control_type,
            class_name=uia_result.class_name,
            framework_id=uia_result.framework_id,
            stability=uia_result.stability,
            is_password=uia_result.is_password,
            used_clipboard_fallback=True,
            fallback_reason=uia_result.reason or "uia-empty",
            fallback_detail=uia_result.detail,
            uia_reason=uia_result.reason or "uia-empty",
            uia_detail=uia_result.detail,
            clipboard_reason=clipboard_result.reason,
            clipboard_detail=clipboard_result.detail,
        )

    def capture_by_uia(self, payload: dict | None = None) -> SelectionCaptureResult:
        if not self.script_path.exists():
            detail = f"script={self.script_path}"
            return SelectionCaptureResult(
                source="uia",
                reason="uia-script-missing",
                detail=detail,
                uia_reason="uia-script-missing",
                uia_detail=detail,
            )

        cmd = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
        ]

        point = self._extract_probe_point(payload)
        if point is not None:
            cmd.extend(["-PointX", str(point[0]), "-PointY", str(point[1])])

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=0.65,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
        except FileNotFoundError:
            return SelectionCaptureResult(
                source="uia",
                reason="powershell-missing",
                detail="powershell.exe not found",
                uia_reason="powershell-missing",
                uia_detail="powershell.exe not found",
            )
        except subprocess.TimeoutExpired:
            return SelectionCaptureResult(
                source="uia",
                reason="uia-timeout",
                detail="timeout=0.65s",
                uia_reason="uia-timeout",
                uia_detail="timeout=0.65s",
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return SelectionCaptureResult(
                source="uia",
                reason="uia-launch-failed",
                detail=detail,
                uia_reason="uia-launch-failed",
                uia_detail=detail,
            )

        if completed.returncode != 0:
            detail = self._truncate_detail(
                f"exit={completed.returncode} stderr={(completed.stderr or '').strip()} stdout={(completed.stdout or '').strip()}"
            )
            return SelectionCaptureResult(
                source="uia",
                reason="uia-process-error",
                detail=detail,
                uia_reason="uia-process-error",
                uia_detail=detail,
            )

        payload_obj = self._parse_json_output(completed.stdout)
        if payload_obj is None:
            detail = self._truncate_detail((completed.stdout or "").strip())
            return SelectionCaptureResult(
                source="uia",
                reason="uia-invalid-json",
                detail=detail or "stdout-empty",
                uia_reason="uia-invalid-json",
                uia_detail=detail or "stdout-empty",
            )

        result = SelectionCaptureResult(
            text=str(payload_obj.get("text", "") or "").strip(),
            source=str(payload_obj.get("source", "uia") or "uia"),
            reason=str(payload_obj.get("reason", "") or ""),
            detail=str(payload_obj.get("detail", "") or ""),
            strategy=str(payload_obj.get("strategy", "") or ""),
            control_type=str(payload_obj.get("controlType", "") or ""),
            class_name=str(payload_obj.get("className", "") or ""),
            framework_id=str(payload_obj.get("frameworkId", "") or ""),
            stability=str(payload_obj.get("stability", "") or "unknown"),
            is_password=bool(payload_obj.get("isPassword", False)),
            uia_reason=str(payload_obj.get("reason", "") or ""),
            uia_detail=str(payload_obj.get("detail", "") or ""),
        )
        if result.has_text():
            result.reason = "ok"
            result.uia_reason = "ok"
        return result

    @staticmethod
    def should_fallback_to_clipboard(result: SelectionCaptureResult) -> bool:
        if result.is_password:
            return False
        return result.reason in {
            "",
            "no-focused-element",
            "no-uia-candidate",
            "uia-script-missing",
            "powershell-missing",
            "uia-timeout",
            "uia-launch-failed",
            "uia-process-error",
            "uia-invalid-json",
            "no-textpattern",
            "empty-selection",
            "empty-selection-array",
        }

    @staticmethod
    def _parse_json_output(stdout: str) -> dict | None:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return None
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_probe_point(payload: dict | None) -> tuple[int, int] | None:
        if not isinstance(payload, dict):
            return None

        for x_key, y_key in (("x", "y"), ("down_x", "down_y")):
            try:
                x = int(payload.get(x_key, 0))
                y = int(payload.get(y_key, 0))
            except (TypeError, ValueError):
                continue
            if x != 0 or y != 0:
                return x, y
        return None

    @staticmethod
    def _truncate_detail(value: str, limit: int = 240) -> str:
        text = value.strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    @staticmethod
    def _resolve_script_path() -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(getattr(sys, "_MEIPASS")).joinpath("src", "uia_capture.ps1")
        return Path(__file__).resolve().with_name("uia_capture.ps1")
