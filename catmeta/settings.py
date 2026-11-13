"""持久化应用设置。"""

from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any

from .paths import SETTINGS_FILE

DEFAULT_TEST_URL = "https://www.gstatic.com/generate_204"


def _defaults() -> dict[str, Any]:
    return {
        # 内核
        "core_path": "",
        "api_port": 19098,
        "api_secret": secrets.token_urlsafe(24),
        "mixed_port": 7900,
        "mode": "rule",
        "log_level": "info",
        "allow_lan": False,
        "ipv6": False,
        "tun": False,
        "tun_stack": "mixed",
        "test_url": DEFAULT_TEST_URL,
        # 界面与行为
        "auto_start": False,
        "auto_start_core": False,
        "start_minimized": False,
        "close_to_tray": True,
        "stop_core_on_quit": True,
        "show_tray": True,
        "window_width": 1200,
        "window_height": 800,
        "window_inset_width": 0,
        "window_inset_height": 0,
        "window_maximized": False,
        "sidebar_width": 200,
        "last_page": "home",
        "proxy_columns": ["name", "type", "delay", "status", "actions"],
        # 订阅
        "auto_update_profiles": True,
        "use_builtin_editor": True,
        "update_interval_hours": 24,
        "last_profile_update_check": 0,
        # 系统代理
        "system_proxy": False,
        # 首次使用
        "onboarded": False,
    }


class Settings:
    """线程安全的 JSON 设置存储，写入时使用原子替换。"""

    def __init__(self, path: Path = SETTINGS_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = _defaults()
        self.load()

    def load(self) -> None:
        with self._lock:
            data = {}
            try:
                if self.path.exists():
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = raw
            except (OSError, ValueError):
                data = {}
            merged = _defaults()
            merged.update({k: v for k, v in data.items() if k in merged})
            if not merged.get("api_secret"):
                merged["api_secret"] = secrets.token_urlsafe(24)
            self._data = merged
            if not self.path.exists():
                self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
            fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            self._data[key] = value
            if save:
                self.save()

    def update(self, values: dict[str, Any], save: bool = True) -> None:
        with self._lock:
            self._data.update(values)
            if save:
                self.save()

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)
        with self._lock:
            if item in self._data:
                return copy.deepcopy(self._data[item])
        raise AttributeError(item)
