from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING
from ctypes import windll

if TYPE_CHECKING:
    import tkinter as tk


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

    def geometry(self) -> str:
        x = f"+{self.left}" if self.left >= 0 else str(self.left)
        y = f"+{self.top}" if self.top >= 0 else str(self.top)
        return f"{self.width}x{self.height}{x}{y}"


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


class ScreenCaptureOverlay:
    def __init__(
        self,
        root: Any,
        on_capture: Callable[[ScreenRegion], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self.root = root
        self.on_capture = on_capture
        self.on_cancel = on_cancel
        self.bounds = get_virtual_screen_region()
        self.window = None
        self.canvas = None
        self._drag_start: tuple[int, int] | None = None
        self._drag_current: tuple[int, int] | None = None
        self._closed = False

    def start(self) -> None:
        try:
            import tkinter as tk  # lazy import to avoid hard runtime dependency
        except Exception as exc:
            raise RuntimeError("当前环境不可用 tkinter，无法启用旧版截图覆盖层") from exc
        if self.window is not None and self.window.winfo_exists():
            return

        self.window = tk.Toplevel(self.root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.24)
        self.window.configure(bg="#101418", cursor="crosshair")
        self.window.geometry(self.bounds.geometry())

        self.canvas = tk.Canvas(
            self.window,
            bg="#101418",
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        self.window.bind("<Escape>", lambda _event: self.cancel())
        self.window.bind("<Button-3>", lambda _event: self.cancel())
        self.window.bind("<ButtonPress-1>", self._on_press)
        self.window.bind("<B1-Motion>", self._on_drag)
        self.window.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", lambda _event: self.cancel())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self.canvas.create_text(
            24,
            20,
            anchor="nw",
            fill="#f5f7fa",
            text="拖拽选择截图区域  ·  右键 / Esc 取消",
            font=("Microsoft YaHei UI", 10, "bold"),
            tags="hint",
        )
        self.window.after(10, self._activate)

    def cancel(self) -> None:
        if self._closed:
            return
        self._finish()
        self.on_cancel()

    def _activate(self) -> None:
        if self.window is None or not self.window.winfo_exists():
            return
        self.window.lift()
        try:
            self.window.focus_force()
        except Exception:
            pass

    def _on_press(self, event) -> None:
        self._drag_start = (int(event.x_root), int(event.y_root))
        self._drag_current = self._drag_start
        self._redraw_selection()

    def _on_drag(self, event) -> None:
        if self._drag_start is None:
            return
        self._drag_current = (int(event.x_root), int(event.y_root))
        self._redraw_selection()

    def _on_release(self, event) -> None:
        if self._drag_start is None:
            self.cancel()
            return

        self._drag_current = (int(event.x_root), int(event.y_root))
        region = self._build_region()
        if region is None or not region.is_large_enough():
            self.cancel()
            return

        self._finish()
        self.on_capture(region)

    def _finish(self) -> None:
        self._closed = True
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
        self.window = None
        self.canvas = None
        self._drag_start = None
        self._drag_current = None

    def _build_region(self) -> ScreenRegion | None:
        if self._drag_start is None or self._drag_current is None:
            return None

        left = min(int(self._drag_start[0]), int(self._drag_current[0]))
        top = min(int(self._drag_start[1]), int(self._drag_current[1]))
        right = max(int(self._drag_start[0]), int(self._drag_current[0]))
        bottom = max(int(self._drag_start[1]), int(self._drag_current[1]))
        return ScreenRegion(left=left, top=top, right=right, bottom=bottom)

    def _redraw_selection(self) -> None:
        if self.canvas is None:
            return

        self.canvas.delete("selection")
        region = self._build_region()
        if region is None:
            return

        left = int(region.left - self.bounds.left)
        top = int(region.top - self.bounds.top)
        right = int(region.right - self.bounds.left)
        bottom = int(region.bottom - self.bounds.top)

        self.canvas.create_rectangle(
            left,
            top,
            right,
            bottom,
            outline="#f6fbff",
            width=2,
            tags="selection",
        )
        self.canvas.create_rectangle(
            left + 1,
            top + 1,
            right - 1,
            bottom - 1,
            outline="#78c6ff",
            width=1,
            dash=(4, 2),
            tags="selection",
        )

        label_y = top - 18 if top > 28 else bottom + 10
        self.canvas.create_text(
            left + 6,
            label_y,
            anchor="nw",
            fill="#f5f7fa",
            text=f"{region.width} x {region.height}",
            font=("Microsoft YaHei UI", 9, "bold"),
            tags="selection",
        )
