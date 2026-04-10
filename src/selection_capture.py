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
        self._uia_module = None
        self._uia_import_error = ""

    def warmup(self) -> bool:
        module = self._load_uiautomation()
        if module is None:
            return False
        try:
            focused_getter = getattr(module, "GetFocusedControl", None)
            if callable(focused_getter):
                focused_getter()
            return True
        except Exception as exc:
            self._uia_import_error = f"{type(exc).__name__}: {exc}"
            return False

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

    def probe_fast(self, payload: dict | None = None, *, timeout_ms: int = 80) -> SelectionCaptureResult:
        del timeout_ms
        # Fast path for icon pre-verification:
        # - in-process UIA only (no PowerShell fallback, no clipboard operations)
        # - returns quickly with best-effort reason
        result = self._capture_by_uia_inproc(payload)
        if result.reason in {"uia-module-missing", "uia-module-import-failed", "uia-internal-error"}:
            return SelectionCaptureResult(
                source="uia",
                reason=result.reason or "uia-fast-unavailable",
                detail=result.detail,
                strategy="fast-uia",
                control_type=result.control_type,
                class_name=result.class_name,
                framework_id=result.framework_id,
                stability=result.stability,
                is_password=result.is_password,
                uia_reason=result.uia_reason or result.reason,
                uia_detail=result.uia_detail or result.detail,
            )
        result.strategy = result.strategy or "fast-uia"
        return result

    def capture_by_uia(self, payload: dict | None = None) -> SelectionCaptureResult:
        inproc_result = self._capture_by_uia_inproc(payload)
        if inproc_result.reason not in {"uia-module-missing", "uia-module-import-failed", "uia-internal-error"}:
            return inproc_result

        # Compatibility fallback: old PowerShell probe path.
        # This remains only when in-process UIA runtime is unavailable.
        ps_result = self._capture_by_uia_powershell(payload)
        if ps_result.reason in {"uia-script-missing", "powershell-missing"}:
            return inproc_result
        return ps_result

    def _capture_by_uia_inproc(self, payload: dict | None = None) -> SelectionCaptureResult:
        module = self._load_uiautomation()
        if module is None:
            detail = self._uia_import_error or "uiautomation import failed"
            return SelectionCaptureResult(
                source="uia",
                reason="uia-module-missing",
                detail=detail,
                uia_reason="uia-module-missing",
                uia_detail=detail,
            )
        try:
            return self._probe_with_uia_module(module, payload)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            return SelectionCaptureResult(
                source="uia",
                reason="uia-internal-error",
                detail=self._truncate_detail(detail),
                uia_reason="uia-internal-error",
                uia_detail=self._truncate_detail(detail),
            )

    def _load_uiautomation(self):
        if self._uia_module is not None:
            return self._uia_module
        try:
            import uiautomation as auto  # type: ignore
        except Exception as exc:
            self._uia_import_error = f"{type(exc).__name__}: {exc}"
            return None
        self._uia_module = auto
        self._uia_import_error = ""
        return auto

    def _probe_with_uia_module(self, auto, payload: dict | None = None) -> SelectionCaptureResult:
        point = self._extract_probe_point(payload)
        candidates: list[tuple[object, str]] = []
        seen: set[str] = set()

        if point is not None:
            try:
                ctrl = auto.ControlFromPoint(int(point[0]), int(point[1]))
                if ctrl is not None:
                    candidates.append((ctrl, "point"))
            except Exception:
                pass

        try:
            focused = auto.GetFocusedControl()
            if focused is not None:
                candidates.append((focused, "focused"))
        except Exception:
            pass

        if not candidates:
            return SelectionCaptureResult(
                source="uia",
                reason="no-focused-element",
                strategy="none",
                uia_reason="no-focused-element",
            )

        best: SelectionCaptureResult | None = None
        for candidate, strategy in candidates:
            current = candidate
            depth = 0
            while current is not None and depth <= 5:
                runtime_key = self._runtime_key(current)
                if runtime_key and runtime_key in seen:
                    break
                if runtime_key:
                    seen.add(runtime_key)

                current_strategy = strategy if depth == 0 else f"{strategy}-parent-{depth}"
                probe = self._try_get_selected_text(current, current_strategy)
                if probe.has_text():
                    probe.reason = "ok"
                    probe.uia_reason = "ok"
                    return probe
                if best is None:
                    best = probe
                elif best.stability != "stable" and probe.stability == "stable":
                    best = probe
                elif best.reason == "no-textpattern" and probe.reason != "no-textpattern":
                    best = probe

                depth += 1
                current = self._safe_get_parent(current)

        if best is not None:
            return best
        return SelectionCaptureResult(
            source="uia",
            reason="no-uia-candidate",
            strategy="none",
            uia_reason="no-uia-candidate",
        )

    def _runtime_key(self, control: object) -> str:
        runtime_id = self._safe_get_attr(control, "RuntimeId")
        if isinstance(runtime_id, (list, tuple)):
            try:
                return "-".join(str(int(item)) for item in runtime_id)
            except Exception:
                return "-".join(str(item) for item in runtime_id)
        return ""

    def _try_get_selected_text(self, control: object, strategy: str) -> SelectionCaptureResult:
        control_type = self._normalize_control_type(self._safe_get_attr(control, "ControlTypeName"))
        class_name = str(self._safe_get_attr(control, "ClassName") or "")
        framework_id = str(self._safe_get_attr(control, "FrameworkId") or "")
        is_password = bool(self._safe_get_attr(control, "IsPassword", False))
        stability = self._get_stability(control_type, class_name)

        result = SelectionCaptureResult(
            source="uia",
            reason="no-textpattern",
            strategy=strategy,
            control_type=control_type,
            class_name=class_name,
            framework_id=framework_id,
            stability=stability,
            is_password=is_password,
            uia_reason="no-textpattern",
        )
        if is_password:
            result.reason = "password-field"
            result.uia_reason = "password-field"
            return result

        text_pattern = self._safe_call(control, "GetTextPattern")
        if text_pattern is None:
            return result

        selections = self._safe_call(text_pattern, "GetSelection")
        if selections is None:
            result.reason = "empty-selection-array"
            result.uia_reason = "empty-selection-array"
            return result

        texts: list[str] = []
        try:
            for item in selections:
                text = str(self._safe_call(item, "GetText", -1) or "").strip()
                if text:
                    texts.append(text)
        except Exception:
            result.reason = "empty-selection"
            result.uia_reason = "empty-selection"
            return result

        merged = "\n".join(texts).strip()
        if not merged:
            result.reason = "empty-selection"
            result.uia_reason = "empty-selection"
            return result

        result.text = merged
        result.reason = "ok"
        result.uia_reason = "ok"
        return result

    @staticmethod
    def _safe_get_attr(obj: object, name: str, default=None):
        try:
            return getattr(obj, name)
        except Exception:
            return default

    @staticmethod
    def _safe_call(obj: object, method_name: str, *args):
        try:
            method = getattr(obj, method_name, None)
            if method is None:
                return None
            return method(*args)
        except Exception:
            return None

    @staticmethod
    def _safe_get_parent(control: object):
        try:
            method = getattr(control, "GetParentControl", None)
            if method is None:
                return None
            return method()
        except Exception:
            return None

    @staticmethod
    def _normalize_control_type(value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw.startswith("ControlType."):
            return raw
        return f"ControlType.{raw}"

    @staticmethod
    def _get_stability(control_type: str, class_name: str) -> str:
        problematic = {
            "Chrome_RenderWidgetHostHWND",
            "Chrome_WidgetWin_1",
            "MozillaWindowClass",
            "ConsoleWindowClass",
        }
        if control_type in {"ControlType.Edit", "ControlType.Document"}:
            if class_name in problematic:
                return "conditional"
            return "stable"
        if control_type in {"ControlType.Text", "ControlType.Pane", "ControlType.Custom"}:
            return "conditional"
        return "unknown"

    def _capture_by_uia_powershell(self, payload: dict | None = None) -> SelectionCaptureResult:
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
            "uia-module-missing",
            "uia-module-import-failed",
            "uia-internal-error",
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
