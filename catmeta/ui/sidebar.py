"""原生侧边导航栏。"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from ..controller import MODE_LABELS, Controller
from .widgets.common import icon, label


SIDEBAR_DEFAULT_WIDTH = 200
SIDEBAR_MIN_WIDTH = 160
SIDEBAR_MAX_WIDTH = 420
# 覆盖在描边上的透明拖拽热区宽度；它不参与布局，所以不会改变
# 侧栏描边与内容区工具栏横线的衔接。
SIDEBAR_RESIZE_HIT_WIDTH = 9

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
        # 代码选中行时不要再触发一次导航，否则 on_show() 会跑两遍。
        self._syncing = False
        self.get_style_context().add_class("sidebar")
        # 只约束最小宽度；实际宽度由 SidebarSplit 的固定宽度容器决定。
        self.set_size_request(SIDEBAR_MIN_WIDTH, -1)

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
        # 不设 max_width_chars：文本按侧栏实际宽度省略，宽度不会被文本撑开。
        self.version_label = label("mihomo", "core-version", ellipsize=True)
        self.speed_label = label("↓ 0 B/s   ↑ 0 B/s", "core-version", ellipsize=True)
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
        if self._syncing:
            return
        if row is not None:
            page_id = getattr(row, "page_id", None)
            if page_id:
                self.on_navigate(page_id)

    def select(self, page_id: str) -> None:
        row = self.rows.get(page_id)
        if row is not None and self.listbox.get_selected_row() is not row:
            self._syncing = True
            try:
                self.listbox.select_row(row)
            finally:
                self._syncing = False

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

        text = f"↓ {fmt(down)}   ↑ {fmt(up)}"
        self.speed_label.set_text(text)
        # 侧栏较窄时文本会被省略，悬停仍可看到完整数值。
        self.speed_label.set_tooltip_text(text)

    def update_connections(self, count: int) -> None:
        if count > 0:
            self.connection_badge.set_text("99+" if count > 99 else str(count))
            self.connection_badge.set_visible(True)
        else:
            self.connection_badge.set_visible(False)


def clamp_sidebar_width(width: int | float | str | None) -> int:
    try:
        value = int(float(width))  # type: ignore[arg-type]
    except (OverflowError, TypeError, ValueError):
        return SIDEBAR_DEFAULT_WIDTH
    return max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, value))


class _FixedWidthBin(Gtk.Bin):
    """始终按固定宽度申请空间的容器。

    普通 GtkBin/GtkBox 会把子控件的自然宽度传给父容器，侧栏底部的
    流量文本一变长就会把侧栏撑宽。这里直接返回设定的宽度，子控件的
    自然宽度只影响它自己在侧栏内部如何省略，不再影响侧栏尺寸。
    """

    def __init__(self, child: Gtk.Widget, width: int) -> None:
        super().__init__()
        self._width = max(1, int(width))
        self.add(child)

    def set_fixed_width(self, width: int) -> None:
        width = max(1, int(width))
        if width != self._width:
            self._width = width
            self.queue_resize()

    def do_get_preferred_width(self):
        return self._width, self._width

    def do_get_preferred_width_for_height(self, _height: int):
        return self._width, self._width

    def do_size_allocate(self, allocation: Gdk.Rectangle) -> None:
        self.set_allocation(allocation)
        child = self.get_child()
        if child is None or not child.get_visible():
            return
        child_allocation = Gdk.Rectangle()
        child_allocation.x = allocation.x
        child_allocation.y = allocation.y
        child_allocation.width = min(self._width, allocation.width)
        child_allocation.height = allocation.height
        child.size_allocate(child_allocation)


class SidebarSplit(Gtk.Overlay):
    """可拖拽调整宽度的侧栏容器。

    侧栏宽度由固定宽度容器决定，不会被底部流量文本、版本号或连接
    徽标撑大。描边仍由侧栏自身绘制（1px），拖拽热区是骑跨在描边上的
    9px 透明输入条（描边左右各约一半），不占用布局空间，也不会遮挡
    内容区控件；因此内容区工具栏横线能和描边衔接。拖拽结束后回调调用方
    持久化宽度，双击描边恢复默认宽度。
    """

    SAVE_DELAY_MS = 320

    def __init__(
        self,
        sidebar: Gtk.Widget,
        content: Gtk.Widget,
        width: int | None,
        *,
        on_width_changed: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__()
        self.sidebar = sidebar
        self.content = content
        self.on_width_changed = on_width_changed
        self._width = clamp_sidebar_width(width)
        self._dragging = False
        self._drag_origin = 0.0
        self._drag_start = self._width
        self._save_source = 0
        self._pending_width = 0
        self._hit_x = max(0, self._width - (SIDEBAR_RESIZE_HIT_WIDTH + 1) // 2)
        self._hit_height = 0
        self._hit_height_source = 0

        self._frame = _FixedWidthBin(sidebar, self._width)
        self._frame.set_vexpand(True)

        # 拖拽热区放在一个 pass-through 的 Gtk.Fixed 覆盖层里，
        # 用 Fixed 的坐标把它精确地骑跨在描边上：描边左右各占约一半，
        # 同时 pass-through 保证内容区的按钮等控件仍能正常点击。
        self._hitbox = Gtk.EventBox()
        self._hitbox.set_visible_window(True)
        self._hitbox.get_style_context().add_class("sidebar-resize-hitbox")
        self._hitbox.set_can_focus(False)
        self._hitbox.set_size_request(SIDEBAR_RESIZE_HIT_WIDTH, -1)
        self._hitbox.set_tooltip_text("拖动调整侧栏宽度，双击恢复默认")
        self._hitbox.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._hitbox.connect("realize", self._on_hitbox_realize)
        self._hitbox.connect("enter-notify-event", self._on_hitbox_enter)
        self._hitbox.connect("leave-notify-event", self._on_hitbox_leave)
        self._hitbox.connect("button-press-event", self._on_button_press)
        self._hitbox.connect("button-release-event", self._on_button_release)
        self._hitbox.connect("motion-notify-event", self._on_motion)

        self._hit_layer = Gtk.Fixed()
        self._hit_layer.set_hexpand(True)
        self._hit_layer.set_vexpand(True)
        self._hit_layer.put(self._hitbox, self._hit_x, 0)
        self._hit_layer.connect("size-allocate", self._on_hit_layer_allocate)
        self.add_overlay(self._hit_layer)
        self.set_overlay_pass_through(self._hit_layer, True)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        root.pack_start(self._frame, False, True, 0)
        root.pack_start(content, True, True, 0)
        self.add(root)

    # ---------------- 尺寸 ----------------
    def width(self) -> int:
        return self._width

    def set_width(self, width: int) -> None:
        width = clamp_sidebar_width(width)
        if width == self._width:
            return
        self._width = width
        self._frame.set_fixed_width(width)
        self._sync_hitbox()
        self._pending_width = width
        self._queue_save()

    def _queue_save(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(self.SAVE_DELAY_MS, self._flush_save)

    def reset_width(self) -> None:
        self.set_width(SIDEBAR_DEFAULT_WIDTH)

    def _sync_hitbox(self) -> None:
        x = max(0, self._width - (SIDEBAR_RESIZE_HIT_WIDTH + 1) // 2)
        if x != self._hit_x:
            self._hit_x = x
            self._hit_layer.move(self._hitbox, x, 0)

    def _on_hit_layer_allocate(self, _widget: Gtk.Widget, allocation: Gdk.Rectangle) -> None:
        if allocation.height and allocation.height != self._hit_height:
            self._hit_height = allocation.height
            # 不要在 size-allocate 中直接改子控件尺寸，Fixed 不会在
            # 同一轮重新分配；推迟到空闲时再应用。
            if not self._hit_height_source:
                self._hit_height_source = GLib.idle_add(self._apply_hit_height)

    def _apply_hit_height(self) -> bool:
        self._hit_height_source = 0
        if self._hit_height:
            self._hitbox.set_size_request(SIDEBAR_RESIZE_HIT_WIDTH, self._hit_height)
        return False

    def save_now(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if self._pending_width:
            self._notify_width()

    def _notify_width(self) -> None:
        width = self._pending_width or self._width
        self._pending_width = 0
        if self.on_width_changed is not None:
            self.on_width_changed(clamp_sidebar_width(width))

    def _flush_save(self) -> bool:
        self._save_source = 0
        self._notify_width()
        return False

    # ---------------- 拖拽 ----------------
    def _on_hitbox_realize(self, widget: Gtk.Widget) -> None:
        window = widget.get_window()
        if window is None:
            return
        for name in ("col-resize", "ew-resize"):
            try:
                window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), name))
                return
            except Exception:
                continue

    def _on_hitbox_enter(self, *_args) -> bool:
        self.sidebar.get_style_context().add_class("sidebar-resize-hover")
        return False

    def _on_hitbox_leave(self, *_args) -> bool:
        self.sidebar.get_style_context().remove_class("sidebar-resize-hover")
        return False

    def _on_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.reset_width()
            return True
        self._dragging = True
        self._drag_origin = event.x_root
        self._drag_start = self._width
        self.sidebar.get_style_context().add_class("sidebar-resize-active")
        return True

    def _on_motion(self, _widget: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if not self._dragging:
            return False
        self.set_width(round(self._drag_start + event.x_root - self._drag_origin))
        return True

    def _on_button_release(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        if self._dragging:
            self._dragging = False
            self.sidebar.get_style_context().remove_class("sidebar-resize-active")
            if self._pending_width:
                self._queue_save()
        return True
