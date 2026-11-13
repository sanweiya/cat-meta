"""Gtk.Application 入口。"""

from __future__ import annotations

import logging
import os
import re
import signal
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME, VERSION
from .async_utils import run_async
from .controller import Controller
from .logging_setup import setup_logging
from .paths import CSS_FILE, ICONS_DIR, ensure_dirs, install_user_icons
from .system import notify
from .ui.fonts import ui_font_scale
from .tray import TrayIcon
from .ui.window import MainWindow

log = logging.getLogger("catmeta.app")

_FONT_SIZE_RE = re.compile(r"font-size:\s*([0-9.]+)px;")


def _scaled_css() -> bytes:
    text = CSS_FILE.read_text(encoding="utf-8")
    scale = ui_font_scale()
    if abs(scale - 1.0) > 0.001:
        text = _FONT_SIZE_RE.sub(
            lambda match: f"font-size: {float(match.group(1)) * scale:.2f}px;",
            text,
        )
    return text.encode("utf-8")


def _load_css() -> Gtk.CssProvider | None:
    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(_scaled_css())
    except Exception as exc:
        log.error("加载样式失败：%s", exc)
        return None
    screen = Gdk.Screen.get_default()
    if screen:
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    return provider


class CatMetaApplication(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self.window: MainWindow | None = None
        self.tray: TrayIcon | None = None
        self.controller: Controller | None = None
        self._startup_minimized = False
        self._startup_page = ""
        self._quitting = False
        self._restore_source = 0
        self._auto_update_source = 0
        self._css_provider: Gtk.CssProvider | None = None
        self._font_settings: list[Gio.Settings] = []

    # ---------------- 生命周期 ----------------
    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        ensure_dirs()
        self.controller = Controller()
        self.controller.start_ticker()
        self.controller.subscribe("settings", self._on_settings)
        self._install_actions()

        install_user_icons()
        theme = Gtk.IconTheme.get_default()
        theme.append_search_path(str(ICONS_DIR))
        try:
            theme.rescan_if_needed()
        except Exception:
            pass
        Gtk.Window.set_default_icon_name("cat-meta")
        self._css_provider = _load_css()
        self._watch_font_settings()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)
            except Exception:
                pass
        self.hold()
        log.info("Cat Meta %s 启动", VERSION)

    def do_activate(self) -> None:
        if self.window is not None:
            if self._startup_page:
                self.window.navigate(self._startup_page, save=False)
                self._startup_page = ""
            self.window.present_window()
            return
        assert self.controller is not None
        try:
            self.window = MainWindow(self, self.controller)
        except Exception:
            log.exception("创建主窗口失败")
            raise
        self.window.connect("destroy", self._on_window_destroy)
        self._ensure_tray()
        self.window.show_all()
        if self._startup_page:
            self.window.navigate(self._startup_page, save=False)
        minimize_to_tray = (
            (self._startup_minimized or bool(self.controller.settings.start_minimized))
            and self.tray is not None
            and self.tray.available
            and bool(self.controller.settings.show_tray)
        )
        if minimize_to_tray:
            log.info("启动时最小化到托盘")
            self.window.hide()
        else:
            self.window.present_window()
        self.window._on_state(self.controller.state, self.controller.last_error)
        self._schedule_restore()
        self._auto_update_source = GLib.timeout_add_seconds(120, self._auto_update_tick)
        self.release()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        args = command_line.get_arguments()
        for arg in args[1:]:
            if arg == "--minimized":
                self._startup_minimized = True
            elif arg.startswith("--page="):
                page = arg.split("=", 1)[1]
                if page in ("home", "proxies", "profiles", "connections", "rules", "logs", "settings"):
                    self._startup_page = page
        self.activate()
        return 0

    def do_open(self, files, n_files, hint) -> None:
        # 从文件管理器双击 YAML 时，导入为本地配置。
        if not self.controller:
            return
        for file in files[:3]:
            path = file.get_path()
            if not path:
                continue
            try:
                profile = self.controller.add_profile_local(path)
                self.activate()
                if self.window:
                    self.window.navigate("profiles")
                    self.window.toast(f"已导入「{profile.name}」", success=True)
            except Exception as exc:
                if self.window:
                    self.window.toast(str(exc), error=True)
        self.activate()

    def do_shutdown(self) -> None:
        if self._auto_update_source:
            GLib.source_remove(self._auto_update_source)
            self._auto_update_source = 0
        if self.window:
            try:
                self.window.prepare_quit()
            except Exception:
                pass
        if self.controller:
            try:
                self.controller.shutdown()
            except Exception:
                log.exception("关闭内核失败")
        if self.tray:
            self.tray.set_visible(False)
        log.info("Cat Meta 退出")
        Gtk.Application.do_shutdown(self)

    # ---------------- 字体缩放 ----------------
    def _watch_font_settings(self) -> None:
        try:
            gtk_settings = Gtk.Settings.get_default()
            if gtk_settings:
                gtk_settings.connect("notify::gtk-font-name", self._reload_css)
                gtk_settings.connect("notify::gtk-theme-name", self._reload_css)
                gtk_settings.connect("notify::gtk-application-prefer-dark-theme", self._reload_css)
        except Exception:
            pass
        for schema in ("org.cinnamon.desktop.interface", "org.gnome.desktop.interface"):
            try:
                settings = Gio.Settings.new(schema)
                if not settings.get_value("text-scaling-factor"):
                    continue
                settings.connect("changed::text-scaling-factor", self._reload_css)
                self._font_settings.append(settings)
            except Exception:
                continue

    def _reload_css(self, *_args) -> None:
        if self._css_provider is None:
            return
        try:
            self._css_provider.load_from_data(_scaled_css())
            if self.window:
                for page in self.window.pages.values():
                    refresh = getattr(page, "refresh_fonts", None)
                    if callable(refresh):
                        refresh()
        except Exception as exc:
            log.error("重新加载样式失败：%s", exc)

    # ---------------- 操作 ----------------
    def _install_actions(self) -> None:
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda _a, _p: self.quit_app())
        self.add_action(quit_action)
        show_action = Gio.SimpleAction.new("show", None)
        show_action.connect("activate", lambda _a, _p: self.window.present_window() if self.window else self.activate())
        self.add_action(show_action)
        toggle_action = Gio.SimpleAction.new("toggle-core", None)
        toggle_action.connect("activate", lambda _a, _p: self.window.toggle_core() if self.window else None)
        self.add_action(toggle_action)
        page_action = Gio.SimpleAction.new("page", GLib.VariantType.new("s"))
        page_action.connect("activate", self._on_page_action)
        self.add_action(page_action)

    def _on_signal(self) -> bool:
        log.info("收到退出信号")
        self.quit_app()
        return False

    def _on_page_action(self, _action, parameter) -> None:
        if self.window is None:
            self.activate()
        if self.window and parameter is not None:
            self.window.navigate(parameter.get_string())

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.window:
            try:
                self.window.prepare_quit()
            except Exception:
                pass
        if self.controller:
            try:
                self.controller.shutdown()
            except Exception:
                log.exception("退出时停止内核失败")
        if self.tray:
            self.tray.set_visible(False)
        self.quit()

    def _on_window_destroy(self, _window) -> None:
        self.window = None
        if not self._quitting and not (self.tray and self.tray.available):
            self.quit_app()

    def _ensure_tray(self) -> None:
        if not self.controller or not self.controller.settings.show_tray:
            return
        if self.window is None:
            return
        if self.tray is None:
            self.tray = TrayIcon(self, self.window, self.controller)
        self.tray.set_visible(True)
        self.tray.update()

    def _on_settings(self, values: dict) -> None:
        if self.window is None:
            return
        if values.get("show_tray"):
            self._ensure_tray()
        elif self.tray:
            self.tray.set_visible(False)
        if self.tray:
            self.tray.update()

    def _schedule_restore(self) -> None:
        if self._restore_source:
            GLib.source_remove(self._restore_source)
        self._restore_source = GLib.timeout_add_seconds(1, self._restore_session)

    def _restore_session(self) -> bool:
        self._restore_source = 0
        if not self.controller or self.window is None:
            return False
        auto_core = bool(self.controller.settings.auto_start_core)
        restore_proxy = bool(self.controller.settings.system_proxy)
        # 自动启动内核，或上次开启了系统代理时恢复内核，避免代理指向空端口。
        if (auto_core or restore_proxy) and not self.controller.running:
            reason = "自动启动内核" if auto_core else "恢复系统代理会话"

            def started(_r) -> None:
                if self.window:
                    self.window.toast("内核已自动启动" if auto_core else "已恢复上次的系统代理会话", success=True)

            def failed(exc: Exception) -> None:
                log.warning("%s失败：%s", reason, exc)
                if self.window:
                    self.window.toast(f"{reason}失败：{exc}", error=True, timeout=8)

            log.info("启动时%s", reason)
            run_async(self.controller.start, on_success=started, on_error=failed, name="restore-session")
        return False

    def _auto_update_tick(self) -> bool:
        if self.controller:
            try:
                self.controller.maybe_auto_update()
            except Exception:
                log.exception("自动更新检查失败")
        return True
