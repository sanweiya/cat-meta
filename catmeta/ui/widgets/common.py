"""通用原生控件。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango

from ...util import format_speed, latency_class, latency_text


def theme_has_icon(name: str) -> bool:
    try:
        return Gtk.IconTheme.get_default().has_icon(name)
    except Exception:
        return False


def icon(name: str, size: int = 16, fallback: str = "image-missing-symbolic") -> Gtk.Image:
    if not theme_has_icon(name):
        name = fallback if theme_has_icon(fallback) else "image-missing"
    image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.BUTTON)
    image.set_pixel_size(size)
    return image


def label(
    text: str = "",
    css: str | None = None,
    *,
    xalign: float = 0.0,
    wrap: bool = False,
    ellipsize: bool = False,
    max_width: int | None = None,
    selectable: bool = False,
) -> Gtk.Label:
    widget = Gtk.Label(label=text)
    widget.set_xalign(xalign)
    widget.set_use_markup(False)
    if wrap:
        widget.set_line_wrap(True)
        widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
        widget.set_single_line_mode(True)
    if max_width:
        widget.set_max_width_chars(max_width)
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    if selectable:
        widget.set_selectable(True)
        widget.set_can_focus(False)
    if css:
        widget.get_style_context().add_class(css)
    return widget


def set_hand_cursor(widget: Gtk.Widget) -> None:
    try:
        window = widget.get_window()
        if window:
            window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "pointer"))
        else:
            widget.connect("realize", lambda w: w.get_window().set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer")))
    except Exception:
        pass


def spacing(widget: Gtk.Widget, top: int = 0, right: int = 0, bottom: int = 0, left: int = 0) -> None:
    widget.set_margin_top(top)
    widget.set_margin_end(right)
    widget.set_margin_bottom(bottom)
    widget.set_margin_start(left)


class Card(Gtk.Box):
    def __init__(self, *, orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL, spacing: int = 0, pad: bool = True, css: str = "card"):
        super().__init__(orientation=orientation, spacing=spacing)
        if css:
            self.get_style_context().add_class(css)
        if pad:
            self.get_style_context().add_class("card-pad")


class ClickBox(Gtk.EventBox):
    """可点击的卡片容器。"""

    def __init__(self, *, css: str = "card", pad: bool = True, spacing: int = 6):
        super().__init__()
        self.get_style_context().add_class(css)
        self.set_visible_window(True)
        self.inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        if pad:
            self.inner.get_style_context().add_class("card-pad")
        self.add(self.inner)
        self.set_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        set_hand_cursor(self)

    def connect_click(self, callback: Callable[..., Any]) -> None:
        self.connect("button-press-event", lambda _w, event: (callback() if event.button == 1 else False) or False)


class IconButton(Gtk.Button):
    def __init__(self, icon_name: str, tooltip: str, callback: Callable[[], Any] | None = None, *, size: int = 16, css: str = "flat icon-button"):
        super().__init__()
        self.set_image(icon(icon_name, size))
        self.set_tooltip_text(tooltip)
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_focus_on_click(False)
        for item in css.split():
            self.get_style_context().add_class(item)
        if callback:
            self.connect("clicked", lambda _b: callback())


class LinkButton(Gtk.Button):
    def __init__(self, text: str, callback: Callable[[], Any], *, icon_name: str | None = None):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if icon_name:
            box.pack_start(icon(icon_name, 14), False, False, 0)
        box.pack_start(label(text), False, False, 0)
        self.add(box)
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.get_style_context().add_class("flat")
        self.connect("clicked", lambda _b: callback())


class Badge(Gtk.Label):
    def __init__(self, text: str, css: str = ""):
        super().__init__(label=text)
        self.get_style_context().add_class("badge")
        if css:
            for item in css.split():
                self.get_style_context().add_class(item)
        self.set_valign(Gtk.Align.CENTER)


class LatencyBadge(Gtk.Label):
    def __init__(self, delay: Any = None):
        super().__init__(label=latency_text(delay))
        self.get_style_context().add_class("latency-badge")
        self.set_delay(delay)

    def set_delay(self, delay: Any) -> None:
        context = self.get_style_context()
        for css in ("latency-good", "latency-medium", "latency-bad", "latency-timeout"):
            context.remove_class(css)
        context.add_class(latency_class(delay))
        self.set_text(latency_text(delay))


class StatCard(Gtk.Box):
    def __init__(self, title: str, icon_name: str, value: str = "—", unit: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.get_style_context().add_class("stat-card")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.pack_start(icon(icon_name, 13), False, False, 0)
        top.pack_start(label(title, "stat-title"), False, False, 0)
        self.pack_start(top, False, False, 0)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.value = label(value, "stat-value")
        self.unit = label(unit, "stat-unit")
        self.unit.set_valign(Gtk.Align.END)
        self.unit.set_margin_bottom(3)
        line.pack_start(self.value, False, False, 0)
        line.pack_start(self.unit, False, False, 0)
        self.pack_start(line, False, False, 0)

    def set(self, value: str, unit: str | None = None) -> None:
        self.value.set_text(value)
        if unit is not None:
            self.unit.set_text(unit)


class PageHeader(Gtk.Box):
    """页面工具栏。

    Mint 原生应用的页面标题位于窗口标题栏中，页面顶部只保留操作工具栏。
    title/subtitle 仍保存下来，供主窗口同步到 HeaderBar。
    """

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.get_style_context().add_class("page-toolbar")
        self.set_valign(Gtk.Align.CENTER)
        self.title_label = label(title, "page-title", ellipsize=True)
        self.subtitle_label = label(subtitle, "page-subtitle", wrap=True)
        self.lead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lead.set_valign(Gtk.Align.CENTER)
        self.pack_start(self.lead, True, True, 0)
        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.actions.set_valign(Gtk.Align.CENTER)
        self.actions.set_halign(Gtk.Align.END)
        self.pack_end(self.actions, False, False, 0)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.set_text(text)


class BasePage(Gtk.Box):
    def __init__(self, title: str, subtitle: str = "", *, scroll: bool = True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("page")
        self.header = PageHeader(title, subtitle)
        self.pack_start(self.header, False, False, 0)
        self.scroll: Gtk.ScrolledWindow | None = None
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.body.get_style_context().add_class("page-body")
        if scroll:
            self.scroll = Gtk.ScrolledWindow()
            self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.scroll.set_vexpand(True)
            self.scroll.set_overlay_scrolling(True)
            self.scroll.add(self.body)
            self.pack_start(self.scroll, True, True, 0)
        else:
            self.body.set_vexpand(True)
            self.pack_start(self.body, True, True, 0)
        self._page_loaded = False

    def on_show(self) -> None:
        self._page_loaded = True

    def add_section_title(self, text: str, subtitle: str = "") -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.pack_start(label(text, "section-heading"), False, False, 0)
        if subtitle:
            box.pack_start(label(subtitle, "section-subtitle"), False, False, 0)
        return box

    def add_section_heading(self, text: str) -> Gtk.Label:
        return label(text, "section-heading")


class EmptyState(Gtk.Box):
    def __init__(self, icon_name: str, title: str, subtitle: str, action: Gtk.Widget | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.get_style_context().add_class("empty-state")
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        holder = Gtk.Box()
        holder.set_halign(Gtk.Align.CENTER)
        icon_widget = icon(icon_name, 42)
        icon_widget.get_style_context().add_class("dim-label")
        holder.pack_start(icon_widget, False, False, 0)
        self.pack_start(holder, False, False, 6)
        self.title_label = label(title, "empty-title", xalign=0.5)
        self.subtitle_label = label(subtitle, "empty-subtitle", xalign=0.5, wrap=True, max_width=48)
        self.pack_start(self.title_label, False, False, 0)
        self.pack_start(self.subtitle_label, False, False, 0)
        if action:
            action.set_halign(Gtk.Align.CENTER)
            action.set_margin_top(8)
            self.pack_start(action, False, False, 0)

    def set(self, title: str | None = None, subtitle: str | None = None) -> None:
        if title is not None:
            self.title_label.set_text(title)
        if subtitle is not None:
            self.subtitle_label.set_text(subtitle)


class SettingRow(Gtk.Box):
    def __init__(self, title: str, subtitle: str = "", control: Gtk.Widget | None = None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self.get_style_context().add_class("settings-row")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.pack_start(label(title, "row-title"), False, False, 0)
        if subtitle:
            text.pack_start(label(subtitle, "row-subtitle", wrap=True), False, False, 0)
        self.pack_start(text, True, True, 0)
        if control is not None:
            control.set_valign(Gtk.Align.CENTER)
            self.pack_end(control, False, False, 0)


class SettingsGroup(Gtk.Box):
    def __init__(self, title: str, rows: list[Gtk.Widget]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_label = label(title, "settings-group-title")
        self.pack_start(title_label, False, False, 0)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.get_style_context().add_class("list-card")
        for index, row in enumerate(rows):
            if index:
                row.get_style_context().add_class("settings-row")
            card.pack_start(row, False, False, 0)
        self.pack_start(card, False, False, 0)


class IconText(Gtk.Box):
    def __init__(self, icon_name: str, text: str, *, css: str = "", size: int = 14, spacing_: int = 6):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing_)
        self.pack_start(icon(icon_name, size), False, False, 0)
        self.text_label = label(text, css or None)
        self.pack_start(self.text_label, False, False, 0)


def speed_box() -> tuple[Gtk.Box, Gtk.Label, Gtk.Label]:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    up_icon = icon("go-up-symbolic", 12)
    up_label = label("0 B/s", "tiny-label")
    box.pack_start(up_icon, False, False, 0)
    box.pack_start(up_label, False, False, 0)
    return box, up_label, up_label


def format_delay(delay: Any) -> str:
    return latency_text(delay)


def format_speed_short(value: Any) -> str:
    return format_speed(value)


def add_class(widget: Gtk.Widget, *classes: str) -> None:
    context = widget.get_style_context()
    for css in classes:
        context.add_class(css)


def separator(horizontal: bool = True) -> Gtk.Separator:
    return Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL)
