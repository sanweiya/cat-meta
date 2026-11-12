"""应用使用的 XDG 路径。"""

from __future__ import annotations

import os
from pathlib import Path

from . import APP_ID


def _xdg(env: str, default: str) -> Path:
    value = os.environ.get(env, "")
    return Path(value).expanduser() if value else Path.home() / default


DATA_HOME = _xdg("XDG_DATA_HOME", ".local/share")
CONFIG_HOME = _xdg("XDG_CONFIG_HOME", ".config")
CACHE_HOME = _xdg("XDG_CACHE_HOME", ".cache")

APP_DATA = DATA_HOME / "cat-meta"
APP_CONFIG = CONFIG_HOME / "cat-meta"
APP_CACHE = CACHE_HOME / "cat-meta"

PROFILES_DIR = APP_DATA / "profiles"
RUNTIME_DIR = APP_DATA / "runtime"
LOGS_DIR = APP_DATA / "logs"
BIN_DIR = APP_DATA / "bin"
GEO_DIR = APP_DATA / "geo"
STATE_DIR = APP_DATA / "state"

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICONS_DIR = ASSETS_DIR / "icons"
CSS_FILE = ASSETS_DIR / "css" / "style.css"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

SETTINGS_FILE = APP_CONFIG / "settings.json"
PROFILES_FILE = APP_CONFIG / "profiles.json"
SYSTEM_PROXY_BACKUP = STATE_DIR / "system-proxy.json"
AUTOSTART_FILE = CONFIG_HOME / "autostart" / "cat-meta.desktop"

CORE_LOG = LOGS_DIR / "core.log"
APP_LOG = LOGS_DIR / "app.log"


def ensure_dirs() -> None:
    for directory in (
        APP_DATA,
        APP_CONFIG,
        APP_CACHE,
        PROFILES_DIR,
        RUNTIME_DIR,
        LOGS_DIR,
        BIN_DIR,
        GEO_DIR,
        STATE_DIR,
        ICONS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def desktop_id() -> str:
    return APP_ID


def install_user_icons() -> None:
    """把应用图标安装到用户 hicolor 主题。

    Cinnamon 面板是独立进程，不会读取当前 Python 进程追加的 GtkIconTheme
    搜索路径。XApp 状态图标只向面板传递图标名称，因此必须把图标写入
    ~/.local/share/icons/hicolor 并刷新缓存，托盘图标才能正常显示。
    """
    import shutil
    import subprocess

    icon_root = DATA_HOME / "icons" / "hicolor"
    target = icon_root / "scalable" / "apps"
    source = ICONS_DIR / "hicolor" / "scalable" / "apps"
    try:
        target.mkdir(parents=True, exist_ok=True)
        changed = False
        for name in ("cat-meta.svg", "cat-meta-symbolic.svg"):
            src = source / name
            dst = target / name
            if not src.exists():
                continue
            if not dst.exists() or src.read_bytes() != dst.read_bytes():
                shutil.copy2(src, dst)
                dst.chmod(0o644)
                changed = True
        if changed and shutil.which("gtk-update-icon-cache"):
            subprocess.run(
                ["gtk-update-icon-cache", "-f", "-t", str(icon_root)],
                capture_output=True,
                timeout=10,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        pass
