"""无第三方依赖的实时流量曲线。"""

from __future__ import annotations

import math
from collections import deque

import cairo

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo

from ...util import format_speed

DOWN_COLOR = (0.18, 0.70, 0.47)
UP_COLOR = (0.90, 0.60, 0.05)


def _with_alpha(color: tuple[float, float, float], alpha: float) -> tuple[float, float, float, float]:
    return color[0], color[1], color[2], alpha


class TrafficChart(Gtk.DrawingArea):
    def __init__(self, seconds: int = 60):
        super().__init__()
        self.set_vexpand(False)
        self.set_hexpand(True)
        self.set_size_request(-1, 132)
        self.samples: deque[tuple[int, int]] = deque(maxlen=seconds)
        self._hover_x: float | None = None
        self._plot = (0, 0, 0, 0)
        self._fg = (0.2, 0.2, 0.2)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self._on_draw)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("style-updated", self._on_style_updated)

    def _on_style_updated(self, _widget: Gtk.Widget) -> None:
        color = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        self._fg = (color.red, color.green, color.blue)
        self.queue_draw()

    def add_sample(self, up: int, down: int) -> None:
        self.samples.append((max(0, int(down)), max(0, int(up))))
        if self.get_mapped():
            self.queue_draw()

    def clear(self) -> None:
        self.samples.clear()
        self.queue_draw()

    def _on_motion(self, _widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        self._hover_x = event.x
        self.queue_draw()
        return False

    def _on_leave(self, _widget: Gtk.Widget, _event: Gdk.EventCrossing) -> bool:
        self._hover_x = None
        self.queue_draw()
        return False

    @staticmethod
    def _nice_ceiling(value: float) -> float:
        value = max(value, 1024.0)
        exponent = math.floor(math.log10(value))
        fraction = value / (10 ** exponent)
        for step in (1, 2, 2.5, 5, 10):
            if fraction <= step:
                return step * 10 ** exponent
        return 10 ** (exponent + 1)

    def _text(self, cr, text: str, x: float, y: float, size: float = 9, alpha: float = 0.5, align: str = "left") -> None:
        layout = self.create_pango_layout(text)
        desc = Pango.FontDescription.from_string("Sans")
        desc.set_absolute_size(size * Pango.SCALE)
        layout.set_font_description(desc)
        width, height = layout.get_pixel_size()
        if align == "right":
            x -= width
        elif align == "center":
            x -= width / 2
        cr.set_source_rgba(self._fg[0], self._fg[1], self._fg[2], alpha)
        cr.move_to(x, y - height / 2)
        PangoCairo.show_layout(cr, layout)

    def _on_draw(self, _widget: Gtk.Widget, cr) -> bool:
        alloc = self.get_allocation()
        width, height = alloc.width, alloc.height
        left, right, top, bottom = 44.0, 12.0, 12.0, 24.0
        plot_w = max(10.0, width - left - right)
        plot_h = max(10.0, height - top - bottom)
        self._plot = (left, top, plot_w, plot_h)

        samples = list(self.samples)
        max_value = self._nice_ceiling(max((max(v) for v in samples), default=1024))
        # 背景网格
        cr.set_line_width(1)
        for i in range(5):
            y = top + plot_h * i / 4
            cr.set_source_rgba(self._fg[0], self._fg[1], self._fg[2], 0.075)
            cr.set_dash([2, 4])
            cr.move_to(left, y)
            cr.line_to(left + plot_w, y)
            cr.stroke()
        cr.set_dash([])
        for i in range(5):
            value = max_value * (4 - i) / 4
            y = top + plot_h * i / 4
            text = format_speed(value).replace("/s", "")
            self._text(cr, text, left - 7, y, 8.5, 0.42, "right")

        baseline = top + plot_h
        if len(samples) < 2:
            cr.set_source_rgba(self._fg[0], self._fg[1], self._fg[2], 0.22)
            cr.set_line_width(1.5)
            cr.set_dash([5, 5])
            cr.move_to(left, baseline - 2)
            cr.line_to(left + plot_w, baseline - 2)
            cr.stroke()
            cr.set_dash([])
        else:
            slots = max(1, int(plot_w))
            n = len(samples)
            xs = [left + plot_w * (i / max(1, n - 1)) for i in range(n)]
            down = [top + plot_h - (v[0] / max_value) * plot_h for v in samples]
            up = [top + plot_h - (v[1] / max_value) * plot_h for v in samples]
            # 填充
            for values, color in ((down, DOWN_COLOR), (up, UP_COLOR)):
                cr.move_to(xs[0], baseline)
                for x, y in zip(xs, values):
                    cr.line_to(x, y)
                cr.line_to(xs[-1], baseline)
                cr.close_path()
                gradient = cairo.LinearGradient(0, top, 0, baseline)
                gradient.add_color_stop_rgba(0, *_with_alpha(color, 0.22))
                gradient.add_color_stop_rgba(1, *_with_alpha(color, 0.015))
                cr.set_source(gradient)
                cr.fill()
            # 曲线
            for values, color in ((down, DOWN_COLOR), (up, UP_COLOR)):
                cr.set_source_rgba(*color, 0.95)
                cr.set_line_width(2.0)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.move_to(xs[0], values[0])
                for x, y in zip(xs[1:], values[1:]):
                    cr.line_to(x, y)
                cr.stroke()
            self._draw_x_labels(cr, left, top, plot_w, plot_h, n)
            # 悬停
            if self._hover_x is not None and left <= self._hover_x <= left + plot_w:
                index = round((self._hover_x - left) / plot_w * (n - 1))
                index = max(0, min(n - 1, index))
                hx = xs[index]
                cr.set_source_rgba(self._fg[0], self._fg[1], self._fg[2], 0.25)
                cr.set_line_width(1)
                cr.set_dash([3, 3])
                cr.move_to(hx, top)
                cr.line_to(hx, baseline)
                cr.stroke()
                cr.set_dash([])
                for values, color in ((down, DOWN_COLOR), (up, UP_COLOR)):
                    cr.set_source_rgba(*color, 1)
                    cr.arc(hx, values[index], 3.0, 0, math.tau)
                    cr.fill()
                down_value, up_value = samples[index]
                text = f"↓ {format_speed(down_value)}   ↑ {format_speed(up_value)}"
                layout = self.create_pango_layout(text)
                desc = Pango.FontDescription.from_string("Sans")
                desc.set_absolute_size(9 * Pango.SCALE)
                layout.set_font_description(desc)
                tw, th = layout.get_pixel_size()
                box_w, box_h = tw + 16, th + 10
                bx = min(max(left, hx - box_w / 2), left + plot_w - box_w)
                by = top + 4
                self._rounded_rect(cr, bx, by, box_w, box_h, 6)
                cr.set_source_rgba(self._fg[0], self._fg[1], self._fg[2], 0.90)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 0.95)
                cr.move_to(bx + 8, by + 5)
                PangoCairo.show_layout(cr, layout)
        # 时间标签
        cr.set_source_rgba(self._fg[0], self._fg[1], self._fg[2], 0.4)
        self._text(cr, "60 秒前", left, height - 10, 8.5, 0.4)
        self._text(cr, "现在", left + plot_w, height - 10, 8.5, 0.4, "right")
        return False

    def _draw_x_labels(self, cr, left: float, top: float, plot_w: float, plot_h: float, n: int) -> None:
        # 底部只保留“60 秒前 / 现在”，避免拥挤。
        pass

    @staticmethod
    def _rounded_rect(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
        cr.new_sub_path()
        cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
        cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
        cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
        cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
