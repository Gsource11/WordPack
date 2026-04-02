from __future__ import annotations

from typing import Any


class WindowApi:
    def __init__(self, controller, kind: str) -> None:
        self._controller = controller
        self.kind = kind
        self._window = None

    def attach_window(self, window) -> None:
        self._window = window

    def bootstrap(self) -> dict[str, Any]:
        return self._controller.bootstrap_window(self.kind)

    def mark_ready(self) -> dict[str, bool]:
        self._controller.mark_window_ready(self.kind)
        return {"ok": True}

    def notify_window_interaction(self) -> dict[str, bool]:
        self._controller.note_window_interaction(self.kind)
        return {"ok": True}

    def set_main_compact(self, compact: bool, height: int | None = None) -> dict[str, Any]:
        return self._controller.set_main_compact(bool(compact), height)

    def translate(self, text: str, action: str = "翻译") -> dict[str, Any]:
        return self._controller.translate_from_window(self.kind, text, action)

    def cancel_translation(self) -> dict[str, Any]:
        return self._controller.cancel_translation()

    def set_mode(self, mode: str) -> dict[str, Any]:
        return self._controller.set_translation_mode(mode)

    def cycle_direction(self) -> dict[str, Any]:
        return self._controller.cycle_direction()

    def copy_text(self, text: str) -> dict[str, Any]:
        return self._controller.copy_text(text)

    def clear_history(self) -> dict[str, Any]:
        return self._controller.clear_history()

    def load_settings(self) -> dict[str, Any]:
        return self._controller.get_settings_payload(probe_runtime=True)

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._controller.save_settings(payload)

    def test_ai_connection(self) -> dict[str, Any]:
        self._controller.test_ai_connection()
        return {"started": True}

    def import_offline_model(self) -> dict[str, Any]:
        return self._controller.import_offline_model(self._window)

    def set_theme(self, theme: str) -> dict[str, Any]:
        return self._controller.set_theme(theme)

    def close_window(self) -> dict[str, Any]:
        return self._controller.close_window(self.kind)

    def toggle_bubble_pin(self) -> dict[str, Any]:
        return self._controller.toggle_bubble_pin()

    def open_zoom_from_bubble(self) -> dict[str, Any]:
        return self._controller.open_zoom_from_bubble()

    def open_zoom_panel(self) -> dict[str, Any]:
        return self._controller.open_zoom_panel()

    def close_zoom_panel(self) -> dict[str, Any]:
        return self._controller.close_zoom_panel()

    def trigger_selection_translate(self) -> dict[str, Any]:
        return self._controller.trigger_selection_translate()

    def cancel_screenshot_selection(self) -> dict[str, Any]:
        return self._controller.cancel_screenshot_capture()

    def finish_screenshot_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._controller.finish_screenshot_selection(payload)
