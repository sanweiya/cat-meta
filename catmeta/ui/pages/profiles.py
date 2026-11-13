"""订阅与配置管理页。"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from ...async_utils import run_async
from ...controller import Controller
from ...profiles import Profile, ProfileError, validate_config_text
from ...system import open_in_text_editor
from ...util import format_bytes, format_timestamp, safe_filename, time_ago
from ..dialogs import ProfileEditDialog, ProfileImportDialog, TextInputDialog, confirm
from ..widgets.common import (
    Badge,
    BasePage,
    EmptyState,
    IconButton,
    icon,
    label,
    set_hand_cursor,
)


class ProfilesPage(BasePage):
    def __init__(self, controller: Controller, toast: Callable[..., None]):
        super().__init__("订阅", "管理机场订阅、本地配置与自动更新")
        self.controller = controller
        self.toast = toast
        self._open_popover = None
        self._external_edits: dict[str, dict] = {}

        refresh = IconButton("view-refresh-symbolic", "刷新配置列表", self.refresh)
        self.header.actions.pack_start(refresh, False, False, 0)
        self.import_button = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(icon("list-add-symbolic", 14), False, False, 0)
        box.pack_start(label("导入配置"), False, False, 0)
        self.import_button.add(box)
        self.import_button.connect("clicked", lambda _b: self._open_import())
        self.header.lead.pack_start(self.import_button, False, False, 0)

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.list_box.get_style_context().add_class("native-group")
        self.body.pack_start(self.list_box, False, False, 0)

        empty_action = Gtk.Button(label="导入第一个配置")
        empty_action.get_style_context().add_class("primary")
        empty_action.connect("clicked", lambda _b: self._open_import())
        self.empty = EmptyState("folder-documents-symbolic", "还没有配置", "导入机场订阅或本地 YAML 文件开始使用", action=empty_action)
        self.empty.set_no_show_all(True)
        self.body.pack_start(self.empty, True, True, 0)

        self.refresh()

    def on_profiles(self) -> None:
        if self.get_mapped():
            self.refresh()

    def on_show(self) -> None:
        super().on_show()
        self.refresh()

    def refresh(self) -> None:
        profiles = self.controller.profiles.all()
        current = self.controller.profiles.current_id
        for child in self.list_box.get_children():
            child.destroy()
        self.empty.set_visible(not profiles)
        if not profiles:
            self.empty.show_all()
        if profiles:
            current_profile = self.controller.profiles.current()
            for profile in profiles:
                self.list_box.pack_start(self._profile_card(profile, profile.id == current), False, False, 0)
            self.list_box.show_all()
            self.header.set_subtitle(f"{len(profiles)} 个配置 · 当前 {current_profile.name if current_profile else '未选择'}")
        else:
            self.list_box.set_visible(False)
            self.header.set_subtitle("管理机场订阅、本地配置与自动更新")
        self.list_box.set_visible(bool(profiles))

    def _profile_card(self, profile: Profile, active: bool) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=13)
        card.get_style_context().add_class("profile-card")
        if active:
            card.get_style_context().add_class("active")

        icon_holder = Gtk.Box()
        icon_holder.set_valign(Gtk.Align.START)
        icon_holder.set_margin_top(2)
        source_icon = "network-server-symbolic" if profile.kind == "remote" else "text-x-generic-symbolic"
        image = icon(source_icon, 22)
        image.get_style_context().add_class("status-icon")
        icon_holder.pack_start(image, False, False, 0)
        card.pack_start(icon_holder, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_row.pack_start(label(profile.name, "profile-title", ellipsize=True, max_width=36), False, False, 0)
        title_row.pack_start(label(f"· {profile.kind_label}", "profile-kind"), False, False, 0)
        if active:
            title_row.pack_start(label("· 使用中", "state-active"), False, False, 0)
        if profile.kind == "remote":
            title_row.pack_start(label("· 自动更新" if profile.auto_update else "· 手动更新", "profile-kind"), False, False, 0)
        main.pack_start(title_row, False, False, 0)

        if profile.kind == "remote":
            host = urlparse(profile.url).hostname or profile.url
            url_label = label(host, "profile-url", ellipsize=True, max_width=60)
            url_label.set_tooltip_text(profile.url)
        else:
            url_label = label("本地 YAML 配置 · 由 Cat Meta 管理", "profile-url", ellipsize=True)
        main.pack_start(url_label, False, False, 0)

        stats = self.controller.profiles.stats(profile.id)
        try:
            size = profile.path.stat().st_size
        except OSError:
            size = 0
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        for text in (
            f"更新于 {time_ago(profile.updated_at)}",
            f"{stats['proxies']} 个节点",
            f"{stats['rules']} 条规则",
            format_bytes(size),
        ):
            meta.pack_start(label(text, "profile-meta"), False, False, 0)
        main.pack_start(meta, False, False, 0)

        sub = profile.subscription or {}
        total = int(sub.get("total") or 0)
        if total > 0:
            used = int(sub.get("upload") or 0) + int(sub.get("download") or 0)
            # 交给 GTK 原生进度条绘制，避免自定义 trough/progress 样式
            # 造成蓝色填充与边框高度、内边距不一致。
            progress = Gtk.ProgressBar()
            progress.set_fraction(min(1.0, used / total))
            progress.set_size_request(-1, 6)
            bar_holder = Gtk.Box()
            bar_holder.set_margin_top(3)
            bar_holder.set_margin_end(40)
            bar_holder.pack_start(progress, True, True, 0)
            main.pack_start(bar_holder, False, False, 0)
            expire = int(sub.get("expire") or 0)
            expire_text = ""
            if expire > 0:
                expired = expire < time.time()
                expire_text = f" · {'已过期' if expired else format_timestamp(expire, '%Y-%m-%d') + ' 到期'}"
            usage_text = label(f"已用 {format_bytes(used)} / {format_bytes(total)}{expire_text}", "tiny-label")
            if expire > 0 and expire < time.time():
                usage_text.get_style_context().add_class("error-label")
            main.pack_start(usage_text, False, False, 0)

        card.pack_start(main, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        actions.set_valign(Gtk.Align.CENTER)
        if active:
            use_button = Gtk.Button(label="已启用")
            use_button.set_sensitive(False)
            use_button.get_style_context().add_class("pill")
            actions.pack_start(use_button, False, False, 0)
        else:
            use_button = Gtk.Button(label="启用")
            use_button.get_style_context().add_class("pill")
            use_button.connect("clicked", lambda _b, p=profile: self._use(p))
            actions.pack_start(use_button, False, False, 0)
        if profile.kind == "remote":
            update = IconButton("view-refresh-symbolic", "更新此订阅", lambda p=profile: self._update(p), size=14)
            actions.pack_start(update, False, False, 0)
        menu = self._menu_button(profile, active)
        actions.pack_start(menu, False, False, 0)
        card.pack_end(actions, False, False, 0)
        return card

    def _menu_button(self, profile: Profile, active: bool) -> Gtk.MenuButton:
        button = Gtk.MenuButton()
        button.set_image(icon("view-more-symbolic", 15))
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_tooltip_text("更多操作")
        button.get_style_context().add_class("flat")
        button.get_style_context().add_class("icon-button")
        popover = Gtk.Popover.new(button)
        popover.connect("show", self._on_popover_show)
        popover.connect("hide", self._on_popover_hide)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        def add_item(text: str, icon_name: str, callback: Callable[[], None], destructive: bool = False) -> None:
            item = Gtk.Button()
            item.set_relief(Gtk.ReliefStyle.NONE)
            item.get_style_context().add_class("flat")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(icon(icon_name, 14), False, False, 0)
            text_label = label(text)
            if destructive:
                text_label.get_style_context().add_class("destructive")
            row.pack_start(text_label, False, False, 0)
            item.add(row)
            item.connect("clicked", lambda _b: (popover.popdown(), callback()))
            box.pack_start(item, False, False, 0)

        add_item("编辑 YAML", "text-editor-symbolic", lambda: self._edit(profile))
        add_item("重命名", "document-edit-symbolic", lambda: self._rename(profile))
        if profile.kind == "remote":
            add_item("立即更新", "view-refresh-symbolic", lambda: self._update(profile))
            add_item("复制订阅链接", "edit-copy-symbolic", lambda: self._copy(profile.url))
        add_item("导出配置", "document-save-symbolic", lambda: self._export(profile))
        add_item("删除配置", "user-trash-symbolic", lambda: self._delete(profile, active), destructive=True)
        popover.add(box)
        button.set_popover(popover)
        box.show_all()
        return button

    def _on_popover_show(self, popover: Gtk.Popover) -> None:
        if self._open_popover is not None and self._open_popover is not popover:
            self._open_popover.popdown()
        self._open_popover = popover

    def _on_popover_hide(self, popover: Gtk.Popover) -> None:
        if self._open_popover is popover:
            self._open_popover = None

    # ---------------- 操作 ----------------
    def _open_import(self) -> None:
        dialog = ProfileImportDialog(
            self.get_toplevel(),
            on_url=self.controller.add_profile_remote,
            on_file=self.controller.add_profile_local,
            on_manual=self.controller.add_profile_manual,
            on_done=self._imported,
        )
        dialog.show_all()

    def _imported(self, profile: Profile) -> None:
        self.toast(f"已导入「{profile.name}」，点击“启用”应用", success=True)
        self.refresh()
        self.controller.emit("profiles")

    def _use(self, profile: Profile) -> None:
        self.toast(f"正在应用「{profile.name}」…")

        def action() -> None:
            self.controller.use_profile(profile.id, restart=True)

        run_async(
            action,
            on_success=lambda _r: self.toast(f"已启用「{profile.name}」", success=True),
            on_error=lambda exc: self.toast(str(exc), error=True, timeout=8),
            name="use-profile",
        )

    def _update(self, profile: Profile) -> None:
        self.toast(f"正在更新「{profile.name}」…")

        def action():
            return self.controller.update_profile(profile.id)

        run_async(
            action,
            on_success=lambda _p: self.toast("订阅已更新", success=True),
            on_error=lambda exc: self.toast(str(exc), error=True, timeout=8),
            name="update-profile",
        )

    def _edit(self, profile: Profile) -> None:
        try:
            content = self.controller.profiles.content(profile.id)
        except Exception as exc:
            self.toast(str(exc), error=True)
            return
        if bool(self.controller.settings.use_builtin_editor):
            self._open_builtin_editor(profile, content)
            return
        if self._open_in_system_editor(profile, content):
            return
        self.toast("系统编辑器不可用，已打开内置编辑器", timeout=5)
        self._open_builtin_editor(profile, content)

    def _open_builtin_editor(self, profile: Profile, content: str) -> None:
        dialog = ProfileEditDialog(self.get_toplevel(), profile, content, lambda text: self._save_edit(profile, text))
        dialog.show_all()

    def _open_in_system_editor(self, profile: Profile, content: str) -> bool:
        try:
            stat = profile.path.stat()
        except OSError:
            return False
        if not open_in_text_editor(profile.path):
            return False
        watched = self._start_external_watch(profile, content, stat.st_mtime_ns, stat.st_size)
        if watched:
            self.toast("已用系统编辑器打开，保存后自动校验并重载", timeout=6)
        else:
            self.toast("已用系统编辑器打开，但无法监听保存，请编辑后重新启用配置", error=True, timeout=8)
        return True

    # ---------------- 外部编辑器监听 ----------------
    def _start_external_watch(self, profile: Profile, content: str, mtime: int, size: int) -> bool:
        self._stop_external_watch(profile.id)
        path = profile.path
        try:
            monitor = Gio.File.new_for_path(str(path.parent)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except Exception:
            monitor = None
        if monitor is None:
            return False
        state = {
            "profile": profile,
            "filename": path.name,
            "initial": content,
            "mtime": mtime,
            "size": size,
            "bad": None,
            "monitor": monitor,
            "debounce": 0,
            "timeout": 0,
        }
        self._external_edits[profile.id] = state
        monitor.connect("changed", self._on_external_file_changed, profile.id)
        state["timeout"] = GLib.timeout_add_seconds(15 * 60, self._on_external_timeout, profile.id)
        return True

    def _stop_external_watch(self, profile_id: str) -> None:
        state = self._external_edits.pop(profile_id, None)
        if not state:
            return
        monitor = state.get("monitor")
        if monitor is not None:
            try:
                monitor.cancel()
            except Exception:
                pass
        for key in ("timeout", "debounce"):
            source = state.get(key)
            if source:
                GLib.source_remove(source)

    def _on_external_timeout(self, profile_id: str) -> bool:
        state = self._external_edits.get(profile_id)
        if state is not None:
            state["timeout"] = 0
            self._stop_external_watch(profile_id)
        return False

    def _on_external_file_changed(self, _monitor, file, _other, _event, profile_id: str) -> None:
        state = self._external_edits.get(profile_id)
        if state is None:
            return
        try:
            names = {file.get_basename()} if file is not None else set()
            if _other is not None:
                names.add(_other.get_basename())
        except Exception:
            return
        if state["filename"] not in names:
            return
        if state["debounce"]:
            GLib.source_remove(state["debounce"])
        state["debounce"] = GLib.timeout_add(250, self._check_external_edit, profile_id)

    def _check_external_edit(self, profile_id: str) -> bool:
        state = self._external_edits.get(profile_id)
        if state is None:
            return False
        state["debounce"] = 0
        path = state["profile"].path
        try:
            stat = path.stat()
        except OSError:
            return False
        current = (stat.st_mtime_ns, stat.st_size)
        if current == (state["mtime"], state["size"]):
            return False
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        if text == state["initial"]:
            state["mtime"], state["size"] = current
            return False
        try:
            validate_config_text(text)
        except ProfileError as exc:
            state["mtime"], state["size"] = current
            if state["bad"] != text:
                state["bad"] = text
                self.toast(f"配置有误，未重载：{exc}", error=True, timeout=8)
            return False
        self._stop_external_watch(profile_id)
        run_async(
            lambda: self.controller.save_profile_content(profile_id, text),
            on_success=lambda _r: self.toast("配置已保存并重新加载", success=True),
            on_error=lambda exc: self.toast(str(exc), error=True, timeout=8),
            name="save-profile",
        )
        return False

    def _save_edit(self, profile: Profile, text: str) -> None:
        def action() -> None:
            self.controller.save_profile_content(profile.id, text)

        run_async(
            action,
            on_success=lambda _r: self.toast("配置已保存", success=True),
            on_error=lambda exc: self.toast(str(exc), error=True, timeout=8),
            name="save-profile",
        )

    def _rename(self, profile: Profile) -> None:
        def apply(name: str) -> None:
            try:
                self.controller.rename_profile(profile.id, name)
                self.toast("已重命名", success=True)
            except Exception as exc:
                self.toast(str(exc), error=True)

        dialog = TextInputDialog(self.get_toplevel(), "重命名配置", "请输入新的配置名称", profile.name, apply)
        dialog.show_all()

    def _copy(self, text: str) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()
        self.toast("已复制到剪贴板")

    def _export(self, profile: Profile) -> None:
        dialog = Gtk.FileChooserDialog(
            title="导出配置",
            transient_for=self.get_toplevel(),
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "保存", Gtk.ResponseType.OK)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(f"{safe_filename(profile.name)}.yaml")
        file_filter = Gtk.FileFilter()
        file_filter.set_name("YAML 配置 (*.yaml)")
        file_filter.add_pattern("*.yaml")
        dialog.add_filter(file_filter)

        def on_response(dlg: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.OK:
                path = dlg.get_filename()
                try:
                    if path:
                        self.controller.profiles.export_to(profile.id, path)
                        self.toast("配置已导出", success=True)
                except Exception as exc:
                    self.toast(str(exc), error=True)
            dlg.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()

    def _delete(self, profile: Profile, active: bool) -> None:
        secondary = "此操作会删除本地配置文件，无法撤销。"
        if active:
            secondary += "当前正在使用此配置，删除后将切换到其他配置。"
        confirm(
            self.get_toplevel(),
            f"删除「{profile.name}」？",
            secondary,
            lambda: self._do_delete(profile),
            destructive=True,
            confirm_label="删除",
        )

    def _do_delete(self, profile: Profile) -> None:
        def action() -> None:
            self.controller.delete_profile(profile.id)

        run_async(
            action,
            on_success=lambda _r: self.toast("配置已删除", success=True),
            on_error=lambda exc: self.toast(str(exc), error=True),
            name="delete-profile",
        )
