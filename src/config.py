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
class SelectionAppProfile:
    executable: str = ""
    mode: str = "inherit"  # inherit | icon | double_ctrl | double_alt | double_shift | disabled
    icon_trigger: str = "inherit"  # inherit | click | hover


@dataclass
class InteractionConfig:
    startup_launch_enabled: bool = False
    selection_enabled: bool = True
    selection_trigger_mode: str = "double_ctrl"  # icon | double_ctrl | double_alt | double_shift
    selection_icon_trigger: str = "click"  # click | hover
    screenshot_enabled: bool = True
    screenshot_hotkey: str = "Ctrl+Alt+S"
    bubble_restore_hotkey: str = "Ctrl+Shift+Z"
    main_toggle_hotkey: str = "Ctrl+Alt+W"
    bubble_close_on_fast_mouse_leave: bool = False
    bubble_fast_close_profile: str = "off"  # off | loose | standard | aggressive
    bubble_close_on_click_outside: bool = False
    selection_icon_delay_ms: int = 1500
    selection_drag_min_px: int = 9
    selection_click_pair_max_distance_px: int = 14
    selection_hold_min_ms: int = 35
    selection_icon_arm_delay_ms: int = 150
    selection_verify_timeout_ms: int = 80
    selection_hover_dwell_ms: int = 130
    selection_hover_max_speed_px_s: int = 900
    selection_candidate_dedupe_window_ms: int = 320
    selection_candidate_max_age_sec: float = 12.0
    app_profiles: list[SelectionAppProfile] = field(default_factory=list)


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

    @staticmethod
    def _default_selection_app_profiles() -> list[SelectionAppProfile]:
        # Keep empty by default: user global mode should remain authoritative unless
        # the user explicitly creates per-app overrides.
        return []

    @staticmethod
    def _normalize_bubble_fast_close_profile(
        profile: object,
        *,
        legacy_enabled: bool = False,
    ) -> str:
        value = str(profile or "").strip().lower()
        if value in {"off", "loose", "standard", "aggressive"}:
            return value
        return "standard" if bool(legacy_enabled) else "off"

    @staticmethod
    def _normalize_selection_profile_item(item: object) -> SelectionAppProfile | None:
        if not isinstance(item, dict):
            return None
        executable = str(item.get("executable", "") or "").strip().lower()
        mode = str(item.get("mode", "inherit") or "inherit").strip().lower()
        icon_trigger = str(item.get("icon_trigger", "inherit") or "inherit").strip().lower()
        if not executable:
            return None
        if mode not in {"inherit", "icon", "double_ctrl", "double_alt", "double_shift", "disabled"}:
            mode = "inherit"
        if icon_trigger not in {"inherit", "click", "hover"}:
            icon_trigger = "inherit"
        return SelectionAppProfile(executable=executable, mode=mode, icon_trigger=icon_trigger)

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
        legacy_startup_enabled = interaction_raw.get("launch_at_startup", False)
        legacy_selection_enabled = interaction_raw.get("selection_mouse_enabled", True)
        legacy_screenshot_enabled = interaction_raw.get("screenshot_hotkey_enabled", True)
        raw_profiles = interaction_raw.get("app_profiles")
        profile_items: list[SelectionAppProfile] = []
        if isinstance(raw_profiles, list):
            for item in raw_profiles:
                normalized = self._normalize_selection_profile_item(item)
                if normalized is not None:
                    profile_items.append(normalized)

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
                startup_launch_enabled=bool(interaction_raw.get("startup_launch_enabled", legacy_startup_enabled)),
                selection_enabled=bool(interaction_raw.get("selection_enabled", legacy_selection_enabled)),
                selection_trigger_mode=str(interaction_raw.get("selection_trigger_mode", "double_ctrl") or "double_ctrl"),
                selection_icon_trigger=str(interaction_raw.get("selection_icon_trigger", "click") or "click"),
                screenshot_enabled=bool(interaction_raw.get("screenshot_enabled", legacy_screenshot_enabled)),
                screenshot_hotkey=str(
                    interaction_raw.get(
                        "screenshot_hotkey",
                        "Ctrl+Alt+S",
                    )
                    or ""
                ).strip(),
                bubble_restore_hotkey=str(interaction_raw.get("bubble_restore_hotkey", "Ctrl+Shift+Z") or "").strip(),
                main_toggle_hotkey=str(interaction_raw.get("main_toggle_hotkey", "Ctrl+Alt+W") or "").strip(),
                bubble_close_on_fast_mouse_leave=bool(
                    interaction_raw.get("bubble_close_on_fast_mouse_leave", False)
                ),
                bubble_fast_close_profile=self._normalize_bubble_fast_close_profile(
                    interaction_raw.get("bubble_fast_close_profile", ""),
                    legacy_enabled=bool(interaction_raw.get("bubble_close_on_fast_mouse_leave", False)),
                ),
                bubble_close_on_click_outside=bool(interaction_raw.get("bubble_close_on_click_outside", False)),
                selection_icon_delay_ms=int(interaction_raw.get("selection_icon_delay_ms", interaction_raw.get("hover_delay_ms", 1500))),
                selection_drag_min_px=int(interaction_raw.get("selection_drag_min_px", 9) or 9),
                selection_click_pair_max_distance_px=int(interaction_raw.get("selection_click_pair_max_distance_px", 14) or 14),
                selection_hold_min_ms=int(interaction_raw.get("selection_hold_min_ms", 35) or 35),
                selection_icon_arm_delay_ms=int(interaction_raw.get("selection_icon_arm_delay_ms", 150) or 150),
                selection_verify_timeout_ms=int(interaction_raw.get("selection_verify_timeout_ms", 80) or 80),
                selection_hover_dwell_ms=int(interaction_raw.get("selection_hover_dwell_ms", 130) or 130),
                selection_hover_max_speed_px_s=int(interaction_raw.get("selection_hover_max_speed_px_s", 900) or 900),
                selection_candidate_dedupe_window_ms=int(interaction_raw.get("selection_candidate_dedupe_window_ms", 320) or 320),
                selection_candidate_max_age_sec=float(interaction_raw.get("selection_candidate_max_age_sec", 12.0) or 12.0),
                app_profiles=profile_items,
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

        if cfg.interaction.selection_trigger_mode not in {"icon", "double_ctrl", "double_alt", "double_shift"}:
            cfg.interaction.selection_trigger_mode = "double_ctrl"
        cfg.interaction.startup_launch_enabled = bool(cfg.interaction.startup_launch_enabled)
        cfg.interaction.bubble_fast_close_profile = self._normalize_bubble_fast_close_profile(
            getattr(cfg.interaction, "bubble_fast_close_profile", ""),
            legacy_enabled=bool(getattr(cfg.interaction, "bubble_close_on_fast_mouse_leave", False)),
        )
        cfg.interaction.bubble_close_on_fast_mouse_leave = bool(cfg.interaction.bubble_fast_close_profile != "off")
        cfg.interaction.bubble_close_on_click_outside = bool(cfg.interaction.bubble_close_on_click_outside)
        if cfg.interaction.selection_icon_trigger not in {"click", "hover"}:
            cfg.interaction.selection_icon_trigger = "click"
        cfg.interaction.screenshot_hotkey = str(cfg.interaction.screenshot_hotkey or "").strip()
        cfg.interaction.bubble_restore_hotkey = str(cfg.interaction.bubble_restore_hotkey or "").strip()
        cfg.interaction.main_toggle_hotkey = str(cfg.interaction.main_toggle_hotkey or "").strip()
        cfg.interaction.selection_icon_delay_ms = max(0, min(5000, int(cfg.interaction.selection_icon_delay_ms or 1500)))
        cfg.interaction.selection_drag_min_px = max(3, min(40, int(cfg.interaction.selection_drag_min_px or 9)))
        cfg.interaction.selection_click_pair_max_distance_px = max(4, min(40, int(cfg.interaction.selection_click_pair_max_distance_px or 14)))
        cfg.interaction.selection_hold_min_ms = max(0, min(300, int(cfg.interaction.selection_hold_min_ms or 35)))
        cfg.interaction.selection_icon_arm_delay_ms = max(0, min(800, int(cfg.interaction.selection_icon_arm_delay_ms or 150)))
        cfg.interaction.selection_verify_timeout_ms = max(20, min(300, int(cfg.interaction.selection_verify_timeout_ms or 80)))
        cfg.interaction.selection_hover_dwell_ms = max(0, min(500, int(cfg.interaction.selection_hover_dwell_ms or 130)))
        cfg.interaction.selection_hover_max_speed_px_s = max(120, min(5000, int(cfg.interaction.selection_hover_max_speed_px_s or 900)))
        cfg.interaction.selection_candidate_dedupe_window_ms = max(60, min(1500, int(cfg.interaction.selection_candidate_dedupe_window_ms or 320)))
        try:
            cfg.interaction.selection_candidate_max_age_sec = float(cfg.interaction.selection_candidate_max_age_sec or 12.0)
        except Exception:
            cfg.interaction.selection_candidate_max_age_sec = 12.0
        cfg.interaction.selection_candidate_max_age_sec = max(2.0, min(30.0, cfg.interaction.selection_candidate_max_age_sec))
        normalized_profiles: list[SelectionAppProfile] = []
        for item in cfg.interaction.app_profiles or []:
            if isinstance(item, SelectionAppProfile):
                normalized = self._normalize_selection_profile_item(asdict(item))
            else:
                normalized = self._normalize_selection_profile_item(item)
            if normalized is not None:
                normalized_profiles.append(normalized)
        cfg.interaction.app_profiles = normalized_profiles
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
