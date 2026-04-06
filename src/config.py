from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class OpenAIConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout_sec: int = 60
    multi_candidate_count: int = 3
    multi_candidate_short_cn_max_chars: int = 24
    multi_candidate_short_en_max_words: int = 12


@dataclass
class DictionaryConfig:
    preferred_direction: str = "auto"


@dataclass
class InteractionConfig:
    selection_enabled: bool = True
    selection_trigger_mode: str = "icon"  # icon | double_ctrl
    selection_icon_trigger: str = "click"  # click | hover
    screenshot_enabled: bool = True
    screenshot_hotkey: str = "Ctrl+Alt+S"
    selection_icon_delay_ms: int = 1500


@dataclass
class OCRConfig:
    windows_lang: str = "auto"
    timeout_sec: int = 6


@dataclass
class HistoryConfig:
    retention_days: int = 30  # 7 | 30 | 90


@dataclass
class AppConfig:
    translation_mode: str = "dictionary"
    theme_mode: str = "system"
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    dictionary: DictionaryConfig = field(default_factory=DictionaryConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    interaction: InteractionConfig = field(default_factory=InteractionConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)


class ConfigStore:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_translation_mode(value: str | None) -> str:
        mode = str(value or "dictionary").strip().lower()
        return mode if mode in {"dictionary", "ai"} else "dictionary"

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            cfg = AppConfig()
            self.save(cfg)
            return cfg

        with self.config_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        openai_raw = raw.get("openai", {})
        dictionary_raw = raw.get("dictionary", {})
        ocr_raw = raw.get("ocr", {})
        interaction_raw = raw.get("interaction", {})
        history_raw = raw.get("history", {})

        # Backward compatibility with previous keys.
        legacy_selection_enabled = interaction_raw.get("selection_mouse_enabled", True)
        legacy_screenshot_enabled = interaction_raw.get("screenshot_hotkey_enabled", True)

        cfg = AppConfig(
            translation_mode=self._normalize_translation_mode(raw.get("translation_mode", "dictionary")),
            theme_mode=str(raw.get("theme_mode", "system") or "system"),
            openai=OpenAIConfig(
                base_url=openai_raw.get("base_url", "https://api.openai.com/v1"),
                api_key=openai_raw.get("api_key", ""),
                model=openai_raw.get("model", "gpt-4o-mini"),
                timeout_sec=int(openai_raw.get("timeout_sec", 60)),
                multi_candidate_count=int(openai_raw.get("multi_candidate_count", 3) or 3),
                multi_candidate_short_cn_max_chars=int(openai_raw.get("multi_candidate_short_cn_max_chars", 24) or 24),
                multi_candidate_short_en_max_words=int(openai_raw.get("multi_candidate_short_en_max_words", 12) or 12),
            ),
            dictionary=DictionaryConfig(
                preferred_direction=dictionary_raw.get("preferred_direction", "auto"),
            ),
            ocr=OCRConfig(
                windows_lang=str(ocr_raw.get("windows_lang", "auto") or "auto").strip().lower(),
                timeout_sec=int(ocr_raw.get("timeout_sec", 6) or 6),
            ),
            interaction=InteractionConfig(
                selection_enabled=bool(interaction_raw.get("selection_enabled", legacy_selection_enabled)),
                selection_trigger_mode=str(interaction_raw.get("selection_trigger_mode", "icon") or "icon"),
                selection_icon_trigger=str(interaction_raw.get("selection_icon_trigger", "click") or "click"),
                screenshot_enabled=bool(interaction_raw.get("screenshot_enabled", legacy_screenshot_enabled)),
                screenshot_hotkey=str(
                    interaction_raw.get(
                        "screenshot_hotkey",
                        "Ctrl+Alt+S",
                    )
                    or ""
                ).strip(),
                selection_icon_delay_ms=int(interaction_raw.get("selection_icon_delay_ms", interaction_raw.get("hover_delay_ms", 1500))),
            ),
            history=HistoryConfig(
                retention_days=int(history_raw.get("retention_days", 30) or 30),
            ),
        )

        cfg.translation_mode = self._normalize_translation_mode(cfg.translation_mode)
        if cfg.theme_mode not in {"system", "light", "dark"}:
            cfg.theme_mode = "system"

        if not cfg.dictionary.preferred_direction:
            cfg.dictionary.preferred_direction = "auto"

        if cfg.interaction.selection_trigger_mode not in {"icon", "double_ctrl"}:
            cfg.interaction.selection_trigger_mode = "icon"
        if cfg.interaction.selection_icon_trigger not in {"click", "hover"}:
            cfg.interaction.selection_icon_trigger = "click"
        cfg.interaction.screenshot_hotkey = str(cfg.interaction.screenshot_hotkey or "").strip()
        cfg.interaction.selection_icon_delay_ms = max(0, min(5000, int(cfg.interaction.selection_icon_delay_ms or 1500)))
        if cfg.history.retention_days not in {7, 30, 90}:
            cfg.history.retention_days = 30
        cfg.openai.multi_candidate_count = max(2, min(3, int(cfg.openai.multi_candidate_count or 3)))
        cfg.openai.multi_candidate_short_cn_max_chars = max(8, min(60, int(cfg.openai.multi_candidate_short_cn_max_chars or 24)))
        cfg.openai.multi_candidate_short_en_max_words = max(4, min(30, int(cfg.openai.multi_candidate_short_en_max_words or 12)))
        cfg.ocr.windows_lang = str(cfg.ocr.windows_lang or "auto").strip().lower() or "auto"
        cfg.ocr.timeout_sec = max(2, min(20, int(cfg.ocr.timeout_sec or 6)))

        return cfg

    def save(self, config: AppConfig) -> None:
        config.translation_mode = self._normalize_translation_mode(config.translation_mode)
        with self.config_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(config), f, ensure_ascii=False, indent=2)
