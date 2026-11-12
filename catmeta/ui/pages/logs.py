"""实时日志页。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from ...controller import Controller
from ...util import safe_filename
from ..fonts import monospace_font
from ..widgets.common import BasePage, IconButton, label


LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "warn": 2, "error": 3}
LEVEL_LABELS = {"debug": "调试", "info": "信息", "warning": "警告", "warn": "警告", "error": "错误"}


class LogsPage(BasePage):
    def __init__(self, controller: Controller, toast: Callable[..., None]):
        super().__init__("日志", "mihomo 内核的实时运行日志", scroll=False)
        self.controller = controller
        self.toast = toast
        self._paused = False
        self._line_count = 0
        self._scroll_source = 0
        self._search_source = 0
        self._level = "all"

        self.level_combo = Gtk.ComboBoxText()
        for level, text in (("all", "全部级别"), ("debug", "调试"), ("info", "信息"), ("warning", "警告"), ("error", "错误")):
            self.level_combo.append(level, text)
        self.level_combo.set_active_id("all")
        self.level_combo.set_tooltip_text("按日志级别过滤")
        self.level_combo.connect("changed", lambda _c: self._on_level_changed())
        self.header.lead.pack_start(self.level_combo, False, False, 0)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("搜索日志")
        self.search.set_width_chars(17)
        self.search.get_style_context().add_class("search-entry")
        self.search.connect("search-changed", self._on_search)
        self.header.lead.pack_start(self.search, False, False, 0)

        self.pause_button = Gtk.ToggleButton()
        self.pause_button.set_image(Gtk.Image.new_from_icon_name("media-playback-pause-symbolic", Gtk.IconSize.BUTTON))
        self.pause_button.set_tooltip_text("暂停日志滚动")
        self.pause_button.set_relief(Gtk.ReliefStyle.NONE)
        self.pause_button.get_style_context().add_class("flat")
        self.pause_button.get_style_context().add_class("icon-button")
        self.pause_button.connect("toggled", self._on_pause)
        self.header.actions.pack_start(self.pause_button, False, False, 0)

        self.header.actions.pack_start(IconButton("user-trash-symbolic", "清空日志", self._clear), False, False, 0)
        self.header.actions.pack_start(IconButton("document-save-symbolic", "导出日志", self._export), False, False, 0)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_label = label("等待内核日志…", "tiny-label")
        self.count_label = label("0 行", "badge")
        status.pack_start(self.status_label, False, False, 0)
        status.pack_end(self.count_label, False, False, 0)
        self.body.pack_start(status, False, False, 0)

        self.buffer = Gtk.TextBuffer()
        self.buffer.create_tag("time", foreground="#989898")
        self.buffer.create_tag("debug", foreground="#8a8a8a")
        self.buffer.create_tag("info", foreground="#3584e4")
        self.buffer.create_tag("warning", foreground="#c47d00")
        self.buffer.create_tag("error", foreground="#e01b24")
        self.buffer.create_tag("message")

        self.view = Gtk.TextView(buffer=self.buffer)
        self.view.set_editable(False)
        self.view.set_cursor_visible(False)
        # 只沿用系统的等宽字族，字号固定为 10px，不跟随系统字体大小。
        log_font = monospace_font(ui_scale=False)
        log_font.set_absolute_size(10 * Pango.SCALE)
        self.view.override_font(log_font)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(0)
        self.view.set_right_margin(0)
        self.view.set_top_margin(0)
        self.view.set_bottom_margin(0)
        self.view.get_style_context().add_class("log-view")
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_margin_start(8)
        self.scroller.set_margin_end(8)
        self.scroller.set_margin_top(6)
        self.scroller.set_margin_bottom(6)
        self.scroller.set_vexpand(True)
        self.scroller.add(self.view)

        self.placeholder = label("等待内核输出日志…", "empty-subtitle", xalign=0.5)
        self.placeholder.set_no_show_all(True)
        overlay = Gtk.Overlay()
        overlay.add(self.scroller)
        holder = Gtk.Box()
        holder.set_valign(Gtk.Align.CENTER)
        holder.set_halign(Gtk.Align.CENTER)
        holder.pack_start(self.placeholder, False, False, 0)
        overlay.add_overlay(holder)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("list-card")
        card.pack_start(overlay, True, True, 0)
        self.body.pack_start(card, True, True, 0)

        self._rebuild()
        self.controller.subscribe("logs-cleared", self._on_cleared)

    # ---------------- 事件 ----------------
    def on_log(self, entry: dict[str, Any]) -> None:
        # 不在日志页时只保留 Controller 中的环形缓冲，切回页面时重建，
        # 避免后台高频日志持续触发 TextBuffer 布局和滚动。
        if self._paused or not self.get_mapped():
            return
        if not self._match(entry):
            return
        self._append(entry)

    def on_show(self) -> None:
        super().on_show()
        self._rebuild()
        self._scroll_to_end()

    def _on_cleared(self) -> None:
        self._rebuild()

    # ---------------- 渲染 ----------------
    def _match(self, entry: dict[str, Any]) -> bool:
        level = str(entry.get("type") or "info").lower()
        if self._level != "all":
            threshold = LEVEL_ORDER.get(self._level, 1)
            if LEVEL_ORDER.get(level, 1) < threshold:
                return False
        query = self.search.get_text().strip().lower()
        if query and query not in str(entry.get("payload") or "").lower():
            return False
        return True

    def _append(self, entry: dict[str, Any], *, scroll: bool = True) -> None:
        was_bottom = self._is_bottom()
        level = str(entry.get("type") or "info").lower()
        tag_name = level if level in ("debug", "info", "warning", "warn", "error") else "info"
        if tag_name == "warn":
            tag_name = "warning"
        stamp = time.strftime("%H:%M:%S", time.localtime(float(entry.get("time") or time.time())))
        payload = str(entry.get("payload") or "").replace("\n", " ")
        end = self.buffer.get_end_iter()
        self.buffer.insert_with_tags_by_name(end, stamp + "  ", "time")
        self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), f"[{LEVEL_LABELS.get(level, level)}] ", tag_name)
        self.buffer.insert_with_tags_by_name(self.buffer.get_end_iter(), payload + "\n", "message")
        self._line_count += 1
        if self._line_count > 2500:
            start = self.buffer.get_start_iter()
            cut = self.buffer.get_iter_at_line(min(500, self._line_count - 1))
            self.buffer.delete(start, cut)
            self._line_count = self.buffer.get_line_count()
        self._update_count()
        self._update_placeholder()
        if scroll and (was_bottom or self._is_bottom()):
            self._scroll_to_end()

    def _rebuild(self) -> None:
        self.buffer.set_text("")
        self._line_count = 0
        logs = list(self.controller.logs)
        for entry in logs:
            if self._match(entry):
                self._append(entry, scroll=False)
        self._update_count()
        self._update_placeholder()
        self._scroll_to_end()

    def _update_count(self) -> None:
        self.count_label.set_text(f"{self._line_count} 行")
        if self._line_count:
            self.status_label.set_text("实时输出中" if not self._paused else "已暂停")
        else:
            self.status_label.set_text("等待内核日志…")

    def _update_placeholder(self) -> None:
        self.placeholder.set_visible(self._line_count == 0)
        if self._line_count == 0:
            self.placeholder.show()

    def _is_bottom(self) -> bool:
        adj = self.scroller.get_vadjustment()
        return adj.get_value() >= max(0, adj.get_upper() - adj.get_page_size() - 36)

    def _scroll_to_end(self) -> None:
        if self._scroll_source:
            return
        self._scroll_source = GLib.idle_add(self._do_scroll)

    def _do_scroll(self) -> bool:
        self._scroll_source = 0
        adj = self.scroller.get_vadjustment()
        adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
        return False

    # ---------------- 操作 ----------------
    def _on_pause(self, button: Gtk.ToggleButton) -> None:
        self._paused = button.get_active()
        button.set_tooltip_text("继续日志" if self._paused else "暂停日志滚动")
        button.set_image(Gtk.Image.new_from_icon_name("media-playback-start-symbolic" if self._paused else "media-playback-pause-symbolic", Gtk.IconSize.BUTTON))
        if not self._paused:
            self._rebuild()
        self._update_count()

    def _on_level_changed(self) -> None:
        self._level = self.level_combo.get_active_id() or "all"
        self._rebuild()

    def _on_search(self, _entry: Gtk.SearchEntry) -> None:
        if self._search_source:
            GLib.source_remove(self._search_source)
        self._search_source = GLib.timeout_add(160, self._apply_search)

    def _apply_search(self) -> bool:
        self._search_source = 0
        self._rebuild()
        return False

    def _clear(self) -> None:
        self.controller.clear_logs()
        self._rebuild()
        self.toast("日志已清空")

    def _export(self) -> None:
        dialog = Gtk.FileChooserDialog(
            title="导出日志",
            transient_for=self.get_toplevel(),
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "保存", Gtk.ResponseType.OK)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(f"cat-meta-logs-{time.strftime('%Y%m%d-%H%M')}.txt")
        file_filter = Gtk.FileFilter()
        file_filter.set_name("文本文件 (*.txt)")
        file_filter.add_pattern("*.txt")
        dialog.add_filter(file_filter)

        def on_response(dlg: Gtk.Dialog, response: int) -> None:
            if response == Gtk.ResponseType.OK and dlg.get_filename():
                lines = []
                for entry in list(self.controller.logs):
                    if not self._match(entry):
                        continue
                    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(entry.get("time") or time.time())))
                    lines.append(f"{stamp} [{str(entry.get('type') or 'info').upper()}] {entry.get('payload') or ''}")
                try:
                    with open(dlg.get_filename(), "w", encoding="utf-8") as handle:
                        handle.write("\n".join(lines) + "\n")
                    self.toast("日志已导出", success=True)
                except OSError as exc:
                    self.toast(f"导出失败：{exc}", error=True)
            dlg.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()
