from __future__ import annotations

import io
import threading
from typing import Callable


class NativeScreenshotOverlay:
    def __init__(
        self,
        *,
        logger,
        on_selection: Callable[[dict[str, int]], None],
        on_cancel: Callable[[int], None],
    ) -> None:
        self._logger = logger
        self._on_selection = on_selection
        self._on_cancel = on_cancel
        self._lock = threading.RLock()
        self._thread = None
        self._thread_ready = threading.Event()
        self._form = None
        self._app_context = None

    @staticmethod
    def _bootstrap_dotnet() -> None:
        try:
            import clr  # type: ignore[import-not-found]
        except Exception:
            return
        for assembly in ("System", "System.Drawing", "System.Windows.Forms"):
            try:
                clr.AddReference(assembly)
            except Exception:
                continue

    def show(
        self,
        *,
        session_id: int,
        image,
        bounds: dict[str, int],
        hint_x: int,
        hint_y: int,
    ) -> bool:
        self._ensure_thread()
        form = self._wait_form(timeout=1.8)
        if form is None:
            return False
        bitmap = self._to_bitmap(image)
        if bitmap is None:
            return False
        local_hint = (
            int(hint_x) - int(bounds.get("left", 0)),
            int(hint_y) - int(bounds.get("top", 0)),
        )

        def apply() -> None:
            form.begin_session(
                int(session_id),
                bitmap,
                dict(bounds),
                local_hint,
            )

        return self._invoke_form(apply, wait=True, timeout=2.0)

    def hide(self) -> None:
        def apply() -> None:
            form = self._form
            if form is not None:
                form.end_session(dispose_bitmap=True)

        self._invoke_form(apply, wait=False, timeout=0.2)

    def destroy(self) -> None:
        def close_form() -> None:
            form = self._form
            if form is None:
                return
            try:
                form.end_session(dispose_bitmap=True)
            except Exception:
                pass
            try:
                form.Close()
            except Exception:
                pass
            ctx = self._app_context
            if ctx is not None:
                try:
                    ctx.ExitThread()
                except Exception:
                    pass

        self._invoke_form(close_form, wait=False, timeout=0.2)
        thread = None
        with self._lock:
            thread = self._thread
        if thread is not None:
            try:
                thread.Join(1000)
            except Exception:
                pass
        with self._lock:
            self._thread = None
            self._form = None
            self._thread_ready.clear()

    def _emit_cancel(self, session_id: int) -> None:
        def worker() -> None:
            try:
                self._on_cancel(int(session_id))
            except Exception:
                self._logger.exception("Native screenshot cancel callback failed")

        threading.Thread(target=worker, daemon=True).start()

    def _emit_selection(self, payload: dict[str, int]) -> None:
        def worker() -> None:
            try:
                self._on_selection(dict(payload))
            except Exception:
                self._logger.exception("Native screenshot selection callback failed")

        threading.Thread(target=worker, daemon=True).start()

    def _wait_form(self, timeout: float) -> object | None:
        self._thread_ready.wait(max(0.0, float(timeout)))
        with self._lock:
            return self._form

    def _ensure_thread(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is not None:
                try:
                    if bool(thread.IsAlive):
                        return
                except Exception:
                    pass
            self._thread_ready.clear()
            try:
                self._bootstrap_dotnet()
                from System.Threading import ApartmentState, Thread, ThreadStart  # type: ignore[import-not-found]
            except ImportError:
                self._logger.exception("System.Threading unavailable for native screenshot overlay")
                return
            thread = Thread(ThreadStart(self._run_loop))
            thread.IsBackground = True
            thread.Name = "wordpack-native-screenshot-overlay"
            thread.SetApartmentState(ApartmentState.STA)
            self._thread = thread
            thread.Start()

    def _run_loop(self) -> None:
        form = None
        try:
            self._bootstrap_dotnet()
            from System.Windows.Forms import Application, ApplicationContext  # type: ignore[import-not-found]
            form = _NativeScreenshotForm(owner=self)
            context = ApplicationContext()
            with self._lock:
                self._form = form
                self._app_context = context
            self._thread_ready.set()
            Application.Run(context)
        except Exception:
            self._logger.exception("Native screenshot overlay loop failed")
        finally:
            with self._lock:
                self._form = None
                self._app_context = None
            self._thread_ready.set()
            if form is not None:
                try:
                    form.Dispose()
                except Exception:
                    pass

    def _invoke_form(self, action: Callable[[], None], *, wait: bool, timeout: float) -> bool:
        form = self._wait_form(timeout=0.3)
        if form is None:
            return False
        try:
            if bool(getattr(form, "IsDisposed", False)):
                return False
        except Exception:
            return False
        try:
            invoke_required = bool(getattr(form, "InvokeRequired", False))
        except Exception:
            invoke_required = False
        if not invoke_required:
            try:
                action()
                return True
            except Exception:
                self._logger.exception("Failed to execute native screenshot overlay action")
                return False

        done = threading.Event()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                action()
            except Exception as exc:  # pragma: no cover - marshaled from UI thread
                errors.append(exc)
            finally:
                done.set()

        try:
            from System import Action  # type: ignore[import-not-found]
            form.BeginInvoke(Action(run))
        except Exception:
            self._logger.exception("Failed to marshal native screenshot overlay action")
            return False
        if not wait:
            return True
        if not done.wait(max(0.05, float(timeout))):
            return False
        if errors:
            err = errors[-1]
            self._logger.error(
                "Native screenshot overlay action failed: %s",
                err,
                exc_info=(type(err), err, err.__traceback__),
            )
            return False
        return True

    def _to_bitmap(self, image):
        try:
            self._bootstrap_dotnet()
            from System import Array, Byte  # type: ignore[import-not-found]
            from System.Drawing import Bitmap  # type: ignore[import-not-found]
            from System.IO import MemoryStream  # type: ignore[import-not-found]
        except ImportError:
            self._logger.exception("System.Drawing unavailable for native screenshot overlay")
            return None
        try:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            data = buffer.getvalue()
            byte_array = Array[Byte](data)
            stream = MemoryStream(byte_array, 0, len(byte_array), False, True)
            tmp_bitmap = Bitmap(stream)
            bitmap = Bitmap(tmp_bitmap)
            tmp_bitmap.Dispose()
            stream.Close()
            return bitmap
        except Exception:
            self._logger.exception("Failed to convert screenshot image to native bitmap")
            return None


class _NativeScreenshotForm:
    def __init__(self, *, owner: NativeScreenshotOverlay) -> None:
        from System.Drawing import Color, Font, FontStyle, GraphicsUnit, Pen, SolidBrush  # type: ignore[import-not-found]
        from System.Windows.Forms import (  # type: ignore[import-not-found]
            ControlStyles,
            Cursors,
            Form,
            FormBorderStyle,
            FormStartPosition,
            MouseButtons,
            PaintEventHandler,
            KeyEventHandler,
            MouseEventHandler,
        )

        self._owner = owner
        self._mouse_buttons = MouseButtons
        self._form = Form()
        self._form.FormBorderStyle = getattr(FormBorderStyle, "None")
        self._form.StartPosition = FormStartPosition.Manual
        self._form.ShowInTaskbar = False
        self._form.TopMost = True
        self._form.KeyPreview = True
        self._form.Cursor = Cursors.Cross
        self._form.BackColor = Color.Black
        self._form.SetStyle(
            ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer,
            True,
        )
        self._form.UpdateStyles()

        self._border_pen = Pen(Color.FromArgb(255, 80, 160, 255), 2.0)
        self._size_bg_brush = SolidBrush(Color.FromArgb(220, 18, 24, 33))
        self._size_text_brush = SolidBrush(Color.FromArgb(245, 245, 245))
        self._hint_bg_brush = SolidBrush(Color.FromArgb(205, 58, 62, 70))
        self._hint_text_brush = SolidBrush(Color.FromArgb(250, 250, 250))
        self._hint_font = Font("Microsoft YaHei UI", 9.6, FontStyle.Regular, GraphicsUnit.Point)
        self._size_font = Font("Microsoft YaHei UI", 9.2, FontStyle.Regular, GraphicsUnit.Point)
        self._hint_text = "拖拽选择截图区域  右键 / Esc 取消"
        self._bitmap = None
        self._session_id = 0
        self._session_active = False
        self._completed = False
        self._bounds: dict[str, int] = {}
        self._source_width = 1
        self._source_height = 1
        self._hint_x = 22
        self._hint_y = 22
        self._dragging = False
        self._start = (0, 0)
        self._current = (0, 0)
        self._hint_hidden = False

        self._paint_handler = PaintEventHandler(self._on_paint)
        self._mouse_down_handler = MouseEventHandler(self._on_mouse_down)
        self._mouse_move_handler = MouseEventHandler(self._on_mouse_move)
        self._mouse_up_handler = MouseEventHandler(self._on_mouse_up)
        self._key_down_handler = KeyEventHandler(self._on_key_down)
        self._closed_handler = lambda *_: self.end_session(dispose_bitmap=True)

        self._form.Paint += self._paint_handler
        self._form.MouseDown += self._mouse_down_handler
        self._form.MouseMove += self._mouse_move_handler
        self._form.MouseUp += self._mouse_up_handler
        self._form.KeyDown += self._key_down_handler
        self._form.FormClosed += self._closed_handler

    def __getattr__(self, item):
        return getattr(self._form, item)

    @property
    def native_form(self):
        return self._form

    def begin_session(
        self,
        session_id: int,
        bitmap,
        bounds: dict[str, int],
        hint_local: tuple[int, int],
    ) -> None:
        from System.Drawing import Point, Size  # type: ignore[import-not-found]
        from System.Windows.Forms import Cursors  # type: ignore[import-not-found]

        self.end_session(dispose_bitmap=True)
        self._bitmap = bitmap
        self._session_id = int(session_id)
        self._bounds = dict(bounds or {})
        self._source_width = int(getattr(bitmap, "Width", self._bounds.get("width", 1)) or 1)
        self._source_height = int(getattr(bitmap, "Height", self._bounds.get("height", 1)) or 1)
        self._session_active = True
        self._completed = False
        self._dragging = False
        self._start = (0, 0)
        self._current = (0, 0)
        self._hint_hidden = False
        width = max(1, int(self._bounds.get("width", self._source_width)))
        height = max(1, int(self._bounds.get("height", self._source_height)))
        left = int(self._bounds.get("left", 0))
        top = int(self._bounds.get("top", 0))
        self._form.Location = Point(left, top)
        self._form.Size = Size(width, height)
        self._hint_x, self._hint_y = self._clamp_hint(int(hint_local[0]), int(hint_local[1]), width, height)
        self._form.Cursor = Cursors.Cross
        self._form.Show()
        self._form.BringToFront()
        try:
            self._form.Activate()
            self._form.Focus()
        except Exception:
            pass
        self._form.Invalidate()

    def end_session(self, *, dispose_bitmap: bool) -> None:
        self._session_active = False
        self._completed = False
        self._dragging = False
        try:
            self._form.Hide()
        except Exception:
            pass
        if dispose_bitmap:
            self._dispose_bitmap()

    def _dispose_bitmap(self) -> None:
        bitmap = self._bitmap
        self._bitmap = None
        if bitmap is None:
            return
        try:
            bitmap.Dispose()
        except Exception:
            pass

    def _on_mouse_down(self, _sender, event) -> None:
        if not self._session_active:
            return
        button = event.Button
        if button == self._mouse_buttons.Right:
            self._cancel_current()
            return
        if button != self._mouse_buttons.Left:
            return
        x = int(event.X)
        y = int(event.Y)
        self._dragging = True
        self._start = (x, y)
        self._current = (x, y)
        self._hint_hidden = True
        self._form.Invalidate()

    def _on_mouse_move(self, _sender, event) -> None:
        if not self._session_active:
            return
        x = int(event.X)
        y = int(event.Y)
        if self._dragging:
            self._current = (x, y)
            self._form.Invalidate()
            return
        self._hint_x = x + 14
        self._hint_y = y + 16
        self._form.Invalidate()

    def _on_mouse_up(self, _sender, event) -> None:
        if not self._session_active:
            return
        if int(event.Button) != int(self._mouse_buttons.Left):
            return
        if not self._dragging:
            return
        self._dragging = False
        self._current = (int(event.X), int(event.Y))
        rect = self._normalized_rect(self._start, self._current)
        if rect[2] <= 0 or rect[3] <= 0:
            self._form.Invalidate()
            return
        self._complete_with_rect(rect)

    def _on_key_down(self, _sender, event) -> None:
        try:
            key_value = int(event.KeyCode)
        except Exception:
            key_value = 0
        if key_value == 27:
            self._cancel_current()
            try:
                event.Handled = True
            except Exception:
                pass

    def _on_paint(self, _sender, event) -> None:
        from System.Drawing import Rectangle  # type: ignore[import-not-found]

        graphics = event.Graphics
        width = max(1, int(self._form.ClientSize.Width))
        height = max(1, int(self._form.ClientSize.Height))
        if self._bitmap is not None:
            graphics.DrawImage(self._bitmap, Rectangle(0, 0, width, height))

        rect = None
        if self._dragging:
            rect = self._normalized_rect(self._start, self._current)
        if rect is not None and rect[2] > 0 and rect[3] > 0:
            x, y, w, h = rect
            graphics.DrawRectangle(self._border_pen, Rectangle(x, y, max(1, w - 1), max(1, h - 1)))
            self._draw_size_label(graphics, x, y, w, h, width)

        if self._session_active and (not self._hint_hidden):
            self._draw_hint(graphics, width, height)

    def _draw_hint(self, graphics, width: int, height: int) -> None:
        from System.Drawing import Rectangle  # type: ignore[import-not-found]

        text = self._hint_text
        measured = graphics.MeasureString(text, self._hint_font)
        hint_w = int(measured.Width) + 16
        hint_h = max(28, int(measured.Height) + 8)
        x = min(max(8, int(self._hint_x)), max(8, width - hint_w - 8))
        y = min(max(8, int(self._hint_y)), max(8, height - hint_h - 8))
        graphics.FillRectangle(self._hint_bg_brush, Rectangle(x, y, hint_w, hint_h))
        graphics.DrawString(text, self._hint_font, self._hint_text_brush, float(x + 8), float(y + 4))

    def _draw_size_label(self, graphics, x: int, y: int, w: int, h: int, max_width: int) -> None:
        from System.Drawing import Rectangle  # type: ignore[import-not-found]

        text = f"{w} x {h}"
        measured = graphics.MeasureString(text, self._size_font)
        label_w = int(measured.Width) + 12
        label_h = max(24, int(measured.Height) + 6)
        label_x = x
        if label_x + label_w > max_width - 6:
            label_x = max(6, max_width - label_w - 6)
        label_y = y - label_h - 8
        if label_y < 6:
            label_y = y + 8
        graphics.FillRectangle(self._size_bg_brush, Rectangle(label_x, label_y, label_w, label_h))
        graphics.DrawString(text, self._size_font, self._size_text_brush, float(label_x + 6), float(label_y + 3))

    @staticmethod
    def _normalized_rect(start: tuple[int, int], current: tuple[int, int]) -> tuple[int, int, int, int]:
        sx, sy = int(start[0]), int(start[1])
        cx, cy = int(current[0]), int(current[1])
        left = min(sx, cx)
        top = min(sy, cy)
        width = abs(cx - sx)
        height = abs(cy - sy)
        return left, top, width, height

    @staticmethod
    def _clamp_hint(x: int, y: int, width: int, height: int) -> tuple[int, int]:
        return (
            min(max(8, int(x) + 16), max(8, int(width) - 210)),
            min(max(8, int(y) + 18), max(8, int(height) - 40)),
        )

    def _cancel_current(self) -> None:
        if not self._session_active or self._completed:
            return
        session_id = int(self._session_id)
        self._completed = True
        self.end_session(dispose_bitmap=True)
        self._owner._emit_cancel(session_id)

    def _complete_with_rect(self, rect: tuple[int, int, int, int]) -> None:
        if not self._session_active or self._completed:
            return
        x, y, w, h = rect
        session_id = int(self._session_id)
        client_w = max(1, int(self._form.ClientSize.Width))
        client_h = max(1, int(self._form.ClientSize.Height))
        scale_x = float(self._source_width) / float(client_w)
        scale_y = float(self._source_height) / float(client_h)
        bounds_left = int(self._bounds.get("left", 0))
        bounds_top = int(self._bounds.get("top", 0))
        left = bounds_left + int(round(float(x) * scale_x))
        top = bounds_top + int(round(float(y) * scale_y))
        right = bounds_left + int(round(float(x + w) * scale_x))
        bottom = bounds_top + int(round(float(y + h) * scale_y))
        self._completed = True
        self.end_session(dispose_bitmap=True)
        self._owner._emit_selection(
            {
                "sessionId": session_id,
                "left": int(left),
                "top": int(top),
                "right": int(right),
                "bottom": int(bottom),
            }
        )
