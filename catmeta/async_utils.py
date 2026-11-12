"""把阻塞操作放到工作线程，并安全回到 GTK 主线程。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

log = logging.getLogger("catmeta.async")

SuccessCallback = Callable[[Any], None]
ErrorCallback = Callable[[Exception], None]


def run_async(
    func: Callable[[], Any],
    *,
    on_success: SuccessCallback | None = None,
    on_error: ErrorCallback | None = None,
    name: str = "task",
) -> threading.Thread:
    def worker() -> None:
        try:
            result = func()
        except Exception as exc:
            log.debug("异步任务 %s 失败", name, exc_info=True)
            if on_error:
                GLib.idle_add(_call_error, on_error, exc)
            return
        if on_success:
            GLib.idle_add(_call_success, on_success, result)

    thread = threading.Thread(target=worker, name=f"catmeta-{name}", daemon=True)
    thread.start()
    return thread


def _call_success(callback: SuccessCallback, result: Any) -> bool:
    try:
        callback(result)
    except Exception:
        log.exception("异步成功回调失败")
    return False


def _call_error(callback: ErrorCallback, exc: Exception) -> bool:
    try:
        callback(exc)
    except Exception:
        log.exception("异步错误回调失败")
    return False


def on_main_thread(func: Callable[..., Any], *args: Any) -> None:
    GLib.idle_add(func, *args)
