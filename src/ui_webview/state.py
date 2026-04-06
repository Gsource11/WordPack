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
    down_at: float = 0.0
    up_at: float = 0.0
    moved_px: int = 0
    executable: str = ""
    trigger_mode: str = "icon"
    icon_trigger: str = "click"
    fingerprint: str = ""
    verify_reason: str = ""
    verified_has_text: bool | None = None
    verified_at: float = 0.0

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


@dataclass
class SelectionFlowState:
    phase: str = "idle"  # idle | captured | armed | verified | icon_shown | triggered | cancelled
    token: int = 0
    updated_at: float = 0.0
    candidate_fingerprint: str = ""
    reason: str = ""
