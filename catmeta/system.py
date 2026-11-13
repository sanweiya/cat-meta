"""Linux Mint / Cinnamon 系统集成。"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .paths import APP_DATA, AUTOSTART_FILE, PROJECT_ROOT, SYSTEM_PROXY_BACKUP

log = logging.getLogger("catmeta.system")


def _run_gsettings(args: list[str], timeout: float = 5) -> tuple[bool, str]:
    try:
        result = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return result.returncode == 0, (result.stdout or result.stderr).strip()


def _gsettings_get(schema: str, key: str) -> str:
    ok, value = _run_gsettings(["get", schema, key])
    return value if ok else ""


def _gsettings_set(schema: str, key: str, value: str) -> bool:
    ok, _ = _run_gsettings(["set", schema, key, value])
    return ok


def parse_gvariant_string(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    return value


class SystemProxy:
    """使用 GSettings 控制系统代理；这是 Cinnamon / GNOME 的原生方式。"""

    def __init__(self, port: int = 7900):
        self.port = int(port)

    @property
    def available(self) -> bool:
        if not shutil.which("gsettings"):
            return False
        ok, schemas = _run_gsettings(["list-schemas"])
        return ok and "org.gnome.system.proxy" in schemas.splitlines()

    def mode(self) -> str:
        if not self.available:
            return "unknown"
        return parse_gvariant_string(_gsettings_get("org.gnome.system.proxy", "mode"))

    def is_enabled(self) -> bool:
        if not self.available:
            return False
        mode = self.mode()
        if mode != "manual":
            return False
        host = parse_gvariant_string(_gsettings_get("org.gnome.system.proxy.http", "host"))
        try:
            port = int(_gsettings_get("org.gnome.system.proxy.http", "port") or 0)
        except ValueError:
            port = 0
        return host in ("127.0.0.1", "localhost") and port == self.port

    def _backup(self) -> None:
        if SYSTEM_PROXY_BACKUP.exists():
            return
        data = {
            "mode": parse_gvariant_string(_gsettings_get("org.gnome.system.proxy", "mode")),
            "autoconfig-url": parse_gvariant_string(_gsettings_get("org.gnome.system.proxy", "autoconfig-url")),
            "ignore-hosts": _gsettings_get("org.gnome.system.proxy", "ignore-hosts"),
            "saved_at": time.time(),
        }
        for kind in ("http", "https", "socks", "ftp"):
            data[kind] = {
                "host": parse_gvariant_string(_gsettings_get(f"org.gnome.system.proxy.{kind}", "host")),
                "port": _gsettings_get(f"org.gnome.system.proxy.{kind}", "port"),
            }
        try:
            APP_DATA.mkdir(parents=True, exist_ok=True)
            SYSTEM_PROXY_BACKUP.parent.mkdir(parents=True, exist_ok=True)
            SYSTEM_PROXY_BACKUP.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.exception("无法备份系统代理设置")

    def apply(self) -> tuple[bool, str]:
        if not self.available:
            return False, "当前桌面环境不支持 GSettings 系统代理"
        self._backup()
        ok = True
        for kind in ("http", "https", "socks"):
            ok = _gsettings_set(f"org.gnome.system.proxy.{kind}", "host", "127.0.0.1") and ok
            ok = _gsettings_set(f"org.gnome.system.proxy.{kind}", "port", str(self.port)) and ok
        ok = _gsettings_set("org.gnome.system.proxy", "mode", "manual") and ok
        if not ok:
            return False, "写入系统代理设置失败"
        return True, f"系统代理已指向 127.0.0.1:{self.port}"

    def disable(self) -> tuple[bool, str]:
        if not self.available:
            return False, "当前桌面环境不支持 GSettings 系统代理"
        ok = _gsettings_set("org.gnome.system.proxy", "mode", "none")
        return (True, "系统代理已关闭") if ok else (False, "关闭系统代理失败")

    def clear_backup(self) -> None:
        try:
            SYSTEM_PROXY_BACKUP.unlink(missing_ok=True)
        except OSError:
            pass


def autostart_exec() -> str:
    installed = Path("/usr/bin/cat-meta")
    if installed.exists():
        return str(installed)
    launcher = PROJECT_ROOT / "run.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(launcher))}"


def is_autostart_enabled() -> bool:
    return AUTOSTART_FILE.exists()


def set_autostart(enabled: bool) -> bool:
    try:
        if not enabled:
            AUTOSTART_FILE.unlink(missing_ok=True)
            return True
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        content = f"""\
[Desktop Entry]
Type=Application
Name=Cat Meta
Name[zh_CN]=Cat Meta 代理客户端
Comment=Native mihomo proxy client for Linux Mint
Exec={autostart_exec()}
Icon=cat-meta
Terminal=false
Categories=Network;
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=4
"""
        AUTOSTART_FILE.write_text(content, encoding="utf-8")
        AUTOSTART_FILE.chmod(0o755)
        return True
    except OSError:
        log.exception("设置自动启动失败")
        return False


def open_uri(uri: str) -> bool:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    try:
        return bool(Gio.AppInfo.launch_default_for_uri(uri, None))
    except Exception:
        log.exception("打开链接失败")
        return False


def open_folder(path: Path) -> bool:
    return open_uri(Path(path).as_uri())


def default_text_editor():
    """返回系统默认文本编辑器，跳过 Cat Meta 自身注册的 YAML 处理器。"""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    for mime in ("text/plain", "text/x-yaml", "application/x-yaml", "application/yaml"):
        try:
            app = Gio.AppInfo.get_default_for_type(mime, False)
        except Exception:
            continue
        if app is None:
            continue
        app_id = (app.get_id() or "").lower()
        if app_id == "cat-meta.desktop" or "cat-meta" in app_id:
            continue
        try:
            if app.supports_uris():
                return app
        except Exception:
            continue
    return None


def open_in_text_editor(path: Path) -> bool:
    """用系统默认编辑器打开文件；没有可用编辑器时返回 False。"""
    app = default_text_editor()
    if app is None:
        return False
    try:
        return bool(app.launch_uris([Path(path).resolve().as_uri()], None))
    except Exception:
        log.exception("打开系统编辑器失败")
        return False


def notify(title: str, body: str = "", icon: str = "cat-meta") -> None:
    try:
        import gi

        gi.require_version("Notify", "0.7")
        from gi.repository import Notify

        if not Notify.is_initted():
            Notify.init("Cat Meta")
        notification = Notify.Notification.new(title, body, icon)
        notification.set_app_name("Cat Meta")
        notification.show()
    except Exception:
        log.debug("通知不可用", exc_info=True)


def is_cinnamon() -> bool:
    return "cinnamon" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def session_type() -> str:
    return os.environ.get("XDG_SESSION_TYPE", "x11")
