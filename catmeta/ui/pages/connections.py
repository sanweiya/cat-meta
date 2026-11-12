"""实时连接页。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, Pango

from ...async_utils import run_async
from ...controller import Controller
from ...util import format_bytes, format_duration
from ..dialogs import confirm
from ..widgets.common import BasePage, EmptyState, IconButton, icon, label


class ConnectionsPage(BasePage):
    def __init__(self, controller: Controller, toast: Callable[..., None], on_navigate: Callable[[str], None]):
        super().__init__("连接", "查看实时网络连接、代理链与流量", scroll=False)
        self.controller = controller
        self.toast = toast
        self.on_navigate = on_navigate
        self._data: dict[str, Any] = {}
        self._rows: list[dict[str, Any]] = []
        self._paused = False
        self._dirty = True
        self._last_state = ""

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("搜索主机、进程或规则")
        self.search.set_width_chars(20)
        self.search.get_style_context().add_class("search-entry")
        self.search.connect("search-changed", lambda _e: self._render())
        self.header.lead.pack_start(self.search, False, False, 0)

        pause_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pause_box.pack_start(label("暂停", "tiny-label"), False, False, 0)
        self.pause_switch = Gtk.Switch()
        self.pause_switch.connect("notify::active", self._on_pause)
        pause_box.pack_start(self.pause_switch, False, False, 0)
        self.header.actions.pack_start(pause_box, False, False, 0)

        self.close_all_button = Gtk.Button(label="全部断开")
        self.close_all_button.get_style_context().add_class("pill")
        self.close_all_button.get_style_context().add_class("destructive")
        self.close_all_button.connect("clicked", lambda _b: self._close_all())
        self.header.actions.pack_start(self.close_all_button, False, False, 0)

        summary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.count_badge = label("0 个连接", "badge badge-accent")
        self.total_down = label("↓ 0 B", "tiny-label")
        self.total_up = label("↑ 0 B", "tiny-label")
        self.paused_badge = label("已暂停", "badge badge-orange")
        self.paused_badge.set_no_show_all(True)
        self.paused_badge.set_visible(False)
        summary.pack_start(self.count_badge, False, False, 0)
        summary.pack_start(self.total_down, False, False, 0)
        summary.pack_start(self.total_up, False, False, 0)
        summary.pack_end(self.paused_badge, False, False, 0)
        self.body.pack_start(summary, False, False, 0)

        self.store = Gtk.ListStore(str, str, str, str, str, str, int, int, float)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.tree = Gtk.TreeView(model=self.filter)
        self.tree.set_headers_visible(True)
        self.tree.set_enable_search(False)
        self.tree.set_show_expanders(False)
        self.tree.set_grid_lines(Gtk.TreeViewGridLines.NONE)
        self.tree.connect("row-activated", self._on_row_activated)
        self.tree.connect("button-press-event", self._on_tree_button)
        self.tree.get_style_context().add_class("connections-tree")
        self._build_columns()
        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        tree_scroll.set_vexpand(True)
        tree_scroll.add(self.tree)
        list_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_card.get_style_context().add_class("list-card")
        list_card.pack_start(tree_scroll, True, True, 0)

        empty_action = Gtk.Button(label="前往概览")
        empty_action.get_style_context().add_class("pill")
        empty_action.connect("clicked", lambda _b: on_navigate("home"))
        self.empty = EmptyState("network-transmit-receive-symbolic", "暂无活动连接", "产生网络流量后，这里会实时显示连接明细", action=empty_action)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(list_card, "list")
        self.stack.add_named(self.empty, "empty")
        self.stack.set_vexpand(True)
        self.body.pack_start(self.stack, True, True, 0)
        self._render()

    def _build_columns(self) -> None:
        def add_text(title: str, index: int, expand: bool = False, width: int | None = None, align: float = 0.0, css: str | None = None):
            renderer = Gtk.CellRendererText()
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            renderer.set_property("xalign", align)
            if css:
                renderer.set_property("cell-background", None)
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_expand(expand)
            column.set_resizable(True)
            if width:
                column.set_min_width(width)
                column.set_fixed_width(width)
            column.set_sort_column_id(index)
            self.tree.append_column(column)
            return column, renderer

        self.tree.append_column(self._badge_column("主机", 1, expand=True, width=230))
        self.tree.append_column(self._badge_column("网络", 2, width=88))
        self.tree.append_column(self._badge_column("规则", 3, width=160))
        self.tree.append_column(self._badge_column("代理链", 4, expand=True, width=170))

        up_renderer = Gtk.CellRendererText()
        up_renderer.set_property("xalign", 1.0)
        up_col = Gtk.TreeViewColumn("上传", up_renderer)
        up_col.set_cell_data_func(up_renderer, lambda _c, cell, model, it, _d: cell.set_property("text", format_bytes(model.get_value(it, 6))))
        up_col.set_sort_column_id(6)
        up_col.set_fixed_width(82)
        self.tree.append_column(up_col)

        down_renderer = Gtk.CellRendererText()
        down_renderer.set_property("xalign", 1.0)
        down_col = Gtk.TreeViewColumn("下载", down_renderer)
        down_col.set_cell_data_func(down_renderer, lambda _c, cell, model, it, _d: cell.set_property("text", format_bytes(model.get_value(it, 7))))
        down_col.set_sort_column_id(7)
        down_col.set_fixed_width(82)
        self.tree.append_column(down_col)

        time_renderer = Gtk.CellRendererText()
        time_renderer.set_property("xalign", 1.0)
        time_col = Gtk.TreeViewColumn("持续", time_renderer)
        time_col.set_cell_data_func(time_renderer, lambda _c, cell, model, it, _d: cell.set_property("text", format_duration(time.time() - model.get_value(it, 8))))
        time_col.set_sort_column_id(8)
        time_col.set_fixed_width(76)
        self.tree.append_column(time_col)

    def _badge_column(self, title: str, index: int, *, expand: bool = False, width: int | None = None) -> Gtk.TreeViewColumn:
        renderer = Gtk.CellRendererText()
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        column = Gtk.TreeViewColumn(title, renderer, text=index)
        column.set_expand(expand)
        column.set_resizable(True)
        column.set_sort_column_id(index)
        if width:
            column.set_min_width(width)
            column.set_fixed_width(width)
        return column

    # ---------------- 数据 ----------------
    def _extract(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        conns = data.get("connections") if isinstance(data.get("connections"), list) else []
        rows = []
        for conn in conns:
            if not isinstance(conn, dict):
                continue
            meta = conn.get("metadata") if isinstance(conn.get("metadata"), dict) else {}
            host = str(meta.get("host") or meta.get("destinationIP") or "未知主机")
            process = str(meta.get("process") or "")
            network = str(meta.get("network") or "tcp").upper()
            rule = str(conn.get("rule") or "—")
            payload = str(conn.get("rulePayload") or "")
            if payload:
                rule = f"{rule} · {payload}"
            chains = conn.get("chains") if isinstance(conn.get("chains"), list) else []
            chain = " → ".join(str(c) for c in reversed(chains)) or "DIRECT"
            start = time.time()
            raw_start = conn.get("start")
            if isinstance(raw_start, (int, float)):
                start = float(raw_start)
            elif isinstance(raw_start, str):
                try:
                    start = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    start = time.time()
            rows.append(
                {
                    "id": str(conn.get("id") or ""),
                    "host": host,
                    "network": network,
                    "rule": rule,
                    "chain": chain,
                    "up": int(conn.get("upload") or 0),
                    "down": int(conn.get("download") or 0),
                    "start": start,
                    "process": process,
                    "meta": meta,
                    "raw": conn,
                }
            )
        return rows

    def on_connections(self, data: dict[str, Any]) -> None:
        if self._paused:
            return
        self._data = data
        self._dirty = True
        if self.get_mapped():
            self._rows = self._extract(data)
            self._render()

    def on_state(self, state: str, error: str = "") -> None:
        self._last_state = state
        self.close_all_button.set_sensitive(state == "running")
        if state != "running":
            self._rows = []
            self._data = {}
            self._dirty = True
            if self.get_mapped():
                self._render()

    def on_show(self) -> None:
        super().on_show()
        if self._dirty:
            self._rows = self._extract(self._data)
            self._render()

    def _render(self) -> None:
        if not self.get_mapped() and not self._dirty:
            return
        self._dirty = False
        query = self.search.get_text().strip().lower()
        selected = self._selected_id()
        self.filter.refilter()
        self.store.clear()
        for row in self._rows:
            self.store.append(
                [
                    row["id"],
                    row["host"],
                    row["network"],
                    row["rule"],
                    row["chain"],
                    row["process"],
                    row["up"],
                    row["down"],
                    row["start"],
                ]
            )
        self.filter.refilter()
        if selected:
            self._select_id(selected)
        count = len(self._rows)
        filtered = self.filter.iter_n_children(None)
        self.count_badge.set_text(f"{count} 个连接")
        try:
            self.total_down.set_text(f"↓ {format_bytes(int(self._data.get('downloadTotal') or 0))}")
            self.total_up.set_text(f"↑ {format_bytes(int(self._data.get('uploadTotal') or 0))}")
        except (TypeError, ValueError):
            pass
        self.stack.set_visible_child_name("list" if filtered else "empty")
        if filtered:
            self.empty.set(title="暂无活动连接", subtitle="产生网络流量后，这里会实时显示连接明细")
        elif query:
            self.empty.set(title="没有匹配的连接", subtitle="试试其他关键词，或清空搜索框")
        else:
            self.empty.set(title="暂无活动连接", subtitle="产生网络流量后，这里会实时显示连接明细")
        if query and not filtered:
            self.count_badge.set_text(f"{count} 个连接 · 0 匹配")

    def _visible(self, model: Gtk.TreeModel, tree_iter: Gtk.TreeIter, _data: Any) -> bool:
        query = self.search.get_text().strip().lower()
        if not query:
            return True
        values = [model.get_value(tree_iter, i) for i in (1, 2, 3, 4, 5)]
        return any(query in str(value).lower() for value in values)

    def _selected_id(self) -> str:
        model, tree_iter = self.tree.get_selection().get_selected()
        if tree_iter is None:
            return ""
        try:
            return str(self.filter.get_value(tree_iter, 0))
        except Exception:
            return ""

    def _select_id(self, connection_id: str) -> None:
        for row in self.filter:
            if row[0] == connection_id:
                self.tree.get_selection().select_iter(row.iter)
                return

    def _row_for_id(self, connection_id: str) -> dict[str, Any] | None:
        return next((r for r in self._rows if r["id"] == connection_id), None)

    # ---------------- 操作 ----------------
    def _on_pause(self, switch: Gtk.Switch, _param) -> None:
        self._paused = switch.get_active()
        self.paused_badge.set_visible(self._paused)
        if not self._paused:
            self._render()
        else:
            self._dirty = True

    def _on_row_activated(self, _tree, _path, _column) -> None:
        cid = self._selected_id()
        row = self._row_for_id(cid)
        if row:
            self._show_detail(row)

    def _on_tree_button(self, _tree, event) -> bool:
        if event.button != 3:
            return False
        path_info = self.tree.get_path_at_pos(int(event.x), int(event.y))
        if not path_info:
            return False
        self.tree.get_selection().select_path(path_info[0])
        cid = self._selected_id()
        row = self._row_for_id(cid)
        if not row:
            return False
        menu = Gtk.Menu()
        detail = Gtk.MenuItem(label="查看详情")
        detail.connect("activate", lambda _i: self._show_detail(row))
        menu.append(detail)
        copy = Gtk.MenuItem(label="复制主机")
        copy.connect("activate", lambda _i: self._copy(row["host"]))
        menu.append(copy)
        menu.append(Gtk.SeparatorMenuItem())
        close = Gtk.MenuItem(label="关闭此连接")
        close.connect("activate", lambda _i: self._close_one(row))
        menu.append(close)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _close_one(self, row: dict[str, Any]) -> None:
        run_async(
            lambda: self.controller.close_connection(row["id"]),
            on_success=lambda _r: self.toast("连接已关闭", success=True),
            on_error=lambda exc: self.toast(str(exc), error=True),
            name="close-connection",
        )

    def _close_all(self) -> None:
        if not self.controller.running:
            return
        confirm(
            self.get_toplevel(),
            "断开所有连接？",
            "所有当前活动的网络连接都会立即关闭。",
            self._do_close_all,
            destructive=True,
            confirm_label="全部断开",
        )

    def _do_close_all(self) -> None:
        run_async(
            self.controller.close_all_connections,
            on_success=lambda _r: self.toast("已断开所有连接", success=True),
            on_error=lambda exc: self.toast(str(exc), error=True),
            name="close-all",
        )

    def _copy(self, text: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()

    def _show_detail(self, row: dict[str, Any]) -> None:
        dialog = Gtk.Dialog(title="连接详情", transient_for=self.get_toplevel(), flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT)
        dialog.set_default_size(620, 560)
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        dialog.connect("response", lambda d, _r: d.destroy())
        content = dialog.get_content_area()
        content.set_spacing(9)
        content.set_margin_top(12)
        content.set_margin_bottom(8)
        content.set_margin_start(14)
        content.set_margin_end(14)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.pack_start(icon("network-transmit-receive-symbolic", 18), False, False, 0)
        head.pack_start(label(row["host"], "section-title", ellipsize=True, max_width=50), False, False, 0)
        content.pack_start(head, False, False, 0)
        meta_grid = Gtk.Grid(column_spacing=16, row_spacing=5)
        pairs = [
            ("连接 ID", row["id"]),
            ("网络", row["network"]),
            ("代理链", row["chain"]),
            ("规则", row["rule"]),
            ("上传", format_bytes(row["up"])),
            ("下载", format_bytes(row["down"])),
            ("持续", format_duration(time.time() - row["start"])),
        ]
        meta = row.get("meta") or {}
        for key in ("sourceIP", "sourcePort", "destinationIP", "destinationPort", "process", "processPath", "dnsMode"):
            if meta.get(key) not in (None, "", 0):
                pairs.append((key, str(meta[key])))
        for index, (key, value) in enumerate(pairs):
            meta_grid.attach(label(key, "tiny-label"), 0, index, 1, 1)
            meta_grid.attach(label(str(value), "mono", ellipsize=True, max_width=60, selectable=True), 1, index, 1, 1)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.add(meta_grid)
        content.pack_start(scroller, True, True, 0)
        raw_header = label("原始数据", "section-title")
        content.pack_start(raw_header, False, False, 0)
        raw_scroller = Gtk.ScrolledWindow()
        raw_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        raw_scroller.set_vexpand(True)
        raw = Gtk.TextView()
        raw.set_monospace(True)
        raw.set_editable(False)
        raw.get_buffer().set_text(json.dumps(row.get("raw", {}), ensure_ascii=False, indent=2))
        raw_scroller.add(raw)
        content.pack_start(raw_scroller, True, True, 0)
        dialog.show_all()
