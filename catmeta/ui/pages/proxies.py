"""代理页：原生分组下拉与节点表格。"""

from __future__ import annotations

import math
import time

import cairo
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo

from ...async_utils import run_async
from ...controller import Controller
from ...util import latency_text
from ..fonts import monospace_font
from ..widgets.common import BasePage, EmptyState, IconButton, label
from ..widgets.table import BaselineCellRendererText, refresh_renderer_fonts



COLUMN_ORDER = ["status", "name", "type", "delay", "actions"]
COLUMN_LABELS = {
    "name": "节点",
    "type": "类型",
    "delay": "延迟",
    "status": "状态",
    "actions": "操作",
}
ACTION_COLUMN_WIDTH = 118
ACTION_BUTTON_WIDTH = 50
ACTION_BUTTON_HEIGHT = 22
ACTION_BUTTON_GAP = 8
ACTION_HEIGHT = 26

GROUP_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay"}
TYPE_LABELS = {
    "Selector": "手动选择",
    "URLTest": "自动测速",
    "Fallback": "故障转移",
    "LoadBalance": "负载均衡",
    "Relay": "链式代理",
    "Direct": "直连",
    "Reject": "拒绝",
    "RejectDrop": "拒绝",
    "Compatible": "兼容",
    "Shadowsocks": "SS",
    "ShadowsocksR": "SSR",
    "VMess": "VMess",
    "Vmess": "VMess",
    "VLESS": "VLESS",
    "Trojan": "Trojan",
    "Hysteria": "Hysteria",
    "Hysteria2": "Hysteria2",
    "Tuic": "TUIC",
    "WireGuard": "WireGuard",
    "Socks5": "SOCKS5",
    "Http": "HTTP",
    "Snell": "Snell",
    "AnyTLS": "AnyTLS",
    "Mieru": "Mieru",
}

LATENCY_COLORS = {
    "latency-good": "#2ec27e",
    "latency-medium": "#c47d00",
    "latency-bad": "#e01b24",
    "latency-timeout": "#8a8a8a",
}


def _latency_color(delay: Any) -> str:
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        return LATENCY_COLORS["latency-timeout"]
    if delay <= 0:
        return LATENCY_COLORS["latency-timeout"]
    if delay < 200:
        return LATENCY_COLORS["latency-good"]
    if delay < 500:
        return LATENCY_COLORS["latency-medium"]
    return LATENCY_COLORS["latency-bad"]


class CheckCellRenderer(BaselineCellRendererText):
    """状态列对勾：矢量绘制，避免字体里的勾过细。"""

    def do_render(self, cr, widget, _background_area, cell_area, flags):
        if not self.props.text:
            return
        selected = bool(flags & Gtk.CellRendererState.SELECTED)
        context = widget.get_style_context()
        color = context.get_color(Gtk.StateFlags.SELECTED if selected else Gtk.StateFlags.NORMAL)
        width, height = 8.5, 6.5
        left = cell_area.x + (cell_area.width - width) / 2
        top = cell_area.y + (cell_area.height - height) / 2
        cr.save()
        cr.set_line_width(2.0)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.move_to(left, top + height * 0.55)
        cr.line_to(left + width * 0.36, top + height)
        cr.line_to(left + width, top)
        cr.stroke()
        cr.restore()


def _rounded_rect(cr, x: float, y: float, width: float, height: float, radius: float) -> None:
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
    cr.close_path()


class ActionCellRenderer(Gtk.CellRenderer):
    """在树形列表单元格内绘制两个原生风格的迷你按钮。"""

    def __init__(self):
        super().__init__()
        self.set_property("mode", Gtk.CellRendererMode.ACTIVATABLE)
        self.set_property("xpad", 0)
        self.set_property("ypad", 0)
        self.connected = False
        self.hover_action = ""
        self.hover_y = -1.0
        self.pressed_action = ""
        self.pressed_y = -1.0

    def set_feedback(self, hover_action: str = "", hover_y: float = -1.0,
                     pressed_action: str = "", pressed_y: float = -1.0) -> None:
        self.hover_action = hover_action
        self.hover_y = hover_y
        self.pressed_action = pressed_action
        self.pressed_y = pressed_y

    def do_get_size(self, _widget, _cell_area):
        return (0, 0, ACTION_COLUMN_WIDTH, ACTION_HEIGHT)

    def do_get_preferred_width(self, _widget):
        return (ACTION_COLUMN_WIDTH, ACTION_COLUMN_WIDTH)

    def do_get_preferred_height(self, _widget):
        return (ACTION_HEIGHT, ACTION_HEIGHT)

    def do_render(self, cr, widget, _background_area, cell_area, flags):
        selected = bool(flags & Gtk.CellRendererState.SELECTED)
        context = widget.get_style_context()
        foreground = context.get_color(Gtk.StateFlags.NORMAL)
        red, green, blue = foreground.red, foreground.green, foreground.blue
        if selected:
            red = green = blue = 1.0
        total = ACTION_BUTTON_WIDTH * 2 + ACTION_BUTTON_GAP
        left = cell_area.x + max(0.0, (cell_area.width - total) / 2)
        top = cell_area.y + max(0.0, (cell_area.height - ACTION_BUTTON_HEIGHT) / 2)
        for index, text in enumerate(("连接", "测试")):
            action_id = "connect" if index == 0 else "test"
            disabled = index == 0 and self.connected
            hovered = self.hover_action == action_id and abs(cell_area.y - self.hover_y) < 2.0
            pressed = self.pressed_action == action_id and abs(cell_area.y - self.pressed_y) < 2.0
            if disabled:
                fill_alpha, border_alpha, text_alpha = 0.04, 0.18, 0.32
            elif pressed:
                fill_alpha, border_alpha, text_alpha = (0.36, 0.95, 1.0) if selected else (0.24, 0.88, 1.0)
            elif hovered:
                fill_alpha, border_alpha, text_alpha = (0.30, 0.92, 1.0) if selected else (0.17, 0.72, 1.0)
            elif selected:
                fill_alpha, border_alpha, text_alpha = 0.20, 0.78, 0.92
            else:
                fill_alpha, border_alpha, text_alpha = 0.10, 0.42, 0.92
            x = left + index * (ACTION_BUTTON_WIDTH + ACTION_BUTTON_GAP)
            cr.save()
            _rounded_rect(cr, x, top, ACTION_BUTTON_WIDTH, ACTION_BUTTON_HEIGHT, 3)
            cr.set_source_rgba(red, green, blue, fill_alpha)
            cr.fill_preserve()
            cr.set_source_rgba(red, green, blue, border_alpha)
            cr.set_line_width(1.0)
            cr.stroke()
            layout = widget.create_pango_layout(text)
            font = context.get_font(Gtk.StateFlags.NORMAL)
            if font is not None:
                layout.set_font_description(font)
            text_width, text_height = layout.get_pixel_size()
            cr.move_to(x + (ACTION_BUTTON_WIDTH - text_width) / 2, top + (ACTION_BUTTON_HEIGHT - text_height) / 2)
            cr.set_source_rgba(red, green, blue, text_alpha)
            PangoCairo.show_layout(cr, layout)
            cr.restore()


class ProxiesPage(BasePage):
    def __init__(self, controller: Controller, toast: Callable[..., None]):
        super().__init__("代理", "双击节点连接 · 右键测速/列设置", scroll=False)
        self.controller = controller
        self.toast = toast
        self.data: dict[str, Any] = {}
        self.groups: list[str] = []
        self.group = ""
        self.delays: dict[str, int] = {}
        self._loading = False
        self._testing = False
        self._filter = ""
        self._search_source = 0
        self._last_state = ""
        stored_columns = self.controller.settings.proxy_columns
        if not isinstance(stored_columns, list):
            stored_columns = COLUMN_ORDER[:-1]
        self._visible_columns = {str(item) for item in stored_columns if str(item) in COLUMN_ORDER}
        self._visible_columns.add("name")
        self._column_menu: Gtk.Menu | None = None
        self._center_pending = ""
        self._center_source = 0
        self._center_guard = 0.0
        self._hover_action = ""
        self._hover_y = -1.0
        self._pressed_action = ""
        self._pressed_y = -1.0
        self._pending_action: tuple[str, str] | None = None
        self.action_renderer: ActionCellRenderer | None = None

        self.group_combo = Gtk.ComboBoxText()
        self.group_combo.set_size_request(150, -1)
        self.group_combo.set_tooltip_text("选择代理分组")
        self.group_combo.connect("changed", self._on_group_changed)
        self.group_combo.set_no_show_all(True)
        self.group_combo.set_visible(False)
        self.header.lead.pack_start(self.group_combo, False, False, 0)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("搜索节点")
        self.search.set_width_chars(20)
        self.search.connect("search-changed", self._on_search_changed)
        self.header.lead.pack_start(self.search, False, False, 0)

        self.test_button = Gtk.Button(label="测速")
        self.test_button.set_tooltip_text("测试当前分组所有节点")
        self.test_button.connect("clicked", lambda _b: self._test_group())
        self.header.actions.pack_start(self.test_button, False, False, 0)
        self.header.actions.pack_start(
            IconButton("view-refresh-symbolic", "刷新代理", lambda: self.load(force=True)), False, False, 0
        )

        self.meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.group_meta = label("", "tiny-label")
        self.result_label = label("", "tiny-label")
        self.meta_row.pack_start(self.group_meta, True, True, 0)
        self.meta_row.pack_end(self.result_label, False, False, 0)
        self.meta_row.set_visible(False)
        self.body.pack_start(self.meta_row, False, False, 0)

        start_btn = Gtk.Button(label="启动内核")
        start_btn.get_style_context().add_class("primary")
        start_btn.connect("clicked", lambda _b: self._start_core())
        self.empty = EmptyState("network-vpn-symbolic", "内核尚未运行", "启动内核后即可查看代理分组与节点", action=start_btn)

        self.store = Gtk.ListStore(str, str, object, str, bool)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.tree = Gtk.TreeView(model=self.filter)
        self.tree.get_style_context().add_class("data-list")
        self.tree.set_enable_search(False)
        self.tree.set_show_expanders(False)
        self.tree.set_headers_visible(True)
        self.tree.set_grid_lines(Gtk.TreeViewGridLines.NONE)
        self.tree.set_activate_on_single_click(False)
        self.tree.connect("row-activated", self._on_row_activated)
        self.tree.connect("button-press-event", self._on_tree_button)
        self.tree.connect("button-release-event", self._on_tree_button_release)
        self.tree.connect("motion-notify-event", self._on_tree_motion)
        self.tree.connect("leave-notify-event", self._on_tree_leave)
        self.tree.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.tree.connect("realize", self._install_header_buttons)
        self.tree.connect("map", lambda *_args: GLib.idle_add(self._install_header_buttons))
        self.connect("map", lambda *_args: self._flush_center())
        self._build_columns()
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.add(self.tree)
        scroller.get_vadjustment().connect("value-changed", self._on_tree_scrolled)
        self.list_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_frame.get_style_context().add_class("list-card")
        self.list_frame.pack_start(scroller, True, True, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.add_named(self.list_frame, "list")
        self.stack.add_named(self.empty, "empty")
        self.stack.set_vexpand(True)
        self.body.pack_start(self.stack, True, True, 0)
        self._show_empty("内核尚未运行", "启动内核后即可查看代理分组与节点")

    def _build_columns(self) -> None:
        self.columns: dict[str, Gtk.TreeViewColumn] = {}

        mono_font = monospace_font()
        state_renderer = CheckCellRenderer(mono_font.copy())
        state_column = Gtk.TreeViewColumn("状态", state_renderer, text=3)
        state_column.set_fixed_width(42)
        state_column.set_resizable(False)
        self.tree.append_column(state_column)
        self.columns["status"] = state_column

        name_renderer = BaselineCellRendererText(mono_font.copy())
        name_renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        name_column = Gtk.TreeViewColumn("节点", name_renderer, text=0)
        name_column.set_expand(True)
        name_column.set_min_width(180)
        name_column.set_resizable(True)
        self.tree.append_column(name_column)
        self.columns["name"] = name_column

        type_renderer = BaselineCellRendererText(mono_font.copy())
        type_column = Gtk.TreeViewColumn("类型", type_renderer, text=1)
        type_column.set_fixed_width(100)
        type_column.set_resizable(True)
        self.tree.append_column(type_column)
        self.columns["type"] = type_column

        delay_renderer = BaselineCellRendererText(mono_font.copy())
        delay_renderer.set_property("xalign", 1.0)
        delay_column = Gtk.TreeViewColumn("延迟", delay_renderer)
        delay_column.set_fixed_width(82)
        delay_column.set_cell_data_func(delay_renderer, self._latency_cell)
        self.tree.append_column(delay_column)
        self.columns["delay"] = delay_column

        self._mono_renderers = [state_renderer, name_renderer, type_renderer, delay_renderer]

        action_renderer = ActionCellRenderer()
        self.action_renderer = action_renderer
        action_column = Gtk.TreeViewColumn("操作", action_renderer)
        action_column.set_fixed_width(ACTION_COLUMN_WIDTH)
        action_column.set_resizable(False)
        action_column.set_cell_data_func(action_renderer, self._action_cell)
        self.tree.append_column(action_column)
        self.columns["actions"] = action_column

        for column in self.columns.values():
            column.set_clickable(True)
            column.connect("notify::visible", lambda *_args: GLib.idle_add(self._install_header_buttons))
        self._apply_columns()

    def refresh_fonts(self) -> None:
        refresh_renderer_fonts(getattr(self, "_mono_renderers", []))
        self.tree.queue_resize()

    def _apply_columns(self) -> None:
        for column_id, column in self.columns.items():
            column.set_visible(column_id in self._visible_columns)

    def _install_header_buttons(self, *_args) -> bool:
        for column in self.columns.values():
            button = column.get_button()
            if button is None or getattr(button, "_catmeta_header_menu", False):
                continue
            button._catmeta_header_menu = True
            button.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            button.connect("button-press-event", self._on_header_button)
        return False

    def _on_header_button(self, _button: Gtk.Button, event: Gdk.EventButton) -> bool:
        if event.button != 3:
            return False
        self._show_column_menu(event)
        return True

    def _show_column_menu(self, event: Gdk.EventButton) -> None:
        menu = Gtk.Menu()
        for column_id in COLUMN_ORDER:
            item = Gtk.CheckMenuItem(label=COLUMN_LABELS[column_id])
            item.set_active(column_id in self._visible_columns)
            if column_id == "name":
                item.set_sensitive(False)
            item.connect("toggled", self._on_column_toggled, column_id)
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        self._column_menu = menu

    def _on_column_toggled(self, item: Gtk.CheckMenuItem, column_id: str) -> None:
        if column_id == "name":
            return
        if item.get_active():
            self._visible_columns.add(column_id)
        else:
            self._visible_columns.discard(column_id)
        self._apply_columns()
        ordered = [name for name in COLUMN_ORDER if name in self._visible_columns]
        self.controller.settings.set("proxy_columns", ordered)

    def _action_cell(self, _column, cell, model, tree_iter, _data) -> None:
        cell.connected = bool(model.get_value(tree_iter, 4))

    def _latency_cell(self, _column, cell, model, tree_iter, _data) -> None:
        delay = model.get_value(tree_iter, 2)
        if delay is None:
            cell.set_property("text", "")
            cell.set_property("foreground-set", False)
            return
        cell.set_property("foreground-set", True)
        cell.set_property("foreground", _latency_color(delay))
        cell.set_property("text", latency_text(delay))

    # ---------------- 展示 ----------------
    def on_show(self) -> None:
        super().on_show()
        self._set_action_feedback()
        self.on_state(self.controller.state)
        GLib.idle_add(self._install_header_buttons)
        if self.controller.running and not self.data:
            self.load()
        elif self._center_pending:
            GLib.idle_add(self._flush_center)
        elif self.data and self.group:
            # 页面重新显示时，仅当当前节点不在视野内才滚动；
            # 初次加载的居中定位由 _center_pending 单独处理。
            now = str(self.data.get(self.group, {}).get("now") or "")
            if now:
                GLib.idle_add(self._select_current, now)

    def on_current_node(self, group: str, name: str) -> None:
        """内核侧当前节点变化时只更新勾选/按钮状态，不改变滚动位置。"""
        if not name or group != self.group:
            return
        info = self.data.get(group)
        if isinstance(info, dict):
            info["now"] = name
        self._mark_current(name)
        if not self.get_mapped():
            self._center_pending = name

    def on_state(self, state: str, error: str = "") -> None:
        self._last_state = state
        if state != "running" and not self.data:
            self.group_combo.set_visible(False)
        if state == "running":
            if self.data:
                self.stack.set_visible_child_name("list")
            else:
                self._show_empty("正在读取代理…", "请稍候")
            if not self.data:
                self.load()
            self.search.set_sensitive(True)
            self.test_button.set_sensitive(not self._testing)
            self.group_combo.set_sensitive(True)
        elif state == "starting":
            self._show_empty("正在启动内核…", "请稍候，正在校验配置并连接控制器")
            self.search.set_sensitive(False)
            self.test_button.set_sensitive(False)
            self.group_combo.set_sensitive(False)
        elif state == "error":
            self._show_empty("内核运行异常", "请查看日志并重新启动内核")
            self.search.set_sensitive(False)
            self.test_button.set_sensitive(False)
            self.group_combo.set_sensitive(False)
        else:
            self._show_empty("内核尚未运行", "启动内核后即可查看代理分组与节点")
            self.search.set_sensitive(False)
            self.test_button.set_sensitive(False)
            self.group_combo.set_sensitive(False)

    def _show_empty(self, title: str, subtitle: str) -> None:
        self.empty.set(title=title, subtitle=subtitle)
        self.stack.set_visible_child_name("empty")

    def load(self, force: bool = False) -> None:
        if self._loading or not self.controller.running:
            return
        self._loading = True
        self.result_label.set_text("")
        run_async(self.controller.proxies, on_success=self._loaded, on_error=self._load_error, name="proxies")

    def _loaded(self, data: dict[str, Any]) -> None:
        self._loading = False
        if not self.controller.running:
            return
        if not data:
            self._load_error(Exception("控制器没有返回代理数据"))
            return
        self.data = data
        groups = [
            name
            for name, info in data.items()
            if isinstance(info, dict) and info.get("type") in GROUP_TYPES and isinstance(info.get("all"), list) and name != "GLOBAL"
        ]
        # 订阅里的组顺序通常按 yaml 排列，主选择组往往不在第一项。
        # 把「选择节点 / 节点选择」提到最前，其余组保持订阅原顺序。
        groups.sort(key=lambda name: 0 if ("选择节点" in name or "节点选择" in name) else 1)
        self.groups = groups
        if self.group not in groups:
            self.group = next((g for g in groups if data[g].get("type") == "Selector"), groups[0] if groups else "")
        self._populate_group_combo()
        self.group_combo.set_no_show_all(False)
        self.group_combo.set_visible(bool(groups))
        if not groups:
            self._show_empty("没有代理组", "当前配置中未定义 proxy-groups")
            self.meta_row.set_visible(False)
            self.test_button.set_sensitive(False)
            return
        self.meta_row.set_visible(True)
        self.stack.set_visible_child_name("list")
        self._rebuild_store()
        self.test_button.set_sensitive(True)
        self.search.set_sensitive(True)
        self.group_combo.set_sensitive(True)

    def _load_error(self, exc: Exception) -> None:
        self._loading = False
        if not self.data:
            self._show_empty("无法读取代理", "请检查内核状态或查看日志")
        self.toast(f"读取代理失败：{exc}", error=True)

    def _populate_group_combo(self) -> None:
        self.group_combo.handler_block_by_func(self._on_group_changed)
        self.group_combo.remove_all()
        for name in self.groups:
            self.group_combo.append(name, name)
        if self.group:
            self.group_combo.set_active_id(self.group)
        self.group_combo.handler_unblock_by_func(self._on_group_changed)

    def _on_group_changed(self, combo: Gtk.ComboBoxText) -> None:
        name = combo.get_active_id()
        if not name or name == self.group:
            return
        self.group = name
        self.result_label.set_text("")
        self._rebuild_store()

    def _update_meta(self) -> None:
        if not self.group:
            self.group_meta.set_text("")
            return
        info = self.data.get(self.group, {})
        now = str(info.get("now") or "未选择")
        nodes = info.get("all") if isinstance(info.get("all"), list) else []
        kind = TYPE_LABELS.get(str(info.get("type")), str(info.get("type")))
        self.group_meta.set_text(f"{kind} · {len(nodes)} 个节点 · 当前 {now}")
        self.meta_row.set_visible(True)

    def _node_delay(self, name: str) -> Any:
        if name in self.delays:
            return self.delays[name]
        info = self.data.get(name, {})
        history = info.get("history") if isinstance(info.get("history"), list) else []
        if history and isinstance(history[-1], dict):
            return history[-1].get("delay")
        extra = info.get("extra") if isinstance(info.get("extra"), dict) else {}
        for entry in extra.values():
            if isinstance(entry, dict) and isinstance(entry.get("history"), list) and entry["history"]:
                return entry["history"][-1].get("delay")
        return None

    def _node_type(self, name: str) -> str:
        info = self.data.get(name)
        if not isinstance(info, dict):
            return "节点"
        raw = str(info.get("type") or "节点")
        return TYPE_LABELS.get(raw, raw)

    def _rebuild_store(self) -> None:
        self._set_action_feedback()
        info = self.data.get(self.group, {})
        now = str(info.get("now") or "")
        self.store.clear()
        for name in info.get("all") or []:
            name = str(name)
            delay = self._node_delay(name)
            if delay is not None:
                try:
                    delay = max(0, int(delay))
                except (TypeError, ValueError):
                    delay = None
            current = name == now
            self.store.append([name, self._node_type(name), delay, "✓" if current else "", current])
        self.filter.refilter()
        self._update_meta()
        self._center_pending = now
        if self.get_mapped():
            GLib.idle_add(self._flush_center)
        visible = self.filter.iter_n_children(None)
        if self.search.get_text().strip():
            self.result_label.set_text(f"找到 {visible} 个节点")
        else:
            self.result_label.set_text("")

    def _select_current(self, now: str) -> None:
        """选中当前节点；只有它不在视野内时才滚动列表。"""
        if not now or time.monotonic() < self._center_guard:
            return
        tree_iter = self.filter.get_iter_first()
        while tree_iter:
            if str(self.filter.get_value(tree_iter, 0)) == now:
                self.tree.get_selection().select_iter(tree_iter)
                path = self.filter.get_path(tree_iter)
                if not self._row_is_visible(path):
                    self.tree.scroll_to_cell(path, None, False, 0.5, 0.5)
                return
            tree_iter = self.filter.iter_next(tree_iter)

    def _flush_center(self) -> bool:
        if not self._center_pending or not self.get_mapped() or self._center_source:
            return False
        # 等页面完成一次尺寸分配后再滚动，否则 scroll_to_cell 可能退化为
        # “最小滚动”，把当前节点放到列表底部。
        self._center_source = GLib.timeout_add(150, self._apply_center)
        return False

    def _apply_center(self) -> bool:
        self._center_source = 0
        if not self._center_pending or not self.get_mapped():
            return False
        now = self._center_pending
        self._center_pending = ""
        self._center_guard = time.monotonic() + 0.4
        self._center_current(now)
        return False

    def _center_current(self, now: str) -> None:
        """初次显示时把当前节点放在列表中部；首尾位置由 GTK 自动收敛。"""
        if not now:
            return
        tree_iter = self.filter.get_iter_first()
        while tree_iter:
            if str(self.filter.get_value(tree_iter, 0)) == now:
                self.tree.get_selection().select_iter(tree_iter)
                path = self.filter.get_path(tree_iter)
                self.tree.scroll_to_cell(path, None, True, 0.5, 0.5)
                return
            tree_iter = self.filter.iter_next(tree_iter)

    def _row_is_visible(self, path) -> bool:
        try:
            visible = self.tree.get_visible_rect()
            if visible.height <= 0:
                return False
            column = self.tree.get_column(0)
            area = self.tree.get_background_area(path, column)
            return area.y + area.height > visible.y and area.y < visible.y + visible.height
        except Exception:
            return False

    def _mark_current(self, name: str) -> None:
        """只更新当前标记，不重建模型，避免点击节点时列表跳回顶部。"""
        for row in self.store:
            current = str(row[0]) == name
            row[3] = "✓" if current else ""
            row[4] = current
        self._update_meta()

    def _visible(self, model: Gtk.TreeModel, tree_iter: Gtk.TreeIter, _data: Any) -> bool:
        if not self._filter:
            return True
        return self._filter.lower() in str(model.get_value(tree_iter, 0)).lower()

    # ---------------- 操作 ----------------
    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._filter = entry.get_text().strip()
        if self._search_source:
            GLib.source_remove(self._search_source)
        self._search_source = GLib.timeout_add(120, self._apply_filter)

    def _apply_filter(self) -> bool:
        self._search_source = 0
        if self.data and self.group:
            self.filter.refilter()
            visible = self.filter.iter_n_children(None)
            self.result_label.set_text(f"找到 {visible} 个节点" if self._filter else "")
        return False

    def _path_name(self, path) -> str:
        try:
            tree_iter = self.filter.get_iter(path)
            return str(self.filter.get_value(tree_iter, 0))
        except Exception:
            return ""

    def _action_at(self, cell_x: float, cell_width: float) -> str:
        total = ACTION_BUTTON_WIDTH * 2 + ACTION_BUTTON_GAP
        left = max(0.0, (cell_width - total) / 2)
        if left <= cell_x <= left + ACTION_BUTTON_WIDTH:
            return "connect"
        if left + ACTION_BUTTON_WIDTH + ACTION_BUTTON_GAP <= cell_x <= left + total:
            return "test"
        return ""

    def _set_action_feedback(self, hover_action: str = "", hover_y: float = -1.0,
                             pressed_action: str = "", pressed_y: float = -1.0) -> None:
        state = (hover_action, hover_y, pressed_action, pressed_y)
        if state == (self._hover_action, self._hover_y, self._pressed_action, self._pressed_y):
            return
        self._hover_action, self._hover_y, self._pressed_action, self._pressed_y = state
        if self.action_renderer is not None:
            self.action_renderer.set_feedback(hover_action, hover_y, pressed_action, pressed_y)
        self.tree.queue_draw()

    def _action_under_pointer(self, tree: Gtk.TreeView, x: float, y: float):
        path_info = tree.get_path_at_pos(int(x), int(y))
        if not path_info:
            return "", -1.0, None
        path, column, cell_x, _cell_y = path_info
        if column is not self.columns.get("actions"):
            return "", -1.0, path
        area = tree.get_cell_area(path, column)
        action = self._action_at(cell_x, area.width or column.get_width())
        return action, float(area.y), path

    def _on_tree_motion(self, tree: Gtk.TreeView, event) -> bool:
        action, row_y, _path = self._action_under_pointer(tree, event.x, event.y)
        pressed_action = self._pressed_action
        pressed_y = self._pressed_y
        if pressed_action and (action != pressed_action or abs(row_y - pressed_y) >= 1.0):
            # 按住后拖出按钮，取消按下态；松开时也不会触发。
            pressed_action, pressed_y = "", -1.0
        self._set_action_feedback(action, row_y, pressed_action, pressed_y)
        return False

    def _on_tree_leave(self, _tree: Gtk.TreeView, _event) -> bool:
        self._set_action_feedback()
        return False

    def _on_tree_scrolled(self, _adjustment) -> None:
        if self._hover_action or self._pressed_action:
            self._set_action_feedback()

    def _on_tree_button(self, tree: Gtk.TreeView, event) -> bool:
        path_info = tree.get_path_at_pos(int(event.x), int(event.y))
        if not path_info:
            return False
        path, column, cell_x, _cell_y = path_info
        name = self._path_name(path)
        if not name:
            return False
        if event.button == 3:
            tree.get_selection().select_path(path)
            menu = Gtk.Menu()
            test_item = Gtk.MenuItem(label="测试延迟")
            test_item.connect("activate", lambda _i: self._test_single(name))
            menu.append(test_item)
            copy_item = Gtk.MenuItem(label="复制节点名称")
            copy_item.connect("activate", lambda _i: self._copy_name(name))
            menu.append(copy_item)
            menu.show_all()
            menu.popup_at_pointer(event)
            return True
        if event.button == 1 and column is self.columns.get("actions"):
            if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
                self._pending_action = None
                self._set_action_feedback()
                return True
            area = tree.get_cell_area(path, column)
            action = self._action_at(cell_x, area.width or column.get_width())
            if not action:
                return False
            tree.get_selection().select_path(path)
            current = str(self.data.get(self.group, {}).get("now") or "")
            if action == "connect" and name == current:
                return True
            self._pending_action = (name, action)
            self._set_action_feedback(action, float(area.y), action, float(area.y))
            return True
        return False

    def _on_tree_button_release(self, tree: Gtk.TreeView, event) -> bool:
        if event.button != 1:
            return False
        pending = self._pending_action
        self._pending_action = None
        action, row_y, path = self._action_under_pointer(tree, event.x, event.y)
        name = self._path_name(path) if path is not None else ""
        self._set_action_feedback(action, row_y)
        if not pending or not path or action != pending[1] or name != pending[0]:
            return bool(pending)
        current = str(self.data.get(self.group, {}).get("now") or "")
        if action == "connect" and name == current:
            return True
        if action == "connect":
            self._activate_node(name)
        else:
            self._test_single(name)
        return True

    def _on_row_activated(self, _tree, path, column) -> None:
        if column is self.columns.get("actions"):
            return
        self._activate_node(self._path_name(path))

    def _activate_node(self, name: str) -> None:
        if not name or not self.group:
            return
        info = self.data.get(self.group, {})
        if str(info.get("type")) != "Selector":
            self.toast("该分组为自动选择，无法手动切换节点")
            return
        if str(info.get("now") or "") == name:
            return
        if self.controller.state != "running":
            self.toast("内核未运行", error=True)
            return
        self.toast(f"正在切换到 {name}…")
        group = self.group

        def done(_r) -> None:
            info = self.data.get(group)
            if isinstance(info, dict):
                info["now"] = name
            if group == self.group:
                self._mark_current(name)
            self.toast(f"已切换到 {name}", success=True)

        run_async(
            lambda: self.controller.select_proxy(group, name),
            on_success=done,
            on_error=lambda exc: self.toast(str(exc), error=True),
            name="select-proxy",
        )

    def _copy_name(self, name: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(name, -1)
        clipboard.store()
        self.toast("节点名称已复制")

    def _test_group(self) -> None:
        if self._testing or not self.controller.running or not self.group:
            return
        self._testing = True
        self.test_button.set_sensitive(False)
        self.test_button.set_label("测速中…")
        group = self.group
        run_async(
            lambda: self.controller.test_group(group),
            on_success=lambda result: self._test_done(group, result),
            on_error=self._test_failed,
            name="group-delay",
        )

    def _test_done(self, group: str, result: dict[str, int]) -> None:
        self._testing = False
        self.test_button.set_sensitive(True)
        self.test_button.set_label("测速")
        for name, delay in result.items():
            try:
                self.delays[str(name)] = max(0, int(delay))
            except (TypeError, ValueError):
                continue
        if group == self.group:
            self._update_delays(result)
        self.result_label.set_text(f"测速完成 · {len(result)} 个节点")
        self.toast(f"延迟测试完成，共 {len(result)} 个节点", success=True)

    def _update_delays(self, result: dict[str, int]) -> None:
        for row in self.store:
            name = str(row[0])
            if name not in result:
                continue
            try:
                row[2] = max(0, int(result[name]))
            except (TypeError, ValueError):
                continue

    def _test_failed(self, exc: Exception) -> None:
        self._testing = False
        self.test_button.set_sensitive(True)
        self.test_button.set_label("测速")
        self.toast(f"测速失败：{exc}", error=True)

    def _test_single(self, name: str) -> None:
        if not self.controller.running:
            self.toast("内核未运行", error=True)
            return
        self.toast(f"正在测试 {name}…")
        run_async(
            lambda: self.controller.test_proxy(name),
            on_success=lambda delay: self._single_done(name, delay),
            on_error=lambda exc: self.toast(str(exc), error=True),
            name="proxy-delay",
        )

    def _single_done(self, name: str, delay: int) -> None:
        try:
            value = max(0, int(delay))
        except (TypeError, ValueError):
            value = 0
        self.delays[name] = value
        for row in self.store:
            if str(row[0]) == name:
                row[2] = value
                break
        self.toast(f"{name}：{latency_text(value)}", success=value > 0)

    def _start_core(self) -> None:
        self.toast("正在启动内核…")
        run_async(self.controller.start, on_success=lambda _r: self.toast("内核已启动", success=True), on_error=lambda exc: self.toast(str(exc), error=True), name="start-proxies")

    def on_profiles(self) -> None:
        if self.controller.running:
            self.data = {}
            self.group = ""
            self.load(force=True)
