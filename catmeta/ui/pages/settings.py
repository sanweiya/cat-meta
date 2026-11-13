"""设置页：原生分组表单。"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from ... import VERSION
from ...async_utils import run_async
from ...controller import Controller
from ...geodata import download_geodata, local_proxy_ports, status as geodata_status
from ...paths import APP_CONFIG, APP_DATA, APP_LOG, CORE_LOG, LOGS_DIR
from ...system import open_folder, open_uri
from ..widgets.common import (
    IconButton,
    SettingRow,
    SettingsGroup,
    BasePage,
    block_scroll,
    icon,
    label,
)


class SettingsPage(BasePage):
    def __init__(self, controller: Controller, toast: Callable[..., None]):
        super().__init__("设置", "配置代理端口、内核与系统集成")
        self.controller = controller
        self.toast = toast
        self._loading = False
        self._dirty = False
        self._busy = False
        self._download_busy = False
        self._pending_core_path = ""

        self.dirty_badge = label("未保存", "badge badge-orange")
        self.dirty_badge.set_no_show_all(True)
        self.dirty_badge.set_visible(False)
        self.header.actions.pack_start(self.dirty_badge, False, False, 0)
        self.discard_button = Gtk.Button(label="不保存")
        self.discard_button.set_tooltip_text("放弃未保存的修改，恢复到上次保存的状态")
        self.discard_button.set_no_show_all(True)
        self.discard_button.set_visible(False)
        self.discard_button.set_sensitive(False)
        self.discard_button.connect("clicked", lambda _b: self._discard())
        self.header.actions.pack_start(self.discard_button, False, False, 0)
        self.save_button = Gtk.Button(label="保存更改")
        self.save_button.get_style_context().add_class("primary")
        self.save_button.set_sensitive(False)
        self.save_button.connect("clicked", lambda _b: self._save())
        self.header.actions.pack_start(self.save_button, False, False, 0)

        note = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        note.get_style_context().add_class("native-group")
        note.get_style_context().add_class("settings-note")
        note.set_margin_top(10)
        note.set_margin_bottom(10)
        note_icon = icon("dialog-information-symbolic", 16)
        note_icon.set_valign(Gtk.Align.CENTER)
        note.pack_start(note_icon, False, False, 12)
        note_text = label("修改端口、TUN、日志级别或内核路径后，正在运行的内核会自动重启。", "tiny-label", wrap=True)
        note.pack_start(note_text, True, True, 0)
        self.body.pack_start(note, False, False, 0)

        self._build_general()
        self._build_network()
        self._build_core()
        self._build_profiles()
        self._build_about()
        self._load(self.controller.settings.as_dict())

    # ---------------- 控件工厂 ----------------
    def _switch(self, active: bool, callback: Callable[[bool], None] | None = None) -> Gtk.Switch:
        switch = Gtk.Switch()
        switch.set_active(bool(active))
        switch.connect("notify::active", lambda sw, _p: self._changed(sw.get_active() if callback is None else callback(sw.get_active())))
        return switch

    def _spin(self, value: int, low: int, high: int) -> Gtk.SpinButton:
        spin = Gtk.SpinButton.new_with_range(low, high, 1)
        spin.set_value(int(value))
        spin.set_numeric(True)
        spin.set_width_chars(6)
        # 不允许滚轮改数值，避免滚动设置页时误触端口。
        block_scroll(spin)
        spin.connect("value-changed", lambda _s: self._mark_dirty())
        return spin

    def _entry(self, text: str, width: int = 32) -> Gtk.Entry:
        entry = Gtk.Entry()
        entry.set_text(text or "")
        entry.set_width_chars(width)
        entry.set_max_width_chars(width)
        entry.connect("changed", lambda _e: self._mark_dirty())
        return entry

    def _combo(self, options: list[tuple[str, str]], active: str) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        for key, text in options:
            combo.append(key, text)
        combo.set_active_id(active)
        block_scroll(combo)
        combo.connect("changed", lambda _c: self._mark_dirty())
        return combo

    def _button(self, text: str, callback: Callable[[], None], *, icon_name: str | None = None) -> Gtk.Button:
        button = Gtk.Button(label=text)
        button.get_style_context().add_class("settings-action")
        if icon_name:
            button.set_image(icon(icon_name, 14))
            button.set_always_show_image(True)
        button.connect("clicked", lambda _b: callback())
        return button

    # ---------------- 分组 ----------------
    def _build_general(self) -> None:
        self.autostart_switch = self._switch(False)
        self.auto_core_switch = self._switch(False)
        self.minimized_switch = self._switch(False)
        self.tray_switch = self._switch(True)
        self.close_tray_switch = self._switch(True)
        rows = [
            SettingRow("开机自动启动", "登录桌面后自动运行 Cat Meta", self.autostart_switch),
            SettingRow("启动时自动启动内核", "应用启动后自动连接上次使用的配置", self.auto_core_switch),
            SettingRow("启动时最小化", "启动后不显示主窗口，直接驻留系统托盘（需启用托盘图标）", self.minimized_switch),
            SettingRow("显示托盘图标", "在系统托盘中显示状态图标与快捷菜单", self.tray_switch),
            SettingRow("关闭窗口时最小化到托盘", "关闭按钮只隐藏窗口，内核继续运行", self.close_tray_switch),
        ]
        self.body.pack_start(SettingsGroup("常规", rows), False, False, 0)

    def _build_network(self) -> None:
        self.system_proxy_switch = self._switch(False)
        self.tun_switch = self._switch(False)
        self.lan_switch = self._switch(False)
        self.ipv6_switch = self._switch(False)
        self.mixed_port_spin = self._spin(7900, 1024, 65535)
        self.api_port_spin = self._spin(19098, 1024, 65535)
        rows = [
            SettingRow("系统代理", "使用 GSettings 把 Cinnamon 的 HTTP / HTTPS 代理指向本机", self.system_proxy_switch),
            SettingRow("TUN 模式", "创建虚拟网卡接管全部流量，需要额外授权", self.tun_switch),
            SettingRow("允许局域网连接", "允许同一网络中的其他设备通过本机代理上网", self.lan_switch),
            SettingRow("IPv6", "在内核中启用 IPv6 解析与代理", self.ipv6_switch),
            SettingRow("混合代理端口", "HTTP 与 SOCKS5 共用端口，默认 7900", self.mixed_port_spin),
            SettingRow("控制器端口", "mihomo REST / WebSocket API 端口，仅监听 127.0.0.1", self.api_port_spin),
        ]
        self.body.pack_start(SettingsGroup("网络与代理", rows), False, False, 0)

    def _build_core(self) -> None:
        self.core_version_label = label("—", "row-subtitle")
        self.core_version_label.set_xalign(1.0)
        self.download_button = self._button("下载内核", self._download_core, icon_name="document-save-symbolic")
        core_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        core_control.pack_start(self.core_version_label, False, False, 0)
        core_control.pack_start(self.download_button, False, False, 0)
        self.core_path_label = label("自动检测", "row-subtitle", ellipsize=True, max_width=42)
        choose_button = self._button("选择…", self._choose_core, icon_name="document-open-symbolic")
        path_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        path_control.pack_start(self.core_path_label, False, False, 0)
        path_control.pack_start(choose_button, False, False, 0)
        self.tun_perm_label = label("检查中…", "row-subtitle")
        self.tun_auth_button = self._button("授权 TUN", self._authorize_tun, icon_name="system-lock-screen-symbolic")
        perm_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        perm_control.pack_start(self.tun_perm_label, False, False, 0)
        perm_control.pack_start(self.tun_auth_button, False, False, 0)
        self.geo_label = label("检查中…", "row-subtitle")
        self.geo_button = self._button("下载数据", self._download_geo, icon_name="document-save-symbolic")
        geo_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        geo_control.pack_start(self.geo_label, False, False, 0)
        geo_control.pack_start(self.geo_button, False, False, 0)
        self.log_level_combo = self._combo(
            [("debug", "调试（debug）"), ("info", "信息（info）"), ("warning", "警告（warning）"), ("error", "错误（error）")],
            "info",
        )
        self.test_url_entry = self._entry("https://www.gstatic.com/generate_204", 40)

        self.download_progress = Gtk.ProgressBar()
        self.download_progress.set_no_show_all(True)
        self.download_progress.set_visible(False)
        self.download_progress.set_size_request(220, -1)
        self.download_progress_text = label("", "tiny-label")
        self.download_progress_text.set_no_show_all(True)
        self.download_progress_text.set_visible(False)
        progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        progress_box.pack_start(self.download_progress, False, False, 0)
        progress_box.pack_start(self.download_progress_text, False, False, 0)

        rows = [
            SettingRow("mihomo 内核", "自动下载官方稳定版；也可以手动指定可执行文件", core_control),
            SettingRow("内核路径", "留空时自动在数据目录与 PATH 中查找", path_control),
            SettingRow("TUN 权限", "TUN 需要 cap_net_admin 与 cap_net_raw 能力", perm_control),
            SettingRow("Geo 数据", "GEOIP / GEOSITE 规则所需的数据库，首次启动会自动准备", geo_control),
            SettingRow("日志级别", "更详细的日志会占用更多磁盘空间", self.log_level_combo),
            SettingRow("延迟测试地址", "测速使用的 204 地址；需返回空响应", self.test_url_entry),
        ]
        group = SettingsGroup("内核", rows)
        self.body.pack_start(group, False, False, 0)
        progress_holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        progress_holder.set_margin_start(18)
        progress_holder.set_margin_end(18)
        progress_holder.set_margin_top(6)
        progress_holder.pack_start(progress_box, False, False, 0)
        self.body.pack_start(progress_holder, False, False, 0)

    def _build_profiles(self) -> None:
        self.auto_update_switch = self._switch(True)
        self.builtin_editor_switch = self._switch(True)
        self.interval_combo = self._combo(
            [("6", "每 6 小时"), ("12", "每 12 小时"), ("24", "每 24 小时"), ("72", "每 3 天")],
            "24",
        )
        check_button = self._button("立即检查", self._check_updates, icon_name="view-refresh-symbolic")
        data_button = self._button("打开目录", lambda: open_folder(APP_DATA), icon_name="folder-open-symbolic")
        config_button = self._button("配置目录", lambda: open_folder(APP_CONFIG), icon_name="folder-symbolic")
        data_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        data_control.pack_start(data_button, False, False, 0)
        data_control.pack_start(config_button, False, False, 0)
        rows = [
            SettingRow("自动更新订阅", "在后台定期更新所有启用的远程订阅", self.auto_update_switch),
            SettingRow("更新间隔", "仅在自动更新开启时生效", self.interval_combo),
            SettingRow("检查订阅更新", "立即检查并下载已到期的订阅", check_button),
            SettingRow("使用内置文本编辑器", "关闭后调用系统默认编辑器", self.builtin_editor_switch),
            SettingRow("数据目录", "配置、运行时文件与内核都保存在此目录", data_control),
        ]
        self.body.pack_start(SettingsGroup("订阅", rows), False, False, 0)

    def _build_about(self) -> None:
        app_icon = icon("cat-meta", 40)
        app_icon.set_pixel_size(40)
        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        hero.get_style_context().add_class("card")
        hero.get_style_context().add_class("about-hero")
        hero.pack_start(app_icon, False, False, 0)
        hero_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        hero_text.pack_start(label(f"Cat Meta {VERSION}", "profile-title"), False, False, 0)
        hero_text.pack_start(label("为 Linux Mint 打造的原生 mihomo 代理客户端", "row-subtitle"), False, False, 0)
        hero_text.pack_start(label("Python · GTK 3 · XApp · mihomo", "tiny-label"), False, False, 0)
        hero.pack_start(hero_text, True, True, 0)
        self.body.pack_start(hero, False, False, 0)

        self.about_core_label = label("—", "row-subtitle")
        version_row = SettingRow("mihomo 内核", "由 MetaCubeX 维护的高性能规则代理内核", self.about_core_label)
        links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        links.pack_start(self._button("mihomo 项目", lambda: open_uri("https://github.com/MetaCubeX/mihomo"), icon_name="web-browser-symbolic"), False, False, 0)
        links.pack_start(self._button("XApp", lambda: open_uri("https://github.com/linuxmint/xapp"), icon_name="web-browser-symbolic"), False, False, 0)
        logs_control = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        logs_control.pack_start(self._button("日志目录", lambda: open_folder(LOGS_DIR), icon_name="folder-symbolic"), False, False, 0)
        logs_control.pack_start(self._button("内核日志", lambda: open_uri(CORE_LOG.as_uri()), icon_name="text-x-generic-symbolic"), False, False, 0)
        rows = [
            version_row,
            SettingRow("开源组件", "PyYAML（MIT）· mihomo（GPL-3.0）· XApp（LGPL-2.1）", links),
            SettingRow("调试日志", "应用日志位于 ~/.local/share/cat-meta/logs/", logs_control),
        ]
        self.body.pack_start(SettingsGroup("关于", rows), False, False, 0)
        footer = label("Cat Meta 以 GPL-3.0 许可证发布。界面使用系统主题，跟随 Mint 的字体、颜色与高对比度设置。", "tiny-label", xalign=0.5, wrap=True)
        footer.set_margin_top(4)
        self.body.pack_start(footer, False, False, 0)

    # ---------------- 状态 ----------------
    def _load(self, values: dict[str, Any]) -> None:
        self._loading = True
        try:
            self.autostart_switch.set_active(bool(values.get("auto_start")))
            self.auto_core_switch.set_active(bool(values.get("auto_start_core")))
            self.minimized_switch.set_active(bool(values.get("start_minimized")))
            self.tray_switch.set_active(bool(values.get("show_tray")))
            self.close_tray_switch.set_active(bool(values.get("close_to_tray")))
            self.system_proxy_switch.set_active(bool(values.get("system_proxy")))
            self.tun_switch.set_active(bool(values.get("tun")))
            self.lan_switch.set_active(bool(values.get("allow_lan")))
            self.ipv6_switch.set_active(bool(values.get("ipv6")))
            self.mixed_port_spin.set_value(int(values.get("mixed_port") or 7900))
            self.api_port_spin.set_value(int(values.get("api_port") or 19098))
            self.log_level_combo.set_active_id(str(values.get("log_level") or "info"))
            self.test_url_entry.set_text(str(values.get("test_url") or ""))
            self.auto_update_switch.set_active(bool(values.get("auto_update_profiles")))
            self.builtin_editor_switch.set_active(bool(values.get("use_builtin_editor", True)))
            self.interval_combo.set_active_id(str(int(values.get("update_interval_hours") or 24)))
            self._pending_core_path = str(values.get("core_path") or "")
        finally:
            self._loading = False
        self._refresh_core_info()
        self._set_dirty(False)

    def _refresh_core_info(self, force: bool = False) -> None:
        core = self.controller.core
        # 读取版本要启动一次 `mihomo -v` 子进程：只有内核文件变化时才强制刷新。
        version = core.version(refresh=force)
        binary = core.binary
        self.core_version_label.set_text(f"mihomo {version}" if version else "未安装")
        if binary:
            self.core_path_label.set_text(str(binary))
            self.core_path_label.set_tooltip_text(str(binary))
            self.download_button.set_label("更新内核")
        else:
            self.core_path_label.set_text("自动检测（未找到）")
            self.download_button.set_label("下载内核")
        self.about_core_label.set_text(f"mihomo {version}" if version else "未安装")
        self._refresh_tun_status()
        self._refresh_geo_status()

    def _refresh_geo_status(self) -> None:
        state = geodata_status()
        missing = [name for name, ready in (("GeoIP", state["geoip"]), ("GeoSite", state["geosite"])) if not ready]
        if missing:
            self.geo_label.set_text("缺失：" + "、".join(missing))
            self.geo_button.set_label("下载数据")
        else:
            self.geo_label.set_text("GeoIP 与 GeoSite 已就绪")
            self.geo_button.set_label("重新下载")

    def _refresh_tun_status(self) -> None:
        core = self.controller.core
        if not core.binary:
            self.tun_perm_label.set_text("需要先安装内核")
            self.tun_auth_button.set_sensitive(False)
            return
        available = core.net_admin_available()
        self.tun_perm_label.set_text("已具备 TUN 权限" if available else "未授权")
        self.tun_auth_button.set_sensitive(not available)
        if available:
            self.tun_auth_button.set_label("已授权")
        else:
            self.tun_auth_button.set_label("授权 TUN")

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._set_dirty(True)

    def _changed(self, value: Any) -> Any:
        self._mark_dirty()
        return value

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.dirty_badge.set_visible(dirty)
        self.discard_button.set_visible(dirty)
        self.discard_button.set_sensitive(dirty and not self._busy)
        self.save_button.set_sensitive(dirty and not self._busy)

    def on_show(self) -> None:
        super().on_show()
        if not self._dirty:
            self._load(self.controller.settings.as_dict())
            self._refresh_core_info()

    def on_settings(self, values: dict[str, Any]) -> None:
        if not self._dirty:
            self._load(values)
        else:
            self._refresh_core_info()

    def on_state(self, state: str, error: str = "") -> None:
        self._refresh_core_info()

    # ---------------- 保存 ----------------
    def _collect(self) -> dict[str, Any]:
        return {
            "auto_start": self.autostart_switch.get_active(),
            "auto_start_core": self.auto_core_switch.get_active(),
            "start_minimized": self.minimized_switch.get_active(),
            "show_tray": self.tray_switch.get_active(),
            "close_to_tray": self.close_tray_switch.get_active(),
            "system_proxy": self.system_proxy_switch.get_active(),
            "tun": self.tun_switch.get_active(),
            "allow_lan": self.lan_switch.get_active(),
            "ipv6": self.ipv6_switch.get_active(),
            "mixed_port": int(self.mixed_port_spin.get_value()),
            "api_port": int(self.api_port_spin.get_value()),
            "log_level": self.log_level_combo.get_active_id() or "info",
            "test_url": self.test_url_entry.get_text().strip(),
            "auto_update_profiles": self.auto_update_switch.get_active(),
            "use_builtin_editor": self.builtin_editor_switch.get_active(),
            "update_interval_hours": int(self.interval_combo.get_active_id() or "24"),
            "core_path": self._pending_core_path,
        }

    def _save(self) -> None:
        if self._busy:
            return
        values = self._collect()
        if not values["test_url"].startswith(("http://", "https://")):
            self.toast("延迟测试地址必须是 http/https URL", error=True)
            return
        if values["tun"] and not self.controller.core.net_admin_available():
            self.toast("请先在“内核 → TUN 权限”中完成授权", error=True, timeout=7)
            return
        self._busy = True
        self.save_button.set_sensitive(False)
        self.discard_button.set_sensitive(False)
        self.save_button.set_label("应用中…")

        def action() -> None:
            self.controller.save_settings(values, restart=True)

        def done(_r) -> None:
            self._busy = False
            self.save_button.set_label("保存更改")
            self._set_dirty(False)
            self._load(self.controller.settings.as_dict())
            self.toast("设置已保存并应用", success=True)

        def failed(exc: Exception) -> None:
            self._busy = False
            self.save_button.set_label("保存更改")
            self._set_dirty(True)
            self.toast(str(exc), error=True, timeout=8)
            self._refresh_core_info()

        run_async(action, on_success=done, on_error=failed, name="save-settings")

    def _discard(self) -> None:
        if self._busy or not self._dirty:
            return
        self._load(self.controller.settings.as_dict())
        self.toast("已恢复到上次保存的状态")

    # ---------------- 内核操作 ----------------
    def _download_core(self) -> None:
        if self._download_busy:
            return
        self._download_busy = True
        self.download_button.set_sensitive(False)
        self.download_button.set_label("下载中…")
        self.download_progress.set_visible(True)
        self.download_progress_text.set_visible(True)
        self.download_progress.set_fraction(0.0)

        def progress(percent: int, text: str) -> None:
            GLib.idle_add(self._on_download_progress, percent, text)

        run_async(lambda: self.controller.core.download_core(progress), on_success=self._download_done, on_error=self._download_failed, name="download-core")

    def _on_download_progress(self, percent: int, text: str) -> bool:
        self.download_progress.set_fraction(percent / 100.0)
        self.download_progress_text.set_text(f"{percent}%")
        self.download_button.set_label(text.replace("正在", "").replace("…", ""))
        return False

    def _download_done(self, path) -> None:
        self._download_busy = False
        self.download_button.set_sensitive(True)
        self.download_progress.set_visible(False)
        self.download_progress_text.set_visible(False)
        self._pending_core_path = str(path)
        self._refresh_core_info(force=True)
        self.toast("mihomo 内核已安装，重启内核后生效", success=True, timeout=6)
        if self.controller.running:
            self._mark_dirty()

    def _download_failed(self, exc: Exception) -> None:
        self._download_busy = False
        self.download_button.set_sensitive(True)
        self.download_progress.set_visible(False)
        self.download_progress_text.set_visible(False)
        self._refresh_core_info()
        self.toast(f"内核下载失败：{exc}", error=True, timeout=8)

    def _choose_core(self) -> None:
        dialog = Gtk.FileChooserDialog(title="选择 mihomo 可执行文件", transient_for=self.get_toplevel(), action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "选择", Gtk.ResponseType.OK)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("可执行文件")
        file_filter.add_pattern("*")
        dialog.add_filter(file_filter)

        def on_response(dlg: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.OK and dlg.get_filename():
                path = dlg.get_filename()
                if not os.access(path, os.X_OK):
                    self.toast("该文件不可执行", error=True)
                else:
                    self._pending_core_path = path
                    self.core_path_label.set_text(path)
                    self._mark_dirty()
            dlg.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()

    def _authorize_tun(self) -> None:
        self.tun_auth_button.set_sensitive(False)
        self.tun_auth_button.set_label("授权中…")
        self.toast("请在系统授权窗口中确认")

        def done(result) -> None:
            ok, message = result if isinstance(result, tuple) else (False, str(result))
            self._refresh_tun_status()
            self.toast(message if ok else f"授权失败：{message}", success=ok, error=not ok, timeout=8)

        def failed(exc: Exception) -> None:
            self._refresh_tun_status()
            self.toast(f"授权失败：{exc}", error=True, timeout=8)

        run_async(self.controller.core.grant_tun_permissions, on_success=done, on_error=failed, name="tun-auth")

    def _download_geo(self) -> None:
        self.geo_button.set_sensitive(False)
        self.geo_button.set_label("下载中…")
        self.toast("正在下载 GeoIP / GeoSite 数据…")

        def progress(percent: int, text: str) -> None:
            GLib.idle_add(self.geo_label.set_text, f"{text} · {percent}%")

        def action() -> None:
            download_geodata(
                ["geoip", "geosite"],
                proxy_ports=local_proxy_ports(int(self.controller.settings.mixed_port)),
                progress=progress,
            )

        def done(_r) -> None:
            self.geo_button.set_sensitive(True)
            self._refresh_geo_status()
            self.toast("Geo 数据已更新", success=True)

        def failed(exc: Exception) -> None:
            self.geo_button.set_sensitive(True)
            self._refresh_geo_status()
            self.toast(str(exc), error=True, timeout=8)

        run_async(action, on_success=done, on_error=failed, name="geo-data")

    def _check_updates(self) -> None:
        if not self.controller.settings.auto_update_profiles:
            self.toast("请先开启自动更新")
        self.controller.maybe_auto_update(force=True)
        self.toast("已在后台检查订阅更新")
