from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class UiState:
    status: str
    translation_mode: str
    theme_mode: str
    direction: str
    history: list[dict[str, str]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BubbleState:
    visible: bool = False
    pinned: bool = False
    pending: bool = False
    action: str = "划词翻译"
    mode: str = "dictionary"
    source_text: str = ""
    result_text: str = ""
    candidate_pending: bool = False
    candidate_items: list[str] = field(default_factory=list)
    x: int | None = None
    y: int | None = None
    width: int = 408
    height: int = 272

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SelectionCandidate:
    captured_at: float = 0.0
    payload: dict[str, int] | None = None
    text: str = ""

    def is_fresh(self, max_age_sec: float = 12.0) -> bool:
        import time

        return bool(self.payload) and self.captured_at > 0 and (time.time() - self.captured_at) <= max_age_sec


@dataclass
class ScreenshotSession:
    session_id: int
    bounds: dict[str, int]
    show_bubble: bool
    main_was_hidden: bool
    started_at: float = 0.0
