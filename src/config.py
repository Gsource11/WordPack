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


@dataclass
class OfflineConfig:
    preferred_direction: str = "auto"


@dataclass
class InteractionConfig:
    selection_enabled: bool = True
    selection_trigger_mode: str = "icon"  # icon | double_ctrl
    selection_icon_trigger: str = "click"  # click | hover
    selection_icon_cancel_sensitivity: str = "medium"  # low | medium | high
    selection_hotkey_enabled: bool = True
    screenshot_hotkey_enabled: bool = True
    selection_icon_delay_ms: int = 1500


@dataclass
class AppConfig:
    translation_mode: str = "offline"
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    offline: OfflineConfig = field(default_factory=OfflineConfig)
    interaction: InteractionConfig = field(default_factory=InteractionConfig)


class ConfigStore:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            cfg = AppConfig()
            self.save(cfg)
            return cfg

        with self.config_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        openai_raw = raw.get("openai", {})
        offline_raw = raw.get("offline", {})
        interaction_raw = raw.get("interaction", {})

        # Backward compatibility with previous keys.
        legacy_selection_enabled = interaction_raw.get("selection_mouse_enabled", True)

        cfg = AppConfig(
            translation_mode=raw.get("translation_mode", "offline"),
            openai=OpenAIConfig(
                base_url=openai_raw.get("base_url", "https://api.openai.com/v1"),
                api_key=openai_raw.get("api_key", ""),
                model=openai_raw.get("model", "gpt-4o-mini"),
                timeout_sec=int(openai_raw.get("timeout_sec", 60)),
            ),
            offline=OfflineConfig(
                preferred_direction=offline_raw.get("preferred_direction", "auto"),
            ),
            interaction=InteractionConfig(
                selection_enabled=bool(interaction_raw.get("selection_enabled", legacy_selection_enabled)),
                selection_trigger_mode=str(interaction_raw.get("selection_trigger_mode", "icon") or "icon"),
                selection_icon_trigger=str(interaction_raw.get("selection_icon_trigger", "click") or "click"),
                selection_icon_cancel_sensitivity=str(interaction_raw.get("selection_icon_cancel_sensitivity", "medium") or "medium"),
                selection_hotkey_enabled=bool(interaction_raw.get("selection_hotkey_enabled", True)),
                screenshot_hotkey_enabled=bool(interaction_raw.get("screenshot_hotkey_enabled", True)),
                selection_icon_delay_ms=int(interaction_raw.get("selection_icon_delay_ms", interaction_raw.get("hover_delay_ms", 1500))),
            ),
        )

        if cfg.translation_mode not in {"offline", "ai"}:
            cfg.translation_mode = "offline"

        if not cfg.offline.preferred_direction:
            cfg.offline.preferred_direction = "auto"

        if cfg.interaction.selection_trigger_mode not in {"icon", "double_ctrl"}:
            cfg.interaction.selection_trigger_mode = "icon"
        if cfg.interaction.selection_icon_trigger not in {"click", "hover"}:
            cfg.interaction.selection_icon_trigger = "click"
        if cfg.interaction.selection_icon_cancel_sensitivity not in {"low", "medium", "high"}:
            cfg.interaction.selection_icon_cancel_sensitivity = "medium"
        cfg.interaction.selection_icon_delay_ms = max(300, min(5000, int(cfg.interaction.selection_icon_delay_ms or 1500)))

        return cfg

    def save(self, config: AppConfig) -> None:
        with self.config_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(config), f, ensure_ascii=False, indent=2)
