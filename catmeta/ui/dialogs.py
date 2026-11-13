"""订阅导入、编辑与确认对话框。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

from ..async_utils import run_async
from ..profiles import SAMPLE_PROFILE, Profile, ProfileError, validate_config_text
from .fonts import monospace_font
from .widgets.common import icon, label


def _dialog_button(dialog: Gtk.Dialog, text: str, response: Gtk.ResponseType, primary: bool = False) -> Gtk.Button:
    button = dialog.add_button(text, response)
    if primary:
        button.get_style_context().add_class("primary")
    return button


class ProfileImportDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        *,
        on_url: Callable[[str, str], Profile],
        on_file: Callable[[str, str], Profile],
        on_manual: Callable[[str, str], Profile],
        on_done: Callable[[Profile], None],
    ):
        super().__init__(title="导入配置", transient_for=parent, flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT)
        self.set_default_size(590, 485)
        self.set_resizable(True)
        self.on_url = on_url
        self.on_file = on_file
        self.on_manual = on_manual
        self.on_done = on_done
        self._busy = False

        _dialog_button(self, "取消", Gtk.ResponseType.CANCEL)
        self.ok_button = _dialog_button(self, "导入订阅", Gtk.ResponseType.OK, primary=True)
        self.set_default_response(Gtk.ResponseType.OK)
        self.connect("response", self._on_response)

        content = self.get_content_area()
        content.set_spacing(14)
        content.set_margin_top(14)
        content.set_margin_bottom(10)
        content.set_margin_start(18)
        content.set_margin_end(18)

        intro = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        intro.pack_start(icon("folder-documents-symbolic", 22), False, False, 0)
        intro_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        intro_text.pack_start(label("添加 Clash / mihomo 配置", "section-title"), False, False, 0)
        intro_text.pack_start(label("支持机场订阅链接、本地 YAML 文件，或直接粘贴配置", "tiny-label"), False, False, 0)
        intro.pack_start(intro_text, False, False, 0)
        content.pack_start(intro, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(140)
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        self.stack.set_interpolate_size(True)
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        content.pack_start(switcher, False, False, 0)

        self.stack.add_titled(self._build_url_page(), "url", "订阅链接")
        self.stack.add_titled(self._build_file_page(), "file", "本地文件")
        self.stack.add_titled(self._build_manual_page(), "manual", "手动创建")
        content.pack_start(self.stack, True, True, 0)

        self.error_label = label("", "error-label", wrap=True)
        self.error_label.set_no_show_all(True)
        self.error_label.set_visible(False)
        content.pack_start(self.error_label, False, False, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        self.status_label = label("", "tiny-label")
        bottom.pack_start(self.spinner, False, False, 0)
        bottom.pack_start(self.status_label, False, False, 0)
        content.pack_start(bottom, False, False, 0)

        self.stack.connect("notify::visible-child-name", self._on_page_changed)
        self.show_all()
        self.error_label.set_visible(False)
        self.spinner.set_visible(False)
        self.resize(600, 360)

    def _page(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(4)
        return box

    def _build_url_page(self) -> Gtk.Widget:
        box = self._page()
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.attach(label("名称（可选）"), 0, 0, 1, 1)
        self.url_name = Gtk.Entry()
        self.url_name.set_placeholder_text("留空则使用订阅标题或域名")
        self.url_name.set_hexpand(True)
        grid.attach(self.url_name, 1, 0, 1, 1)
        grid.attach(label("订阅链接"), 0, 1, 1, 1)
        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("https://example.com/api/v1/client/subscribe?token=…")
        self.url_entry.set_hexpand(True)
        self.url_entry.set_activates_default(True)
        grid.attach(self.url_entry, 1, 1, 1, 1)
        box.pack_start(grid, False, False, 0)
        hint = label("订阅会通过直连下载；若失败将自动尝试本地代理端口。支持 Clash YAML 与 base64 编码的订阅。", "tiny-label", wrap=True)
        box.pack_start(hint, False, False, 0)
        self.url_entry.grab_focus()
        return box

    def _build_file_page(self) -> Gtk.Widget:
        box = self._page()
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.attach(label("配置文件"), 0, 0, 1, 1)
        self.file_chooser = Gtk.FileChooserButton(title="选择 YAML 配置")
        self.file_chooser.set_hexpand(True)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Clash / mihomo 配置 (*.yaml, *.yml)")
        file_filter.add_pattern("*.yaml")
        file_filter.add_pattern("*.yml")
        file_filter.add_pattern("*.txt")
        self.file_chooser.add_filter(file_filter)
        grid.attach(self.file_chooser, 1, 0, 1, 1)
        grid.attach(label("名称（可选）"), 0, 1, 1, 1)
        self.file_name = Gtk.Entry()
        self.file_name.set_placeholder_text("默认使用文件名")
        self.file_name.set_hexpand(True)
        grid.attach(self.file_name, 1, 1, 1, 1)
        box.pack_start(grid, False, False, 0)
        box.pack_start(label("文件会被复制到 Cat Meta 的配置目录，不会修改原文件。", "tiny-label", wrap=True), False, False, 0)
        return box

    def _build_manual_page(self) -> Gtk.Widget:
        box = self._page()
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.attach(label("名称"), 0, 0, 1, 1)
        self.manual_name = Gtk.Entry()
        self.manual_name.set_placeholder_text("例如：我的配置")
        self.manual_name.set_hexpand(True)
        grid.attach(self.manual_name, 1, 0, 1, 1)
        box.pack_start(grid, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_min_content_height(250)
        self.manual_view = Gtk.TextView()
        self.manual_view.set_monospace(True)
        self.manual_view.override_font(monospace_font())
        self.manual_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.manual_view.get_buffer().set_text(SAMPLE_PROFILE)
        scroller.add(self.manual_view)
        box.pack_start(scroller, True, True, 0)
        return box

    def _on_page_changed(self, stack: Gtk.Stack, _param) -> None:
        page = stack.get_visible_child_name()
        self.ok_button.set_label({"url": "导入订阅", "file": "导入文件", "manual": "创建配置"}.get(page or "", "导入"))
        self.resize(600, 560 if page == "manual" else 360)
        self._set_error("")

    def _set_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.ok_button.set_sensitive(not busy)
        self.spinner.set_visible(busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.status_label.set_text(text)

    def _on_response(self, dialog: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.CANCEL:
            self.destroy()
            return
        if response != Gtk.ResponseType.OK or self._busy:
            return
        page = self.stack.get_visible_child_name()
        if page == "url":
            url = self.url_entry.get_text().strip()
            name = self.url_name.get_text().strip()
            if not url:
                self._set_error("请输入订阅链接")
                self.url_entry.grab_focus()
                return
            if not url.startswith(("http://", "https://")):
                self._set_error("订阅链接必须以 http:// 或 https:// 开头")
                return
            func = lambda: self.on_url(url, name)
            busy_text = "正在下载并校验订阅…"
        elif page == "file":
            path = self.file_chooser.get_filename()
            if not path:
                self._set_error("请选择一个 YAML 配置文件")
                return
            name = self.file_name.get_text().strip()
            func = lambda: self.on_file(path, name)
            busy_text = "正在导入配置…"
        else:
            name = self.manual_name.get_text().strip()
            buffer = self.manual_view.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            if not name:
                self._set_error("请输入配置名称")
                return
            try:
                validate_config_text(text)
            except ProfileError as exc:
                self._set_error(str(exc))
                return
            func = lambda: self.on_manual(name, text)
            busy_text = "正在创建配置…"
        self._set_error("")
        self._set_busy(True, busy_text)
        run_async(
            func,
            on_success=self._success,
            on_error=self._failure,
            name="import-profile",
        )

    def _success(self, profile: Profile) -> None:
        self._set_busy(False)
        try:
            self.on_done(profile)
        finally:
            self.destroy()

    def _failure(self, exc: Exception) -> None:
        self._set_busy(False)
        self._set_error(str(exc))


class ProfileEditDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, profile: Profile, content: str, on_save: Callable[[str], None]):
        super().__init__(title=f"编辑 · {profile.name}", transient_for=parent, flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT)
        self.set_default_size(780, 640)
        self.on_save = on_save
        _dialog_button(self, "取消", Gtk.ResponseType.CANCEL)
        save = _dialog_button(self, "保存", Gtk.ResponseType.OK, primary=True)
        self.set_default_response(Gtk.ResponseType.OK)
        self.connect("response", self._on_response)

        content_area = self.get_content_area()
        content_area.set_spacing(10)
        content_area.set_margin_top(12)
        content_area.set_margin_bottom(10)
        content_area.set_margin_start(14)
        content_area.set_margin_end(14)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        head.pack_start(icon("text-x-generic-symbolic", 16), False, False, 0)
        head.pack_start(label(f"{profile.kind_label}配置 · {profile.filename}", "tiny-label"), False, False, 0)
        head.pack_end(label("保存后自动校验并重载配置", "tiny-label"), False, False, 0)
        content_area.pack_start(head, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self.view = Gtk.TextView()
        self.view.set_monospace(True)
        self.view.override_font(monospace_font())
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.view.get_buffer().set_text(content)
        scroller.add(self.view)
        content_area.pack_start(scroller, True, True, 0)
        self.error_label = label("", "error-label", wrap=True)
        self.error_label.set_no_show_all(True)
        content_area.pack_start(self.error_label, False, False, 0)
        self.show_all()
        self.error_label.set_visible(False)

    def _on_response(self, dialog: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.CANCEL:
            self.destroy()
            return
        if response != Gtk.ResponseType.OK:
            return
        buffer = self.view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        try:
            validate_config_text(text)
        except ProfileError as exc:
            self.error_label.set_text(str(exc))
            self.error_label.set_visible(True)
            return
        self.on_save(text)
        self.destroy()


class TextInputDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, title: str, prompt: str, initial: str, on_ok: Callable[[str], None]):
        super().__init__(title=title, transient_for=parent, flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT)
        self.set_default_size(420, -1)
        self.on_ok = on_ok
        _dialog_button(self, "取消", Gtk.ResponseType.CANCEL)
        _dialog_button(self, "确定", Gtk.ResponseType.OK, primary=True)
        self.set_default_response(Gtk.ResponseType.OK)
        self.connect("response", self._on_response)
        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(14)
        content.set_margin_bottom(6)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.pack_start(label(prompt), False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_text(initial)
        self.entry.set_activates_default(True)
        self.entry.select_region(0, -1)
        content.pack_start(self.entry, False, False, 0)
        self.show_all()
        self.entry.grab_focus()

    def _on_response(self, dialog: Gtk.Dialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            value = self.entry.get_text().strip()
            if value:
                self.on_ok(value)
                self.destroy()
            return
        self.destroy()


def confirm(
    parent: Gtk.Window,
    title: str,
    secondary: str,
    on_confirm: Callable[[], None],
    *,
    destructive: bool = False,
    confirm_label: str = "确定",
) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        destroy_with_parent=True,
        message_type=Gtk.MessageType.WARNING if destructive else Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE,
        text=title,
    )
    dialog.format_secondary_text(secondary)
    dialog.add_button("取消", Gtk.ResponseType.CANCEL)
    button = dialog.add_button(confirm_label, Gtk.ResponseType.OK)
    if destructive:
        button.get_style_context().add_class("destructive")
    else:
        button.get_style_context().add_class("primary")

    def on_response(dlg: Gtk.Dialog, response: int) -> None:
        dlg.destroy()
        if response == Gtk.ResponseType.OK:
            on_confirm()

    dialog.connect("response", on_response)
    dialog.show_all()
