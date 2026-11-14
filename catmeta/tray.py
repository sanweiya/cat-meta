"""系统托盘图标与原生快捷菜单。

右键菜单优先交给 XApp/GTK 绘制：XApp 会在按钮释放时用系统主题弹出
``Gtk.Menu``，位置、阴影、键盘导航和主题适配都由桌面环境处理。只有
XApp 的 secondary-menu 接口不可用时，才退回自绘的 ``TrayPopup``。
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from .controller import MODE_LABELS, Controller
from .system import notify
from .ui.widgets.common import icon, label
from .util import format_speed

log = logging.getLogger("catmeta.tray")


class TrayIcon:
    def __init__(self, app, window, controller: Controller):
        self.app = app
        self.window = window
        self.controller = controller
        self.available = False
        self._backend = ""
        self._xapp = None
        self._status_icon: Gtk.StatusIcon | None = None
        self._popup: TrayPopup | None = None
        self._menu: Gtk.Menu | None = None
        self._native_menu = False
        self._updating = False
        self._traffic_source = 0
        self._build_menu()
        self._create()

    def _create(self) -> None:
        try:
            gi.require_version("XApp", "1.0")
            from gi.repository import XApp

            status_icon = XApp.StatusIcon()
            try:
                status_icon.set_name("cat-meta")
            except Exception:
                pass
            status_icon.set_icon_name("cat-meta")
            status_icon.set_tooltip_text("Cat Meta")
            status_icon.connect("activate", self._on_activate)
            try:
                # 在显示前挂上 secondary menu，并保证菜单子项已经
                # show_all；否则第一次右键可能退化成 activate，或弹出
                # 一个没有内容的空白菜单。
                status_icon.set_secondary_menu(self._menu)
                self._native_menu = True
            except Exception:
                log.warning("XApp 原生菜单不可用，右键将回退到自绘菜单", exc_info=True)
            status_icon.set_visible(True)
            self._xapp = status_icon
            self._backend = "xapp"
            self.available = True
            log.info("使用 XApp 状态图标%s", "（原生菜单）" if self._native_menu else "（自绘菜单回退）")
            return
        except Exception:
            log.debug("XApp 状态图标不可用，改用 Gtk.StatusIcon", exc_info=True)
        try:
            status = Gtk.StatusIcon.new_from_icon_name("cat-meta")
            status.set_tooltip_text("Cat Meta")
            status.set_visible(True)
            status.connect("activate", lambda _i: self.window.present_window())
            status.connect("popup-menu", self._popup_native)
            self._status_icon = status
            self._native_menu = True
            self._backend = "gtk"
            self.available = True
            # XFCE/LXQt 的面板不一定有 XEmbed 通知区域（XFCE 需装“通知区域”
            # 插件，LXQt 根本不支持）。没有宿主时图标不会显示，此时必须把
            # 托盘判为不可用，否则“关闭到托盘/启动时最小化”会把窗口藏到无处。
            GLib.timeout_add(600, self._verify_xembed)
        except Exception:
            log.exception("无法创建系统托盘图标")
            self.available = False

    def _verify_xembed(self) -> bool:
        icon = self._status_icon
        if icon is None or self._backend != "gtk":
            return False
        try:
            embedded = bool(icon.is_embedded())
        except Exception:
            embedded = True
        if embedded:
            return False
        log.info("面板没有 XEmbed 托盘宿主，禁用托盘图标")
        self.available = False
        self._native_menu = False
        try:
            icon.set_visible(False)
        except Exception:
            pass
        self._status_icon = None
        window = getattr(self.app, "window", None)
        if window is not None:
            try:
                window.present_window()
            except Exception:
                log.debug("恢复主窗口失败", exc_info=True)
        return False

    def _on_activate(self, _icon, button: int, _time: int) -> None:
        # 设置 secondary menu 后，XApp 不会为右键发出 activate；这里只在
        # 原生菜单安装失败时接管右键。
        if button == 3:
            if not self._native_menu:
                self._show_custom()
            return
        self.window.present_window()

    def _show_custom(self) -> None:
        if self._popup is None:
            self._popup = TrayPopup(self)
        self._popup.popup()

    def _popup_native(self, _icon, button: int, time: int) -> None:
        """旧式 Gtk.StatusIcon 的右键入口，仍然使用同一个原生 Gtk.Menu。"""
        if self._menu is None:
            self._show_custom()
            return
        try:
            self._menu.popup(
                None, None, Gtk.StatusIcon.position_menu, self._status_icon, button, time
            )
        except Exception:
            log.exception("原生托盘菜单弹出失败，回退到自绘菜单")
            self._show_custom()

    def _build_menu(self) -> None:
        menu = Gtk.Menu()
        self._menu = menu

        show_item = Gtk.MenuItem(label="显示 Cat Meta")
        show_item.connect("activate", lambda _i: self.window.present_window())
        menu.append(show_item)

        self.core_item = Gtk.MenuItem(label="启动内核")
        self.core_item.connect("activate", lambda _i: self.window.toggle_core())
        menu.append(self.core_item)

        menu.append(Gtk.SeparatorMenuItem())

        self.proxy_item = Gtk.CheckMenuItem(label="系统代理")
        self.proxy_item.connect("toggled", self._on_proxy_item)
        menu.append(self.proxy_item)

        self.tun_item = Gtk.CheckMenuItem(label="TUN 模式")
        self.tun_item.connect("toggled", self._on_tun_item)
        menu.append(self.tun_item)

        mode_menu = Gtk.Menu()
        self.mode_items: dict[str, Gtk.RadioMenuItem] = {}
        group = None
        for mode in ("rule", "global", "direct"):
            item = Gtk.RadioMenuItem(label=f"{MODE_LABELS[mode]}模式")
            if group is None:
                group = item
            else:
                item.join_group(group)
            item.connect("toggled", lambda i, m=mode: self._on_mode_item(i, m))
            mode_menu.append(item)
            self.mode_items[mode] = item
        mode_menu.show_all()
        mode_root = Gtk.MenuItem(label="代理模式")
        mode_root.set_submenu(mode_menu)
        menu.append(mode_root)

        menu.append(Gtk.SeparatorMenuItem())

        settings_item = Gtk.MenuItem(label="设置")
        settings_item.connect("activate", lambda _i: self._open_settings())
        menu.append(settings_item)

        quit_item = Gtk.MenuItem(label="退出 Cat Meta")
        quit_item.connect("activate", lambda _i: self.app.quit_app())
        menu.append(quit_item)

        # 原生 Gtk.Menu 不会自动显示 append 进去的子项；漏掉这一步时
        # XApp 会弹出一个空白菜单，这正是之前“原生菜单无效”的原因之一。
        menu.show_all()

    def _open_settings(self) -> None:
        self.window.present_window()
        self.window.navigate("settings")

    def set_visible(self, visible: bool) -> None:
        if not self.available:
            return
        try:
            if self._xapp is not None:
                self._xapp.set_visible(visible)
            elif self._status_icon is not None:
                self._status_icon.set_visible(visible)
        except Exception:
            log.debug("设置托盘可见性失败", exc_info=True)

    def update_traffic(self) -> None:
        """流量变化只用于刷新提示，合并为最多每秒一次。"""
        if not self.available or self._traffic_source:
            return
        self._traffic_source = GLib.timeout_add(1000, self._update_traffic)

    def _update_traffic(self) -> bool:
        self._traffic_source = 0
        self.update()
        return False

    def update(self) -> None:
        if not self.available:
            return
        self._updating = True
        try:
            running = self.controller.running
            if self.controller.state in ("starting", "stopping"):
                self.core_item.set_label("正在切换…")
                self.core_item.set_sensitive(False)
            else:
                self.core_item.set_label("停止内核" if running else "启动内核")
                self.core_item.set_sensitive(True)
            settings = self.controller.settings
            self.proxy_item.set_active(bool(settings.system_proxy))
            self.tun_item.set_active(bool(settings.tun))
            mode = self.controller.mode()
            if mode in self.mode_items:
                self.mode_items[mode].set_active(True)
            summary = self.controller.session_summary()
            tooltip = f"Cat Meta · {'已连接' if running else '未运行'}"
            if running:
                tooltip += f"\n↓ {format_speed(summary['down'])}  ↑ {format_speed(summary['up'])}\n{MODE_LABELS.get(mode, mode)}模式"
            if self._xapp is not None:
                self._xapp.set_tooltip_text(tooltip)
            elif self._status_icon is not None:
                self._status_icon.set_tooltip_text(tooltip)
        finally:
            self._updating = False

    def _on_mode_item(self, item: Gtk.RadioMenuItem, mode: str) -> None:
        if self._updating or not item.get_active():
            return
        try:
            self.window.set_mode(mode)
        except Exception:
            log.exception("托盘切换模式失败")

    def _on_proxy_item(self, item: Gtk.CheckMenuItem) -> None:
        if self._updating:
            return
        enabled = item.get_active()
        from .async_utils import run_async

        run_async(
            lambda: self.controller.set_system_proxy(enabled),
            on_success=lambda _r: notify("系统代理已" + ("开启" if enabled else "关闭")),
            on_error=lambda exc: (notify("系统代理设置失败", str(exc)), self.update()),
            name="tray-proxy",
        )

    def _on_tun_item(self, item: Gtk.CheckMenuItem) -> None:
        if self._updating:
            return
        enabled = item.get_active()
        from .async_utils import run_async

        run_async(
            lambda: self.controller.set_quick_toggle("tun", enabled),
            on_success=lambda _r: notify("TUN 模式已" + ("开启" if enabled else "关闭")),
            on_error=lambda exc: (notify("TUN 模式设置失败", str(exc)), self.update()),
            name="tray-tun",
        )


class TrayPopup(Gtk.Window):
    """紧凑的自绘托盘菜单。

    菜单位置只按鼠标位置和窗口自然高度计算，不做额外的边界抬升。
    """

    def __init__(self, tray: TrayIcon):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.tray = tray
        self.set_title("Cat Meta")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.set_default_size(204, -1)
        self.get_style_context().add_class("tray-popup")
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.box)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", lambda w, e: w.hide() or True)

    def _on_focus_out(self, _window, _event) -> bool:
        GLib.timeout_add(100, self._hide_if_unfocused)
        return False

    def _hide_if_unfocused(self) -> bool:
        if not self.is_active():
            self.hide()
        return False

    def _on_key(self, _window, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def _clear(self) -> None:
        for child in self.box.get_children():
            child.destroy()

    def _header(self, text: str, active: bool = False) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("tray-popup-header")
        dot = Gtk.Label(label="●")
        dot.get_style_context().add_class("status-dot")
        if active:
            dot.get_style_context().add_class("running")
        row.pack_start(dot, False, False, 0)
        row.pack_start(label(text, "tray-popup-title"), True, True, 0)
        self.box.pack_start(row, False, False, 0)

    def _separator(self) -> None:
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("tray-popup-separator")
        self.box.pack_start(sep, False, False, 0)

    def _item(self, text: str, icon_name: str, callback, *, checked: bool | None = None, destructive: bool = False) -> None:
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("tray-popup-item")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(icon(icon_name, 12), False, False, 0)
        text_label = label(text)
        if destructive:
            text_label.get_style_context().add_class("destructive")
        row.pack_start(text_label, True, True, 0)
        if checked is not None:
            if checked:
                row.pack_end(icon("object-select-symbolic", 12), False, False, 0)
            else:
                spacer = Gtk.Box()
                spacer.set_size_request(12, 1)
                row.pack_end(spacer, False, False, 0)
        button.add(row)
        button.connect("clicked", lambda _b: self._activate(callback))
        self.box.pack_start(button, False, False, 0)

    def _activate(self, callback) -> None:
        self.hide()
        callback()

    def refresh(self) -> None:
        self._clear()
        controller = self.tray.controller
        running = controller.running
        self._header("已连接" if running else "未运行", running)
        self._item("显示 Cat Meta", "window-new-symbolic", self.tray.window.present_window)
        self._item(
            "停止内核" if running else "启动内核",
            "process-stop-symbolic" if running else "media-playback-start-symbolic",
            self.tray.window.toggle_core,
        )
        self._separator()
        settings = controller.settings
        self._item(
            "系统代理",
            "network-wired-symbolic",
            self.tray.window.toggle_system_proxy,
            checked=bool(settings.system_proxy),
        )
        self._item(
            "TUN 模式",
            "network-vpn-symbolic",
            lambda: self.tray.window.set_tun_from_tray(not bool(settings.tun)),
            checked=bool(settings.tun),
        )
        self._separator()
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mode_row.get_style_context().add_class("tray-popup-mode")
        mode_row.pack_start(label("代理模式", "tiny-label"), False, False, 0)
        combo = Gtk.ComboBoxText()
        for mode in ("rule", "global", "direct"):
            combo.append(mode, f"{MODE_LABELS[mode]}模式")
        combo.set_active_id(controller.mode())
        combo.connect("changed", lambda c: self._mode_changed(c))
        mode_row.pack_end(combo, False, False, 0)
        self.box.pack_start(mode_row, False, False, 0)
        self._separator()
        self._item("设置", "preferences-system-symbolic", lambda: self.tray.window.navigate("settings"))
        self._item("退出 Cat Meta", "application-exit-symbolic", self.tray.app.quit_app, destructive=True)
        self.box.show_all()

    def _mode_changed(self, combo: Gtk.ComboBoxText) -> None:
        mode = combo.get_active_id()
        if mode:
            self.tray.window.set_mode(mode)
            self.hide()

    def popup(self) -> None:
        self.refresh()
        self.show_all()
        self.realize()
        display = Gdk.Display.get_default()
        width, height = 204, self.get_preferred_size()[1].height
        try:
            seat = display.get_default_seat()
            pointer = seat.get_pointer()
            position = pointer.get_position()
            if len(position) == 3:
                _, x, y = position
            elif len(position) == 2:
                x, y = position
            monitor = display.get_monitor_at_point(x, y)
            area = monitor.get_workarea()
            px = min(max(area.x, x - width + 24), area.x + area.width - width)
            # 不额外抬升菜单；保持最初的自绘菜单位置逻辑。
            py = max(area.y, y - height - 10)
            self.move(px, py)
        except Exception:
            log.debug("无法定位托盘菜单", exc_info=True)
        self.present_with_time(Gtk.get_current_event_time())
        self.grab_focus()
