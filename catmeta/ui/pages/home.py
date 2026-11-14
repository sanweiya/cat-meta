"""概览页：扁平分组、原生状态行与实时流量。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ...async_utils import run_async
from ...controller import MODE_LABELS, Controller
from ...util import format_bytes, format_duration, format_speed
from ..widgets.chart import DOWN_COLOR, UP_COLOR, TrafficChart
from ..widgets.common import BasePage, icon, label


def speed_parts(value: Any) -> tuple[str, str]:
    text = format_speed(value)
    match = re.match(r"([\d.]+)\s*(.*)", text)
    return (match.group(1), match.group(2)) if match else (text, "")


class HomePage(BasePage):
    def __init__(self, controller: Controller, toast: Callable[..., None], on_navigate: Callable[[str], None]):
        super().__init__("概览", "代理状态、实时流量与快捷控制")
        self.controller = controller
        self.toast = toast
        self.on_navigate = on_navigate
        self._updating = False

        self._build_status()
        self._build_stats()
        self._build_traffic()
        self._build_quick_switches()
        self.refresh_all()

    # ---------------- 构建 ----------------
    def _section(self, text: str) -> None:
        self.body.pack_start(self.add_section_heading(text), False, False, 0)

    def _build_status(self) -> None:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        group.get_style_context().add_class("native-group")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.get_style_context().add_class("status-summary")
        self.status_dot = label("●", "status-dot")
        self.status_dot.set_valign(Gtk.Align.START)
        self.status_dot.set_margin_top(3)
        row.pack_start(self.status_dot, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.status_title = label("内核未运行", "status-title")
        self.status_spinner = Gtk.Spinner()
        self.status_spinner.set_no_show_all(True)
        self.status_spinner.set_visible(False)
        title_row.pack_start(self.status_title, False, False, 0)
        title_row.pack_start(self.status_spinner, False, False, 0)
        text.pack_start(title_row, False, False, 0)
        self.status_subtitle = label("启动内核后即可使用代理", "status-subtitle", wrap=True)
        text.pack_start(self.status_subtitle, False, False, 0)
        self.node_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.node_row.get_style_context().add_class("current-node")
        self.node_row.set_no_show_all(True)
        self.node_row.set_visible(False)
        self.node_icon = icon("network-vpn-symbolic", 13)
        self.node_row.pack_start(self.node_icon, False, False, 0)
        self.node_value = label("—", "current-node-value", ellipsize=True, max_width=42)
        self.node_group = label("", "current-node-group", ellipsize=True, max_width=18)
        self.node_row.pack_start(self.node_value, False, False, 0)
        self.node_row.pack_start(self.node_group, False, False, 0)
        text.pack_start(self.node_row, False, False, 0)
        self.error_label = label("", "error-label", wrap=True)
        self.error_label.set_no_show_all(True)
        self.error_label.set_visible(False)
        text.pack_start(self.error_label, False, False, 0)
        row.pack_start(text, True, True, 0)

        power = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        power.set_valign(Gtk.Align.CENTER)
        self.power_label = label("启用", "tiny-label")
        self.power_switch = Gtk.Switch()
        self.power_switch.set_valign(Gtk.Align.CENTER)
        self.power_switch.connect("notify::active", self._on_power_switch)
        power.pack_start(self.power_label, False, False, 0)
        power.pack_start(self.power_switch, False, False, 0)
        row.pack_end(power, False, False, 0)
        group.pack_start(row, False, False, 0)
        self.body.pack_start(group, False, False, 0)

    def _build_stats(self) -> None:
        group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        group.get_style_context().add_class("native-stats")
        self.stat_down = self._stat_cell(group, "下载速度", "0", "B/s")
        self.stat_up = self._stat_cell(group, "上传速度", "0", "B/s")
        self.stat_connections = self._stat_cell(group, "活动连接", "0", "个")
        self.stat_memory = self._stat_cell(group, "内存占用", "0", "MB")
        self.body.pack_start(group, False, False, 0)

    def _stat_cell(self, parent: Gtk.Box, title: str, value: str, unit: str) -> tuple[Gtk.Label, Gtk.Label]:
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        cell.get_style_context().add_class("stat-cell")
        cell.set_hexpand(True)
        cell.pack_start(label(title, "stat-title"), False, False, 0)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        value_label = label(value, "stat-value")
        unit_label = label(unit, "stat-unit")
        unit_label.set_valign(Gtk.Align.END)
        unit_label.set_margin_bottom(2)
        line.pack_start(value_label, False, False, 0)
        line.pack_start(unit_label, False, False, 0)
        cell.pack_start(line, False, False, 0)
        parent.pack_start(cell, True, True, 0)
        return value_label, unit_label

    def _build_traffic(self) -> None:
        self._section("实时流量")
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.get_style_context().add_class("traffic-panel")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.set_margin_top(7)
        head.set_margin_start(12)
        head.set_margin_end(12)
        legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        legend.pack_start(self._legend(DOWN_COLOR, "下载"), False, False, 0)
        legend.pack_start(self._legend(UP_COLOR, "上传"), False, False, 0)
        head.pack_start(legend, False, False, 0)
        head.pack_end(label("最近 60 秒", "tiny-label"), False, False, 0)
        panel.pack_start(head, False, False, 0)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.get_style_context().add_class("chart-frame")
        self.chart = TrafficChart(60)
        frame.pack_start(self.chart, True, True, 0)
        panel.pack_start(frame, False, False, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        footer.set_margin_start(12)
        footer.set_margin_end(12)
        footer.set_margin_bottom(7)
        self.total_down_label = label("下载 0 B", "tiny-label")
        self.total_up_label = label("上传 0 B", "tiny-label")
        footer.pack_start(self.total_down_label, False, False, 0)
        footer.pack_start(self.total_up_label, False, False, 0)
        panel.pack_start(footer, False, False, 0)
        self.body.pack_start(panel, False, False, 0)

    def _legend(self, color: tuple[float, float, float], text: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        dot = Gtk.Label()
        hex_color = "#%02x%02x%02x" % (int(color[0] * 255), int(color[1] * 255), int(color[2] * 255))
        dot.set_markup(f'<span foreground="{hex_color}" size="small">●</span>')
        dot.get_style_context().add_class("legend-dot")
        box.pack_start(dot, False, False, 0)
        box.pack_start(label(text, "tiny-label"), False, False, 0)
        return box

    def _build_quick_switches(self) -> None:
        self._section("快捷开关")
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        group.get_style_context().add_class("native-group")
        if self.controller.system_proxy.backend == "gsettings":
            proxy_tip = "将桌面环境的 HTTP / HTTPS 代理指向本机"
        else:
            proxy_tip = "写入会话代理环境变量，重新登录后生效"
        self.proxy_switch = self._quick_row(group, "系统代理", proxy_tip, self._on_system_proxy)
        self.tun_switch = self._quick_row(group, "TUN 模式", "接管全部流量，需要网络管理权限", self._on_tun)
        self.lan_switch = self._quick_row(group, "允许局域网", "允许同一网络内的其他设备使用此代理", self._on_lan)
        self.body.pack_start(group, False, False, 0)

    def _quick_row(self, parent: Gtk.Box, title: str, subtitle: str, callback: Callable[[bool], None]) -> Gtk.Switch:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.get_style_context().add_class("native-row")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.pack_start(label(title, "row-title"), False, False, 0)
        text.pack_start(label(subtitle, "row-subtitle", wrap=True), False, False, 0)
        row.pack_start(text, True, True, 0)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", lambda sw, _p: (not self._updating) and callback(sw.get_active()))
        row.pack_end(switch, False, False, 0)
        parent.pack_start(row, False, False, 0)
        return switch

    # ---------------- 事件 ----------------
    def on_show(self) -> None:
        super().on_show()
        self.chart.samples.clear()
        for _ts, up, down in self.controller.history:
            self.chart.add_sample(up, down)
        self.refresh_all()
        self.controller.refresh_current_node()

    def refresh_all(self) -> None:
        self.on_state(self.controller.state, self.controller.last_error)
        self.on_current_node(self.controller.current_proxy_group, self.controller.current_proxy_name)
        self.on_traffic()
        self.on_metrics()
        self.on_profiles()
        self.on_settings(self.controller.settings.as_dict())

    def on_state(self, state: str, error: str = "") -> None:
        self._updating = True
        self.power_switch.set_active(state in ("running", "starting"))
        self.power_switch.set_sensitive(state not in ("starting", "stopping"))
        self._updating = False
        context = self.status_dot.get_style_context()
        for css in ("running", "starting", "error"):
            context.remove_class(css)
        if state in ("running", "starting", "error"):
            context.add_class(state)
        if state == "running":
            self.status_title.set_text("代理已连接")
            self.status_spinner.stop()
            self.status_spinner.set_visible(False)
            self.power_label.set_text("已启用")
            self.error_label.set_visible(False)
        elif state == "starting":
            self.status_title.set_text("正在启动内核…")
            self.status_spinner.set_visible(True)
            self.status_spinner.start()
            self.power_label.set_text("启动中")
            self.error_label.set_visible(False)
        elif state == "stopping":
            self.status_title.set_text("正在停止…")
            self.status_spinner.set_visible(False)
            self.power_label.set_text("停止中")
        elif state == "error":
            self.status_title.set_text("启动失败")
            self.status_spinner.stop()
            self.status_spinner.set_visible(False)
            self.power_label.set_text("重试")
            self.error_label.set_text(error or "请检查日志")
            self.error_label.set_visible(bool(error))
        else:
            self.status_title.set_text("内核未运行")
            self.status_spinner.stop()
            self.status_spinner.set_visible(False)
            self.power_label.set_text("启用")
            self.error_label.set_visible(False)
        profile = self.controller.current_profile
        mode = MODE_LABELS.get(self.controller.mode(), self.controller.mode())
        if state == "running":
            self.status_subtitle.set_text(
                f"{profile.name if profile else '未选择配置'} · {mode}模式 · 已运行 {format_duration(self.controller.session_summary()['uptime'])}"
            )
        elif state == "starting":
            self.status_subtitle.set_text(
                f"{profile.name if profile else '未选择配置'} · 正在校验配置并连接控制器；首次启动可能需要下载 Geo 数据…"
            )
        elif state == "error":
            self.status_subtitle.set_text("查看日志以了解详细原因")
        else:
            self.status_subtitle.set_text(
                f"{profile.name if profile else '未选择配置'} · 启动内核后即可使用代理"
            )
        self._sync_node_row()

    def _sync_node_row(self) -> None:
        running = self.controller.state == "running"
        if not running:
            self.node_row.set_visible(False)
            return
        name = self.controller.current_proxy_name
        group = self.controller.current_proxy_group
        self.node_value.set_text(name or "读取中…")
        self.node_group.set_text(f"· {group}" if group else "")
        self._show_node_row(group)

    def _show_node_row(self, group: str) -> None:
        for widget in (self.node_icon, self.node_value):
            widget.show()
        self.node_group.set_visible(bool(group))
        self.node_row.set_visible(True)

    def on_current_node(self, group: str, name: str) -> None:
        if self.controller.state == "running":
            self.node_value.set_text(name or "读取中…")
            self.node_group.set_text(f"· {group}" if group else "")
            self._show_node_row(group)
        else:
            self.node_row.set_visible(False)

    def on_startup_progress(self, text: str) -> None:
        if self.controller.state == "starting":
            self.status_subtitle.set_text(text)

    def on_traffic(self) -> None:
        if not self.get_mapped():
            return
        summary = self.controller.session_summary()
        down, down_unit = speed_parts(summary["down"])
        up, up_unit = speed_parts(summary["up"])
        self.stat_down[0].set_text(down)
        self.stat_down[1].set_text(down_unit)
        self.stat_up[0].set_text(up)
        self.stat_up[1].set_text(up_unit)
        self.total_down_label.set_text(f"下载 {format_bytes(summary['total_down'])}")
        self.total_up_label.set_text(f"上传 {format_bytes(summary['total_up'])}")
        if self.controller.history:
            last = self.controller.history[-1]
            self.chart.add_sample(last[1], last[2])

    def on_metrics(self) -> None:
        if not self.get_mapped():
            return
        summary = self.controller.session_summary()
        self.stat_connections[0].set_text(str(summary["connections"]))
        self.stat_memory[0].set_text(f"{summary['memory'] / 1024 / 1024:.0f}" if summary["memory"] else "0")
        if self.controller.state == "running":
            profile = self.controller.current_profile
            mode = MODE_LABELS.get(self.controller.mode(), self.controller.mode())
            self.status_subtitle.set_text(
                f"{profile.name if profile else '未选择配置'} · {mode}模式 · 已运行 {format_duration(summary['uptime'])}"
            )

    def on_profiles(self) -> None:
        self.on_state(self.controller.state, self.controller.last_error)
        self.on_settings(self.controller.settings.as_dict())

    def on_tick(self) -> None:
        if self.get_mapped() and self.controller.state == "running":
            self.on_metrics()

    def on_settings(self, values: dict[str, Any]) -> None:
        self._updating = True
        try:
            self.proxy_switch.set_active(bool(values.get("system_proxy")))
            self.tun_switch.set_active(bool(values.get("tun")))
            self.lan_switch.set_active(bool(values.get("allow_lan")))
        finally:
            self._updating = False

    # ---------------- 操作 ----------------
    def _on_power_switch(self, switch: Gtk.Switch, _param) -> None:
        if self._updating:
            return
        if switch.get_active():
            self.toast("正在启动 mihomo 内核…")
            run_async(self.controller.start, on_error=lambda exc: self._action_error(exc, switch, False), name="start")
        else:
            run_async(self.controller.stop, on_error=lambda exc: self._action_error(exc, switch, True), name="stop")

    def _action_error(self, exc: Exception, switch: Gtk.Switch | None = None, revert: bool | None = None) -> None:
        self.toast(str(exc), error=True, timeout=7)
        if switch is not None and revert is not None:
            self._updating = True
            switch.set_active(revert)
            self._updating = False

    def _on_system_proxy(self, enabled: bool) -> None:
        run_async(
            lambda: self.controller.set_system_proxy(enabled),
            on_success=lambda _r: self.toast("系统代理已" + ("开启" if enabled else "关闭"), success=True),
            on_error=lambda exc: self._action_error(exc, self.proxy_switch, not enabled),
            name="system-proxy",
        )

    def _on_tun(self, enabled: bool) -> None:
        run_async(
            lambda: self.controller.set_quick_toggle("tun", enabled),
            on_success=lambda _r: self.toast("TUN 模式已" + ("开启" if enabled else "关闭"), success=True),
            on_error=lambda exc: self._action_error(exc, self.tun_switch, not enabled),
            name="tun",
        )

    def _on_lan(self, enabled: bool) -> None:
        run_async(
            lambda: self.controller.set_quick_toggle("allow_lan", enabled),
            on_success=lambda _r: self.toast("局域网访问已" + ("开启" if enabled else "关闭"), success=True),
            on_error=lambda exc: self._action_error(exc, self.lan_switch, not enabled),
            name="lan",
        )
