"""共享表格单元格：所有数据表格使用同一套等宽字体与基线。"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo

from ..fonts import monospace_font


class BaselineCellRendererText(Gtk.CellRendererText):
    """等宽文本单元格：用字体度量统一所有列的文字基线。"""

    def __init__(self, font: Pango.FontDescription):
        super().__init__()
        self.set_property("font-desc", font)
        self.set_property("ypad", 0)

    def _effective_font(self, widget, flags: Gtk.CellRendererState) -> Pango.FontDescription:
        desc = self.props.font_desc
        desc = desc.copy() if desc is not None else widget.get_style_context().get_font(flags).copy()
        desc.set_weight(self.props.weight)
        return desc

    def do_render(self, cr, widget, background_area, cell_area, flags):
        text = self.props.text or ""
        if not text:
            return
        selected = bool(flags & Gtk.CellRendererState.SELECTED)
        context = widget.get_style_context()
        if selected:
            color = context.get_color(Gtk.StateFlags.SELECTED)
        elif self.props.foreground_set:
            color = self.props.foreground_rgba
        else:
            color = context.get_color(Gtk.StateFlags.NORMAL)
        font = self._effective_font(widget, flags)
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(font)
        layout.set_text(text, -1)
        xpad = self.props.xpad
        available = max(1, cell_area.width - xpad * 2)
        if self.props.ellipsize != Pango.EllipsizeMode.NONE:
            layout.set_width(available * Pango.SCALE)
            layout.set_ellipsize(self.props.ellipsize)
        text_width, _text_height = layout.get_pixel_size()
        if self.props.xalign >= 1.0:
            x = cell_area.x + cell_area.width - xpad - text_width
        elif self.props.xalign > 0.0:
            x = cell_area.x + xpad + (available - text_width) / 2
        else:
            x = cell_area.x + xpad
        metrics = widget.get_pango_context().get_metrics(font, None)
        ascent = metrics.get_ascent() / Pango.SCALE
        descent = metrics.get_descent() / Pango.SCALE
        baseline = cell_area.y + (cell_area.height - ascent - descent) / 2 + ascent
        y = baseline - layout.get_baseline() / Pango.SCALE
        cr.save()
        cr.rectangle(cell_area.x, cell_area.y, cell_area.width, cell_area.height)
        cr.clip()
        cr.move_to(x, y)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        PangoCairo.show_layout(cr, layout)
        cr.restore()


def text_renderer(*, xalign: float = 0.0, ellipsize: bool = True) -> BaselineCellRendererText:
    """创建一个与节点表一致的等宽文本 renderer。"""
    renderer = BaselineCellRendererText(monospace_font())
    renderer.set_property("xalign", xalign)
    if ellipsize:
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
    return renderer


def refresh_renderer_fonts(renderers: list[Gtk.CellRendererText]) -> None:
    font = monospace_font()
    for renderer in renderers:
        renderer.set_property("font-desc", font.copy())
