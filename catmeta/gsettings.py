"""GSettings 安全访问。

Gio.Settings.new() 在 schema 未安装时不是抛异常，而是直接 g_error 中止进程
（GLib-GIO-ERROR）。在 XFCE / LXQt 这类没有 Cinnamon schema 的桌面上，
必须先确认 schema 存在再创建对象。
"""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio


def schema_installed(name: str) -> bool:
    try:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None:
            return False
        return source.lookup(name, True) is not None
    except Exception:
        return False


def new(name: str) -> Gio.Settings | None:
    """返回 Gio.Settings；schema 不存在时返回 None，不会中止进程。"""
    if not schema_installed(name):
        return None
    try:
        return Gio.Settings.new(name)
    except Exception:
        return None
