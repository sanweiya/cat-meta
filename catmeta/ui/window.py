"""主窗口：Mint 风格标题栏、侧栏、页面栈与提示条。"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from .. import APP_NAME, VERSION
from ..async_utils import run_async
from ..controller import MODE_LABELS, Controller
from ..paths import APP_DATA
from ..system import open_folder
from .pages.connections import ConnectionsPage
from .pages.home import HomePage
from .pages.logs import LogsPage
from .pages.profiles import ProfilesPage
from .pages.proxies import ProxiesPage
from .pages.rules import RulesPage
from .pages.settings import SettingsPage
from .sidebar import Sidebar
from .widgets.common import block_scroll, icon, label

MIN_WINDOW_WIDTH = 810
MIN_WINDOW_HEIGHT = 580

PAGE_ORDER = ["home", "proxies", "profiles", "connections", "rules", "logs", "settings"]
PAGE_TITLES = {
    "home": "概览",
    "proxies": "代理",
    "profiles": "订阅",
    "connections": "连接",
    "rules": "规则",
    "logs": "日志",
    "settings": "设置",
}



class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application, controller: Controller):
        super().__init__(application=application, title=APP_NAME)
        self.application = application
        self.controller = controller
        self.app = application
        self._quitting = False
        self._mode_syncing = False
        self._last_state = ""
        self._toast_source = 0
        self._traffic_source = 0
        self._geometry_source = 0
        self._geometry_ready = False

        self.set_icon_name("cat-meta")
        # window_width/height 保存的是外框尺寸；get_size()/set_default_size()
        # 使用内容尺寸。用上次记录的 CSD 内外差值换回内容尺寸，保证最小
        # 可调尺寸和保存的最小尺寸使用同一坐标。
        outer_width = int(controller.settings.window_width or 1200)
        outer_height = int(controller.settings.window_height or 800)
        inset_width = max(0, int(controller.settings.window_inset_width or 0))
        inset_height = max(0, int(controller.settings.window_inset_height or 0))
        content_width = max(1, outer_width - inset_width)
        content_height = max(1, outer_height - inset_height)
        self.set_default_size(content_width, content_height)
        self.set_size_request(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.set_wmclass("cat-meta", "CatMeta")
        try:
            self.set_role("cat-meta")
        except Exception:
            pass

        self._build_header()
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.sidebar = Sidebar(
            controller,
            on_navigate=self.navigate,
            on_toggle_core=self.toggle_core,
        )
        self.sidebar.set_size_request(int(controller.settings.sidebar_width or 200), -1)
        root.pack_start(self.sidebar, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.content_overlay = Gtk.Overlay()
        self.content_overlay.add(content)
        self._build_info_bar(self.content_overlay)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.pages: dict[str, Any] = {}
        self._create_pages()
        content.pack_start(self.stack, True, True, 0)
        root.pack_start(self.content_overlay, True, True, 0)
        self.add(root)

        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)
        self.connect("configure-event", self._on_configure)
        self.connect("map-event", self._on_map_event)
        self.connect("map-event", lambda *_: self.controller.set_ui_visible(True))
        self.connect("unmap-event", lambda *_: self.controller.set_ui_visible(False))
        self.connect("focus-in-event", lambda *_: self.controller.emit("window-focus"))
        self._subscribe()
        self._sync_mode(self.controller.mode())
        self._on_state(self.controller.state, self.controller.last_error)
        self._on_traffic()
        self._on_metrics()

        page = self.controller.settings.last_page
        if page not in self.pages:
            page = "home"
        self.navigate(page, save=False)
        if self.controller.settings.window_maximized:
            self.maximize()
        self._register_actions()

    # ---------------- 标题栏 ----------------
    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_has_subtitle(False)
        self.set_titlebar(header)
        self.header = header

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_box.set_halign(Gtk.Align.CENTER)
        title_box.set_valign(Gtk.Align.CENTER)
        self.header_title = label("概览", "header-title", xalign=0.5)
        self.header_subtitle = label("代理状态、实时流量与快捷控制", "header-subtitle", xalign=0.5, ellipsize=True, max_width=72)
        title_box.pack_start(self.header_title, False, False, 0)
        title_box.pack_start(self.header_subtitle, False, False, 0)
        header.set_custom_title(title_box)

        menu_button = Gtk.MenuButton()
        menu_button.set_image(icon("open-menu-symbolic", 16))
        menu_button.set_tooltip_text("主菜单")
        menu_button.set_relief(Gtk.ReliefStyle.NONE)
        menu_button.get_style_context().add_class("flat")
        menu = Gio.Menu()
        menu.append("重启内核", "win.restart")
        menu.append("切换系统代理", "win.toggle-system-proxy")
        menu.append("打开配置目录", "win.open-data")
        menu.append("设置", "win.settings")
        menu.append("键盘快捷键", "win.shortcuts")
        menu.append("关于 Cat Meta", "win.about")
        menu.append("退出", "app.quit")
        menu_button.set_popover(Gtk.Popover.new_from_model(menu_button, menu))
        self.header_mode = Gtk.ComboBoxText()
        block_scroll(self.header_mode)
        self.header_mode.set_tooltip_text("代理模式")
        self.header_mode.set_size_request(104, -1)
        for mode in ("rule", "global", "direct"):
            self.header_mode.append(mode, f"{MODE_LABELS[mode]}模式")
        self.header_mode.connect("changed", self._on_mode_combo_changed)
        header.pack_end(menu_button)
        header.pack_end(self.header_mode)

    def _build_info_bar(self, parent: Gtk.Overlay) -> None:
        """原生 Gtk.InfoBar，以覆盖层显示，不参与页面布局。"""
        self.info_revealer = Gtk.Revealer()
        self.info_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.info_revealer.set_transition_duration(180)
        self.info_revealer.set_reveal_child(False)
        self.info_revealer.set_halign(Gtk.Align.FILL)
        self.info_revealer.set_valign(Gtk.Align.END)
        self.info_revealer.set_hexpand(True)
        self.info_bar = Gtk.InfoBar()
        self.info_bar.set_show_close_button(True)
        self.info_bar.set_message_type(Gtk.MessageType.INFO)
        self.info_label = label("", wrap=True, max_width=110)
        self.info_label.set_xalign(0)
        self.info_bar.get_content_area().pack_start(self.info_label, True, True, 0)
        self.info_bar.connect("response", self._on_info_bar_response)
        self.info_revealer.add(self.info_bar)
        parent.add_overlay(self.info_revealer)
        self.info_revealer.set_visible(False)
        self.info_revealer.set_no_show_all(True)

    # ---------------- 页面 ----------------
    def _create_pages(self) -> None:
        self.pages = {
            "home": HomePage(self.controller, self.toast, self.navigate),
            "proxies": ProxiesPage(self.controller, self.toast),
            "profiles": ProfilesPage(self.controller, self.toast),
            "connections": ConnectionsPage(self.controller, self.toast, self.navigate),
            "rules": RulesPage(self.controller, self.toast),
            "logs": LogsPage(self.controller, self.toast),
            "settings": SettingsPage(self.controller, self.toast),
        }
        for page_id, page in self.pages.items():
            self.stack.add_named(page, page_id)
            if hasattr(page, "header") and not page.header.actions.get_children():
                # show_all() 会把 set_visible(False) 的页头重新显示出来，
                # 必须同时阻止它参与 show_all，否则概览页顶部会留下空栏。
                page.header.set_no_show_all(True)
                page.header.set_visible(False)

    def _subscribe(self) -> None:
        self.controller.subscribe("state", self._on_state)
        self.controller.subscribe("traffic", self._on_traffic)
        self.controller.subscribe("metrics", self._on_metrics)
        self.controller.subscribe("proxy-node", self._on_proxy_node)
        self.controller.subscribe("tick", self._on_tick)
        self.controller.subscribe("profiles", self._on_profiles)
        self.controller.subscribe("settings", self._on_settings)
        self.controller.subscribe("mode", self._on_mode)
        self.controller.subscribe("connections", self._on_connections)
        self.controller.subscribe("log", self._on_log)
        self.controller.subscribe("startup-progress", self._on_startup_progress)

    def navigate(self, page_id: str, save: bool = True) -> None:
        if page_id not in self.pages:
            return
        self.stack.set_visible_child_name(page_id)
        self.sidebar.select(page_id)
        page = self.pages[page_id]
        if hasattr(page, "on_show"):
            page.on_show()
        self._update_header_meta(page_id)
        if save:
            self.controller.settings.set("last_page", page_id)
        GLib.idle_add(self._clear_focus)

    def _update_header_meta(self, page_id: str) -> None:
        page = self.pages.get(page_id)
        title = PAGE_TITLES.get(page_id, APP_NAME)
        subtitle = ""
        if page is not None and hasattr(page, "header"):
            title = page.header.title_label.get_text() or title
            subtitle = page.header.subtitle_label.get_text()
        self.header_title.set_text(title)
        self.header_subtitle.set_text(subtitle)
        self.header_subtitle.set_visible(bool(subtitle))
        self.set_title(APP_NAME)

    def _clear_focus(self) -> bool:
        try:
            self.set_focus(None)
        except Exception:
            pass
        return False

    # ---------------- 事件 ----------------
    def _on_state(self, state: str, error: str = "") -> None:
        self.sidebar.update_state(state)
        home = self.pages["home"]
        home.on_state(state, error)
        self.pages["proxies"].on_state(state, error)
        self.pages["connections"].on_state(state, error)
        self.pages["rules"].on_state(state, error)
        self.pages["settings"].on_state(state, error)
        if state == "error" and error and self._last_state != "error":
            self.toast(error.splitlines()[0], error=True, timeout=8)
        self._last_state = state
        if getattr(self.app, "tray", None):
            self.app.tray.update()

    def _on_traffic(self) -> None:
        # WebSocket 可能比界面需要的刷新频率高，合并到 4Hz。
        if self._traffic_source:
            return
        self._traffic_source = GLib.timeout_add(250, self._apply_traffic)

    def _apply_traffic(self) -> bool:
        self._traffic_source = 0
        summary = self.controller.session_summary()
        self.sidebar.update_traffic(summary["down"], summary["up"])
        self.pages["home"].on_traffic()
        if getattr(self.app, "tray", None):
            self.app.tray.update_traffic()
        return False

    def _on_metrics(self) -> None:
        summary = self.controller.session_summary()
        self.pages["home"].on_metrics()
        self.sidebar.update_connections(summary["connections"])

    def _on_proxy_node(self, group: str, name: str) -> None:
        self.pages["home"].on_current_node(group, name)
        self.pages["proxies"].on_current_node(group, name)

    def _on_tick(self) -> None:
        self.pages["home"].on_tick()

    def _on_profiles(self) -> None:
        for page in self.pages.values():
            if hasattr(page, "on_profiles"):
                page.on_profiles()
        self.sidebar.update_state(self.controller.state)
        current = self.stack.get_visible_child_name()
        if current:
            self._update_header_meta(current)
        if getattr(self.app, "tray", None):
            self.app.tray.update()

    def _on_settings(self, values: dict[str, Any]) -> None:
        self.pages["home"].on_settings(values)
        self.pages["settings"].on_settings(values)
        self._sync_mode(str(values.get("mode") or "rule"))
        self.sidebar.update_state(self.controller.state)
        if getattr(self.app, "tray", None):
            self.app.tray.update()

    def _on_mode(self, mode: str) -> None:
        self._sync_mode(mode)
        self.sidebar.update_state(self.controller.state)
        self.pages["home"].on_state(self.controller.state, self.controller.last_error)

    def _on_mode_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._mode_syncing:
            return
        mode = combo.get_active_id() or "rule"
        if mode == self.controller.mode():
            return
        run_async(
            lambda: self.controller.set_mode(mode),
            on_success=lambda _r: None,
            on_error=lambda exc: (self.toast(str(exc), error=True), self._sync_mode(self.controller.mode())),
            name="set-mode",
        )

    def _on_connections(self, data: dict[str, Any]) -> None:
        self.pages["connections"].on_connections(data)

    def _on_log(self, entry: dict[str, Any]) -> None:
        self.pages["logs"].on_log(entry)

    def _on_startup_progress(self, text: str) -> None:
        page = self.pages.get("home")
        if page is not None and hasattr(page, "on_startup_progress"):
            page.on_startup_progress(text)

    def _sync_mode(self, mode: str) -> None:
        self._mode_syncing = True
        try:
            self.header_mode.set_active_id(mode)
        finally:
            self._mode_syncing = False

    # ---------------- 操作 ----------------
    def toggle_core(self) -> None:
        if self.controller.running or self.controller.state == "starting":
            self.toast("正在停止内核…")
            run_async(self.controller.stop, on_error=lambda exc: self.toast(str(exc), error=True), name="stop-core")
        else:
            self.toast("正在启动 mihomo 内核…")
            run_async(self.controller.start, on_success=lambda _r: self.toast("代理已连接", success=True), on_error=lambda exc: self.toast(str(exc), error=True, timeout=8), name="start-core")

    def restart_core(self) -> None:
        if not self.controller.current_profile:
            self.toast("没有可用的配置", error=True)
            return
        self.toast("正在重启内核…")
        run_async(self.controller.restart, on_success=lambda _r: self.toast("内核已重启", success=True), on_error=lambda exc: self.toast(str(exc), error=True, timeout=8), name="restart-core")

    def toggle_system_proxy(self) -> None:
        enabled = not bool(self.controller.settings.system_proxy)
        run_async(
            lambda: self.controller.set_system_proxy(enabled),
            on_success=lambda _r: self.toast("系统代理已" + ("开启" if enabled else "关闭"), success=True),
            on_error=lambda exc: self.toast(str(exc), error=True, timeout=8),
            name="system-proxy",
        )

    def set_mode(self, mode: str) -> None:
        self._sync_mode(mode)
        run_async(lambda: self.controller.set_mode(mode), on_error=lambda exc: self.toast(str(exc), error=True), name="set-mode")

    def present_window(self) -> None:
        self.show()
        self.deiconify()
        self.present()
        try:
            self.get_window().raise_()
        except Exception:
            pass
        page_id = self.stack.get_visible_child_name()
        if page_id and page_id in self.pages and hasattr(self.pages[page_id], "on_show"):
            self.pages[page_id].on_show()
        if page_id:
            self._update_header_meta(page_id)
        GLib.idle_add(self._clear_focus)

    def show_about(self) -> None:
        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_program_name(APP_NAME)
        dialog.set_version(VERSION)
        dialog.set_comments("为 Linux Mint 打造的原生 mihomo 代理客户端。\n基于 Python、GTK 3 与 XApp 构建。")
        dialog.set_copyright("© 2026 Cat Meta Contributors")
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_wrap_license(True)
        dialog.set_authors(["Cat Meta Contributors"])
        dialog.set_documenters(["Cat Meta Contributors"])
        dialog.set_logo_icon_name("cat-meta")
        dialog.set_website("https://github.com/MetaCubeX/mihomo")
        dialog.set_website_label("mihomo 内核项目")
        dialog.connect("response", lambda d, _r: d.destroy())
        dialog.show_all()

    def show_shortcuts(self) -> None:
        dialog = Gtk.Dialog(title="键盘快捷键", transient_for=self, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("关闭", Gtk.ResponseType.CLOSE)
        dialog.connect("response", lambda d, _r: d.destroy())
        content = dialog.get_content_area()
        content.set_margin_top(12)
        content.set_margin_bottom(8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        rows = [
            ("Ctrl + 1…7", "切换页面"),
            ("Ctrl + R", "重启内核"),
            ("Ctrl + ,", "打开设置"),
            ("Ctrl + F", "聚焦当前页面搜索"),
            ("Ctrl + Shift + S", "切换系统代理"),
            ("Ctrl + Q", "退出 Cat Meta"),
        ]
        grid = Gtk.Grid(column_spacing=28, row_spacing=8)
        for index, (key, desc) in enumerate(rows):
            grid.attach(label(key, "mono"), 0, index, 1, 1)
            grid.attach(label(desc), 1, index, 1, 1)
        content.pack_start(grid, False, False, 0)
        dialog.show_all()

    def _register_actions(self) -> None:
        actions = {
            "restart": self.restart_core,
            "toggle-system-proxy": self.toggle_system_proxy,
            "open-data": lambda: open_folder(APP_DATA),
            "shortcuts": self.show_shortcuts,
            "about": self.show_about,
            "settings": lambda: self.navigate("settings"),
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

    # ---------------- 提示与窗口 ----------------
    def toast(self, message: str, *, error: bool = False, success: bool = False, timeout: float = 3.2) -> None:
        message = str(message or "").strip()
        if not message:
            return
        if self._toast_source:
            GLib.source_remove(self._toast_source)
            self._toast_source = 0
        self.info_label.set_text(message)
        self.info_bar.set_message_type(Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO)
        if error:
            timeout = max(timeout, 5.0)
        self.info_revealer.set_no_show_all(False)
        self.info_revealer.show_all()
        self.info_revealer.set_reveal_child(True)
        self._toast_source = GLib.timeout_add(int(timeout * 1000), self._hide_toast)

    def _on_info_bar_response(self, _bar: Gtk.InfoBar, _response: int) -> None:
        self._dismiss_toast()

    def _dismiss_toast(self) -> None:
        if self._toast_source:
            GLib.source_remove(self._toast_source)
            self._toast_source = 0
        self.info_revealer.set_reveal_child(False)

    def _hide_toast(self) -> bool:
        self._toast_source = 0
        self.info_revealer.set_reveal_child(False)
        return False

    def _on_delete(self, *_args) -> bool:
        if self._quitting:
            return False
        if self.controller.settings.close_to_tray and self.controller.settings.show_tray and getattr(self.app, "tray", None) and self.app.tray.available:
            self._save_geometry()
            self.hide()
            return True
        self.app.quit_app()
        return True

    def _on_map_event(self, *_args) -> bool:
        # 忽略窗口管理器在映射初期的尺寸调整，避免把临时尺寸写回设置。
        GLib.timeout_add(800, self._mark_geometry_ready)
        return False

    def _mark_geometry_ready(self) -> bool:
        self._geometry_ready = True
        return False

    def _on_configure(self, _widget, event) -> bool:
        if not self._geometry_ready:
            return False
        if self._geometry_source:
            GLib.source_remove(self._geometry_source)
        self._geometry_source = GLib.timeout_add(600, self._save_geometry)
        return False

    def _save_geometry(self) -> bool:
        self._geometry_source = 0
        try:
            maximized = self.is_maximized()
            values = {"window_maximized": maximized}
            if not maximized:
                width, height = self.get_size()
                gdk_window = self.get_window()
                if width > 0 and height > 0:
                    # set_size_request / 窗口最小尺寸使用外框坐标，而
                    # get_size() 返回内容坐标。保存外框尺寸，同时记录
                    # CSD 内外差值，下次启动时换回内容尺寸。
                    outer_width = gdk_window.get_width() if gdk_window else width
                    outer_height = gdk_window.get_height() if gdk_window else height
                    values["window_width"] = max(MIN_WINDOW_WIDTH, outer_width)
                    values["window_height"] = max(MIN_WINDOW_HEIGHT, outer_height)
                    values["window_inset_width"] = max(0, outer_width - width)
                    values["window_inset_height"] = max(0, outer_height - height)
            self.controller.settings.update(values)
        except Exception:
            pass
        return False

    def _on_key_press(self, _widget, event: Gdk.EventKey) -> bool:
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        keyval = event.keyval
        if ctrl and keyval == Gdk.KEY_q:
            self.app.quit_app()
            return True
        if ctrl and keyval == Gdk.KEY_r:
            self.restart_core()
            return True
        if ctrl and keyval == Gdk.KEY_comma:
            self.navigate("settings")
            return True
        if ctrl and shift and keyval in (Gdk.KEY_S, Gdk.KEY_s):
            self.toggle_system_proxy()
            return True
        if ctrl and keyval == Gdk.KEY_f:
            page = self.pages.get(self.stack.get_visible_child_name() or "home")
            search = getattr(page, "search", None)
            if search:
                search.grab_focus()
            return True
        if ctrl and Gdk.KEY_1 <= keyval <= Gdk.KEY_7:
            self.navigate(PAGE_ORDER[keyval - Gdk.KEY_1])
            return True
        return False

    def prepare_quit(self) -> None:
        self._quitting = True
        self._save_geometry()

    def set_tun_from_tray(self, enabled: bool) -> None:
        run_async(
            lambda: self.controller.set_quick_toggle("tun", enabled),
            on_success=lambda _r: self.toast("TUN 模式已" + ("开启" if enabled else "关闭"), success=True),
            on_error=lambda exc: self.toast(str(exc), error=True, timeout=8),
            name="tray-tun",
        )
