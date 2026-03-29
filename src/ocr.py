from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OCRToken:
    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def height(self) -> float:
        return max(1.0, self.bottom - self.top)


@dataclass
class OCRLine:
    tokens: list[OCRToken] = field(default_factory=list)
    center_y: float = 0.0
    avg_height: float = 0.0

    def append(self, token: OCRToken) -> None:
        self.tokens.append(token)
        count = len(self.tokens)
        if count == 1:
            self.center_y = token.center_y
            self.avg_height = token.height
            return
        self.center_y = ((self.center_y * (count - 1)) + token.center_y) / count
        self.avg_height = ((self.avg_height * (count - 1)) + token.height) / count


class ScreenshotOCRService:
    def __init__(self) -> None:
        self._engine = None
        self._init_error = ""
        self._engine_attempted = False

    def runtime_hint(self) -> str:
        if self._init_error:
            return (
                "截图 OCR 依赖未就绪。"
                "请使用同一解释器执行 `\"<python>\" -m pip install -r requirements.txt`。"
                f"底层异常: {self._init_error}"
            )
        return "截图 OCR 依赖未就绪，请执行 `pip install -r requirements.txt`。"

    def extract_text(self, image) -> str:
        engine = self._get_engine()

        try:
            import numpy as np
        except Exception as exc:
            raise RuntimeError("OCR 运行缺少 numpy，请重新安装 requirements。") from exc

        try:
            rgb_image = image.convert("RGB")
            bgr_image = np.ascontiguousarray(np.array(rgb_image)[:, :, ::-1])
            raw_result = engine(bgr_image)
        except Exception as exc:
            raise RuntimeError(f"OCR 识别失败: {exc}") from exc

        tokens = self._normalize_output(raw_result)
        if not tokens:
            return ""

        return self._join_tokens(tokens).strip()

    def _get_engine(self):
        if self._engine is not None:
            return self._engine

        if self._engine_attempted:
            raise RuntimeError(self.runtime_hint())

        self._engine_attempted = True

        try:
            from rapidocr import RapidOCR  # type: ignore
        except Exception as exc:
            self._init_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(
                "未安装 RapidOCR 依赖，请先执行 `pip install -r requirements.txt`。"
            ) from exc

        try:
            self._engine = RapidOCR()
        except Exception as exc:
            self._init_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"初始化 OCR 引擎失败: {exc}") from exc

        return self._engine

    def _normalize_output(self, raw_result: Any) -> list[OCRToken]:
        if raw_result is None:
            return []

        if hasattr(raw_result, "boxes") and hasattr(raw_result, "txts"):
            return self._normalize_box_triplets(
                getattr(raw_result, "boxes", None),
                getattr(raw_result, "txts", None),
                getattr(raw_result, "scores", None),
            )

        if isinstance(raw_result, tuple):
            if raw_result and isinstance(raw_result[0], list):
                return self._normalize_legacy_items(raw_result[0])
            if len(raw_result) >= 3:
                return self._normalize_box_triplets(raw_result[0], raw_result[1], raw_result[2])

        if isinstance(raw_result, list):
            return self._normalize_legacy_items(raw_result)

        return []

    def _normalize_box_triplets(self, boxes: Any, txts: Any, scores: Any) -> list[OCRToken]:
        if boxes is None or txts is None:
            return []

        try:
            box_items = list(boxes)
            text_items = list(txts)
            score_items = list(scores) if scores is not None else []
        except Exception:
            return []

        tokens: list[OCRToken] = []
        for index, text in enumerate(text_items):
            token = self._build_token(
                box_items[index] if index < len(box_items) else None,
                text,
                score_items[index] if index < len(score_items) else 0.0,
            )
            if token is not None:
                tokens.append(token)
        return tokens

    def _normalize_legacy_items(self, items: list[Any]) -> list[OCRToken]:
        tokens: list[OCRToken] = []
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            box = item[0]
            text = item[1]
            score = item[2] if len(item) >= 3 else 0.0
            token = self._build_token(box, text, score)
            if token is not None:
                tokens.append(token)
        return tokens

    def _build_token(self, box: Any, text: Any, score: Any) -> OCRToken | None:
        content = str(text or "").strip()
        if not content:
            return None

        left, top, right, bottom = self._extract_box_bounds(box)
        token_score = self._coerce_float(score)
        return OCRToken(
            text=content,
            score=token_score,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )

    @staticmethod
    def _extract_box_bounds(box: Any) -> tuple[float, float, float, float]:
        try:
            points = list(box)
        except Exception:
            return 0.0, 0.0, 0.0, 0.0

        xs: list[float] = []
        ys: list[float] = []
        for point in points:
            try:
                x = float(point[0])
                y = float(point[1])
            except Exception:
                continue
            xs.append(x)
            ys.append(y)

        if not xs or not ys:
            return 0.0, 0.0, 0.0, 0.0

        return min(xs), min(ys), max(xs), max(ys)

    @staticmethod
    def _coerce_float(value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def _join_tokens(self, tokens: list[OCRToken]) -> str:
        ordered = sorted(tokens, key=lambda item: (item.center_y, item.left))
        lines: list[OCRLine] = []

        for token in ordered:
            if not lines:
                line = OCRLine()
                line.append(token)
                lines.append(line)
                continue

            current = lines[-1]
            threshold = max(10.0, min(28.0, max(current.avg_height, token.height) * 0.65))
            if abs(token.center_y - current.center_y) <= threshold:
                current.append(token)
            else:
                line = OCRLine()
                line.append(token)
                lines.append(line)

        rendered_lines: list[str] = []
        for line in lines:
            line.tokens.sort(key=lambda item: item.left)
            rendered = self._render_line(line.tokens).strip()
            if rendered:
                rendered_lines.append(rendered)

        return "\n".join(rendered_lines)

    def _render_line(self, tokens: list[OCRToken]) -> str:
        parts: list[str] = []
        previous = ""

        for token in tokens:
            current = token.text.strip()
            if not current:
                continue
            if previous and self._needs_space(previous, current):
                parts.append(" ")
            parts.append(current)
            previous = current

        return "".join(parts)

    def _needs_space(self, previous: str, current: str) -> bool:
        prev_tail = previous[-1]
        curr_head = current[0]

        if self._is_cjk(prev_tail) or self._is_cjk(curr_head):
            return False

        if prev_tail.isalnum() and curr_head.isalnum():
            return True

        if prev_tail in "([{/$#@":
            return False
        if curr_head in ")]},.;:!?%":
            return False
        if prev_tail in "-_/" or curr_head in "-_/":
            return False

        return True

    @staticmethod
    def _is_cjk(char: str) -> bool:
        code = ord(char)
        return 0x4E00 <= code <= 0x9FFF
