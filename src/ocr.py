from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from .app_logging import APP_RUNTIME_DIR


class WindowsOCRBackend:
    def __init__(self) -> None:
        self._last_error = ""
        self._script_path = self._resolve_script_path()

    def runtime_hint(self) -> str:
        if self._last_error:
            return f"Windows OCR unavailable: {self._last_error}"
        return "Windows OCR ready"

    def extract_text(self, image, *, lang: str = "auto", timeout_sec: int = 6) -> str:
        temp_dir = APP_RUNTIME_DIR / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"ocr_{uuid.uuid4().hex}.png"
        try:
            image.convert("RGB").save(temp_path, format="PNG")
            payload = self._run_script(temp_path, lang=lang, timeout_sec=timeout_sec)
            return str(payload.get("text", "") or "").strip()
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _run_script(self, image_path: Path, *, lang: str, timeout_sec: int) -> dict[str, Any]:
        if not self._script_path.exists():
            self._last_error = f"script missing: {self._script_path}"
            raise RuntimeError("windows-script-missing")

        cmd = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self._script_path),
            "-ImagePath",
            str(image_path),
            "-Lang",
            str(lang or "auto"),
            "-TimeoutSec",
            str(max(2, min(20, int(timeout_sec or 6)))),
        ]

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=max(3, int(timeout_sec or 6) + 2),
                creationflags=0x08000000,
                check=False,
            )
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError("windows-call-failed") from exc

        lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
        if not lines:
            stderr = str(completed.stderr or "").strip()
            self._last_error = stderr or f"exit={completed.returncode}"
            raise RuntimeError("windows-empty-output")

        try:
            payload = json.loads(lines[-1])
        except Exception as exc:
            self._last_error = f"invalid-json: {exc}"
            raise RuntimeError("windows-invalid-json") from exc

        if not isinstance(payload, dict):
            self._last_error = "payload-not-object"
            raise RuntimeError("windows-invalid-payload")

        if not bool(payload.get("ok", False)):
            error_text = str(payload.get("error", "") or "").strip() or "windows-ocr-failed"
            self._last_error = error_text
            raise RuntimeError(error_text)

        self._last_error = ""
        return payload

    @staticmethod
    def _resolve_script_path() -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(getattr(sys, "_MEIPASS")).joinpath("src", "windows_ocr.ps1")
        return Path(__file__).resolve().with_name("windows_ocr.ps1")


class ScreenshotOCRService:
    def __init__(self, cfg_getter: Callable[[], Any] | None = None) -> None:
        self.cfg_getter = cfg_getter or (lambda: None)
        self.windows = WindowsOCRBackend()

    def runtime_hint(self) -> str:
        return self.windows.runtime_hint()

    @staticmethod
    def _enhanced_variants(image):
        try:
            from PIL import Image, ImageFilter, ImageOps  # type: ignore
        except Exception:
            return [image]

        base = image.convert("RGB")
        variants = [base]
        resampling = getattr(Image, "Resampling", Image)
        lanczos = getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1))

        for scale in (2, 3):
            try:
                enlarged = base.resize((max(1, base.width * scale), max(1, base.height * scale)), lanczos)
                gray = ImageOps.grayscale(enlarged)
                auto = ImageOps.autocontrast(gray, cutoff=1)
                sharp = auto.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN).convert("RGB")
                binary = auto.point(lambda v: 255 if int(v) >= 180 else 0, mode="1").convert("RGB")
                variants.extend([sharp, binary])
            except Exception:
                continue
        return variants

    def extract_text(self, image) -> str:
        cfg = self.cfg_getter() if callable(self.cfg_getter) else None
        ocr_cfg = getattr(cfg, "ocr", None)
        lang = str(getattr(ocr_cfg, "windows_lang", "auto") or "auto").strip().lower() or "auto"
        timeout_sec = max(2, min(20, int(getattr(ocr_cfg, "timeout_sec", 6) or 6)))

        seen: set[str] = set()
        errors: list[str] = []
        for variant in self._enhanced_variants(image):
            try:
                text = self.windows.extract_text(variant, lang=lang, timeout_sec=timeout_sec).strip()
            except Exception as exc:
                error_text = str(exc or "").strip()
                if error_text:
                    errors.append(error_text)
                continue
            normalized = text.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                return normalized

        lowered = "\n".join(errors).lower()
        if "language package not installed" in lowered:
            raise RuntimeError("Language package not installed")
        if "class not registered" in lowered or "没有注册类" in lowered:
            raise RuntimeError("class not registered")
        raise RuntimeError("windows-empty")
