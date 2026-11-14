"""系统字体与界面缩放。"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

from .. import gsettings

FONT_BASE_POINTS = 10.0


def ui_font_scale() -> float:
    """相对 10pt 设计基准的系统字体缩放倍率。"""
    points = FONT_BASE_POINTS
    try:
        font_name = Gtk.Settings.get_default().get_property("gtk-font-name") or ""
        desc = Pango.FontDescription.from_string(font_name)
        if desc.get_size() > 0:
            points = desc.get_size() / Pango.SCALE
    except Exception:
        pass
    scaling = 1.0
    for schema in ("org.cinnamon.desktop.interface", "org.gnome.desktop.interface"):
        settings = gsettings.new(schema)
        if settings is None:
            continue
        try:
            settings.get_value("text-scaling-factor")
            scaling = float(settings.get_double("text-scaling-factor") or 1.0)
            break
        except Exception:
            continue
    return max(0.75, min(2.0, (points / FONT_BASE_POINTS) * scaling))


def monospace_font(*, ui_scale: bool = True) -> Pango.FontDescription:
    """系统等宽字体；可选叠加界面字体缩放。"""
    settings = gsettings.new("org.gnome.desktop.interface")
    try:
        font_name = settings.get_string("monospace-font-name") if settings is not None else ""
    except Exception:
        font_name = ""
    desc = Pango.FontDescription.from_string(font_name or "Monospace 10")
    if desc.get_size() == 0:
        desc.set_size(10 * Pango.SCALE)
    if ui_scale:
        desc.set_size(max(1, int(round(desc.get_size() * ui_font_scale()))))
    return desc
