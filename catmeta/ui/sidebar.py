"""原生侧边导航栏。"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ..controller import MODE_LABELS, Controller
from .widgets.common import icon, label


NAV_ITEMS = [
    ("home", "概览", "go-home-symbolic"),
    ("proxies", "代理", "network-workgroup-symbolic"),
    ("profiles", "订阅", "folder-documents-symbolic"),
    ("connections", "连接", "network-transmit-receive-symbolic"),
    ("rules", "规则", "view-list-symbolic"),
    ("logs", "日志", "utilities-terminal-symbolic"),
    ("settings", "设置", "preferences-system-symbolic"),
]


class Sidebar(Gtk.Box):
    def __init__(
        self,
        controller: Controller,
        *,
        on_navigate: Callable[[str], None],
        on_toggle_core: Callable[[], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.controller = controller
        self.on_navigate = on_navigate
        self.get_style_context().add_class("sidebar")
        self.set_size_request(200, -1)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.get_style_context().add_class("sidebar-scroll")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.get_style_context().add_class("sidebar-list")
        self.rows: dict[str, Gtk.ListBoxRow] = {}
        for page_id, title, icon_name in NAV_ITEMS:
            row = Gtk.ListBoxRow()
            row.page_id = page_id  # type: ignore[attr-defined]
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.pack_start(icon(icon_name, 16), False, False, 0)
            box.pack_start(label(title), True, True, 0)
            if page_id == "connections":
                self.connection_badge = label("0", "badge")
                self.connection_badge.set_no_show_all(True)
                self.connection_badge.set_visible(False)
                box.pack_end(self.connection_badge, False, False, 0)
            row.add(box)
            self.listbox.add(row)
            self.rows[page_id] = row
        self.listbox.connect("row-selected", self._on_row_selected)
        inner.pack_start(self.listbox, False, False, 0)
        scroll.add(inner)
        self.pack_start(scroll, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        footer.get_style_context().add_class("sidebar-footer")

        core_chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        core_chip.get_style_context().add_class("core-chip")
        core_chip.set_margin_top(14)
        core_chip.set_margin_bottom(14)
        core_chip.set_margin_start(14)
        core_chip.set_margin_end(14)
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        status_box.set_valign(Gtk.Align.CENTER)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.status_dot = label("●", "status-dot")
        self.status_label = label("未运行", "core-name")
        top.pack_start(self.status_dot, False, False, 0)
        top.pack_start(self.status_label, False, False, 0)
        self.version_label = label("mihomo", "core-version", ellipsize=True, max_width=20)
        self.speed_label = label("↓ 0 B/s   ↑ 0 B/s", "core-version", ellipsize=True, max_width=22)
        status_box.pack_start(top, False, False, 0)
        status_box.pack_start(self.version_label, False, False, 0)
        status_box.pack_start(self.speed_label, False, False, 0)
        core_chip.pack_start(status_box, True, True, 0)
        self.toggle_button = Gtk.Button(label="启动")
        self.toggle_button.set_tooltip_text("启动内核")
        self.toggle_button.set_valign(Gtk.Align.CENTER)
        self.toggle_button.get_style_context().add_class("footer-action")
        self.toggle_button.connect("clicked", lambda _b: on_toggle_core())
        core_chip.pack_end(self.toggle_button, False, False, 0)
        footer.pack_start(core_chip, False, False, 0)
        self.pack_start(footer, False, False, 0)
        self.update_state(controller.state)

    def _on_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is not None:
            page_id = getattr(row, "page_id", None)
            if page_id:
                self.on_navigate(page_id)

    def select(self, page_id: str) -> None:
        row = self.rows.get(page_id)
        if row is not None and self.listbox.get_selected_row() is not row:
            self.listbox.select_row(row)

    def update_state(self, state: str) -> None:
        labels = {
            "stopped": "未运行",
            "starting": "正在启动",
            "running": "已连接",
            "stopping": "正在停止",
            "error": "运行异常",
        }
        self.status_label.set_text(labels.get(state, state))
        context = self.status_dot.get_style_context()
        for css in ("running", "starting", "error"):
            context.remove_class(css)
        if state in ("running", "starting", "error"):
            context.add_class(state)
        if state in ("starting", "stopping"):
            self.toggle_button.set_sensitive(False)
        else:
            self.toggle_button.set_sensitive(True)
        if state == "running":
            self.toggle_button.set_label("停止")
            self.toggle_button.set_tooltip_text("停止内核")
        else:
            self.toggle_button.set_label("启动")
            self.toggle_button.set_tooltip_text("启动内核")
        version = self.controller.core.version()
        mode = MODE_LABELS.get(self.controller.mode(), "")
        self.version_label.set_text(f"{mode}模式 · {version}" if version else "mihomo 未安装")
        self.version_label.set_tooltip_text(f"{mode}模式 · mihomo {version}" if version else "mihomo 未安装")

    def update_traffic(self, down: float, up: float) -> None:
        def fmt(value: float) -> str:
            value = max(0.0, float(value or 0))
            if value < 1024:
                return f"{value:.0f} B/s"
            if value < 1024 * 1024:
                return f"{value / 1024:.0f} K/s"
            return f"{value / 1024 / 1024:.1f} M/s"

        self.speed_label.set_text(f"↓ {fmt(down)}   ↑ {fmt(up)}")

    def update_connections(self, count: int) -> None:
        if count > 0:
            self.connection_badge.set_text("99+" if count > 99 else str(count))
            self.connection_badge.set_visible(True)
        else:
            self.connection_badge.set_visible(False)
