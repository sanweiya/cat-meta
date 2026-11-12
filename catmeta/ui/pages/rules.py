"""规则页。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango

from ...async_utils import run_async
from ...controller import Controller
from ...profiles import ProfileError
from ..widgets.common import BasePage, EmptyState, IconButton, label

RULE_TYPES = {
    "Domain": "DOMAIN",
    "DomainSuffix": "DOMAIN-SUFFIX",
    "DomainKeyword": "DOMAIN-KEYWORD",
    "DomainRegex": "DOMAIN-REGEX",
    "GeoSite": "GEOSITE",
    "GeoIP": "GEOIP",
    "SrcGeoIP": "SRC-GEOIP",
    "IPCIDR": "IP-CIDR",
    "IPCIDR6": "IP-CIDR6",
    "SrcIPCIDR": "SRC-IP-CIDR",
    "SrcIPCIDR6": "SRC-IP-CIDR6",
    "Match": "MATCH",
    "Process": "PROCESS-NAME",
    "ProcessPath": "PROCESS-PATH",
    "RuleSet": "RULE-SET",
    "SubRules": "SUB-RULES",
    "Network": "NETWORK",
    "DstPort": "DST-PORT",
    "SrcPort": "SRC-PORT",
    "InboundPort": "IN-PORT",
    "InboundName": "IN-NAME",
    "InboundUser": "IN-USER",
    "Uid": "UID",
    "AndroidPackage": "ANDROID-PACKAGE",
}


def rule_type_label(raw: str) -> str:
    if raw in RULE_TYPES:
        return RULE_TYPES[raw]
    return raw.upper()


class RulesPage(BasePage):
    def __init__(self, controller: Controller, toast: Callable[..., None]):
        super().__init__("规则", "查看当前生效的分流规则，按顺序匹配", scroll=False)
        self.controller = controller
        self.toast = toast
        self._rules: list[dict[str, Any]] = []
        self._loading = False
        self._local = False
        self._dirty = True

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("搜索类型、内容或策略")
        self.search.set_width_chars(20)
        self.search.get_style_context().add_class("search-entry")
        self.search.connect("search-changed", lambda _e: self._render())
        self.header.lead.pack_start(self.search, False, False, 0)
        self.header.actions.pack_start(IconButton("view-refresh-symbolic", "刷新规则", lambda: self.load(force=True)), False, False, 0)

        summary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.count_label = label("0 条规则", "badge badge-accent")
        self.source_label = label("", "tiny-label")
        summary.pack_start(self.count_label, False, False, 0)
        summary.pack_start(self.source_label, False, False, 0)
        summary.pack_end(label("首条命中生效", "tiny-label"), False, False, 0)
        self.body.pack_start(summary, False, False, 0)

        self.store = Gtk.ListStore(int, str, str, str, str)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.tree = Gtk.TreeView(model=self.filter)
        self.tree.set_enable_search(False)
        self.tree.set_show_expanders(False)
        self.tree.set_grid_lines(Gtk.TreeViewGridLines.NONE)
        self.tree.connect("button-press-event", self._on_button)
        self._build_columns()
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.add(self.tree)
        self.list_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_card.get_style_context().add_class("list-card")
        self.list_card.pack_start(scroll, True, True, 0)

        self.empty = EmptyState("view-list-symbolic", "暂无规则", "启动内核后即可读取当前生效的规则")
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.list_card, "list")
        self.stack.add_named(self.empty, "empty")
        self.stack.set_vexpand(True)
        self.body.pack_start(self.stack, True, True, 0)
        self._render()

    def _build_columns(self) -> None:
        num = Gtk.CellRendererText()
        num.set_property("xalign", 1.0)
        num_col = Gtk.TreeViewColumn("#", num, text=0)
        num_col.set_fixed_width(48)
        num_col.set_sort_column_id(0)
        self.tree.append_column(num_col)

        type_col = Gtk.TreeViewColumn("类型", Gtk.CellRendererText(), text=1)
        type_col.set_fixed_width(180)
        type_col.set_resizable(True)
        type_col.set_sort_column_id(1)
        self.tree.append_column(type_col)

        payload_col = Gtk.TreeViewColumn("匹配内容", Gtk.CellRendererText(), text=2)
        payload_col.set_expand(True)
        payload_col.set_min_width(260)
        payload_col.set_resizable(True)
        payload_col.set_sort_column_id(2)
        self.tree.append_column(payload_col)

        proxy_col = Gtk.TreeViewColumn("策略", Gtk.CellRendererText(), text=3)
        proxy_col.set_fixed_width(160)
        proxy_col.set_resizable(True)
        proxy_col.set_sort_column_id(3)
        self.tree.append_column(proxy_col)

        size_col = Gtk.TreeViewColumn("条目", Gtk.CellRendererText(), text=4)
        size_col.set_fixed_width(68)
        size_col.set_sort_column_id(4)
        self.tree.append_column(size_col)
        for column in self.tree.get_columns():
            for renderer in column.get_cells():
                renderer.set_property("ellipsize", Pango.EllipsizeMode.END)

    # ---------------- 数据 ----------------
    def on_show(self) -> None:
        super().on_show()
        if self.controller.running:
            self.load()
        else:
            self._load_local()

    def on_state(self, state: str, error: str = "") -> None:
        if state == "running":
            self.load()
        elif state in ("stopped", "error"):
            self._load_local()

    def on_profiles(self) -> None:
        if not self.controller.running:
            self._load_local()

    def load(self, force: bool = False) -> None:
        if self._loading or not self.controller.running:
            return
        self._loading = True
        run_async(self.controller.rules, on_success=self._loaded_remote, on_error=self._load_error, name="rules")

    def _load_local(self) -> None:
        try:
            data = self.controller.profiles.data()
        except (ProfileError, Exception) as exc:
            self._rules = []
            self._local = True
            self._finish_load()
            return
        rules = data.get("rules") if isinstance(data.get("rules"), list) else []
        parsed = []
        for index, rule in enumerate(rules):
            text = str(rule)
            parts = text.split(",")
            if len(parts) >= 3:
                policy = parts[2]
                options = ",".join(parts[3:]) if len(parts) > 3 else ""
                parsed.append({"type": rule_type_label(parts[0]), "payload": parts[1], "proxy": policy, "size": 0, "options": options, "order": index + 1})
            else:
                parsed.append({"type": "规则", "payload": text, "proxy": "—", "size": 0, "order": index + 1})
        self._rules = parsed
        self._local = True
        self._finish_load()

    def _loaded_remote(self, rules: list[dict[str, Any]]) -> None:
        self._loading = False
        self._rules = []
        for index, rule in enumerate(rules):
            self._rules.append(
                {
                    "type": rule_type_label(str(rule.get("type") or "—")),
                    "payload": str(rule.get("payload") or ""),
                    "proxy": str(rule.get("proxy") or "—"),
                    "size": int(rule.get("size") or 0),
                    "order": index + 1,
                }
            )
        self._local = False
        self._finish_load()

    def _load_error(self, exc: Exception) -> None:
        self._loading = False
        self.toast(f"读取规则失败：{exc}", error=True)
        self._load_local()

    def _finish_load(self) -> None:
        self._dirty = True
        self._render()

    def _render(self) -> None:
        self._dirty = False
        self.store.clear()
        for rule in self._rules:
            payload = rule.get("payload", "")
            if not payload and rule.get("type") == "MATCH":
                payload = "所有流量"
            size = rule.get("size", 0)
            self.store.append(
                [rule.get("order", 0), rule.get("type", ""), payload, rule.get("proxy", ""), "—" if int(size or 0) < 0 else str(size)]
            )
        visible = self.filter.iter_n_children(None)
        self.count_label.set_text(f"{len(self._rules)} 条规则" + (f" · {visible} 匹配" if visible != len(self._rules) else ""))
        self.source_label.set_text("来自订阅配置（内核未运行）" if self._local else "来自 mihomo 运行时")
        self.stack.set_visible_child_name("list" if visible else "empty")
        if visible:
            self.empty.set(title="暂无规则", subtitle="启动内核后即可读取当前生效的规则")
        elif self.search.get_text().strip():
            self.empty.set(title="没有匹配的规则", subtitle="试试其他关键词")

    def _visible(self, model: Gtk.TreeModel, tree_iter: Gtk.TreeIter, _data: Any) -> bool:
        query = self.search.get_text().strip().lower()
        if not query:
            return True
        return any(query in str(model.get_value(tree_iter, i)).lower() for i in (1, 2, 3))

    def _on_button(self, tree: Gtk.TreeView, event) -> bool:
        if event.button != 3:
            return False
        path_info = tree.get_path_at_pos(int(event.x), int(event.y))
        if not path_info:
            return False
        tree.get_selection().select_path(path_info[0])
        model, tree_iter = tree.get_selection().get_selected()
        if tree_iter is None:
            return False
        row = [model.get_value(tree_iter, i) for i in range(5)]
        menu = Gtk.Menu()
        copy = Gtk.MenuItem(label="复制规则")
        copy.connect("activate", lambda _i: self._copy(f"{row[1]},{row[2]},{row[3]}"))
        menu.append(copy)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _copy(self, text: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()
        self.toast("规则已复制")
