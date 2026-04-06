from __future__ import annotations

from dataclasses import dataclass
from ctypes import windll


SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

MIN_CAPTURE_SIZE = 12


@dataclass(frozen=True)
class ScreenRegion:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, int(self.right) - int(self.left))

    @property
    def height(self) -> int:
        return max(0, int(self.bottom) - int(self.top))

    def is_large_enough(self) -> bool:
        return self.width >= MIN_CAPTURE_SIZE and self.height >= MIN_CAPTURE_SIZE

    def as_bbox(self) -> tuple[int, int, int, int]:
        return int(self.left), int(self.top), int(self.right), int(self.bottom)


def get_virtual_screen_region() -> ScreenRegion:
    left = int(windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    return ScreenRegion(left=left, top=top, right=left + width, bottom=top + height)


def capture_screen_region(region: ScreenRegion):
    try:
        from PIL import ImageGrab
    except Exception as exc:
        raise RuntimeError("截图功能依赖 Pillow，请先执行 `pip install -r requirements.txt`。") from exc

    try:
        return ImageGrab.grab(bbox=region.as_bbox(), all_screens=True)
    except Exception as exc:
        raise RuntimeError(f"区域截图失败: {exc}") from exc
