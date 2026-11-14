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

from .paths import APP_DATA, AUTOSTART_FILE, CONFIG_HOME, PROJECT_ROOT, SYSTEM_PROXY_BACKUP

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


ENV_PROXY_FILE = CONFIG_HOME / "environment.d" / "90-cat-meta-proxy.conf"


def _env_proxy_values(port: int) -> dict[str, str]:
    url = f"http://127.0.0.1:{port}"
    values = {
        "http_proxy": url,
        "https_proxy": url,
        "all_proxy": f"socks5://127.0.0.1:{port}",
        "no_proxy": "localhost,127.0.0.1,::1",
    }
    values.update({key.upper(): value for key, value in list(values.items())})
    return values


def _publish_session_env(values: dict[str, str], *, unset: bool = False) -> None:
    """把变量推给 D-Bus 激活环境与 systemd 用户环境（新启动的进程可见）。"""
    pairs = [f"{key}={value}" for key, value in values.items()]
    commands = [["dbus-update-activation-environment", *pairs]]
    if unset:
        commands.append(["systemctl", "--user", "unset-environment", *values])
    else:
        commands.append(["systemctl", "--user", "set-environment", *pairs])
    for command in commands:
        try:
            subprocess.run(command, capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            log.debug("更新会话环境失败：%s", command[0], exc_info=True)


class SystemProxy:
    """系统代理开关。

    - GNOME 系桌面（Cinnamon / MATE / GNOME…）写 ``org.gnome.system.proxy``，立即生效；
    - 其他桌面（XFCE / LXQt / KDE…）没有统一的桌面代理设置，改为写入会话环境变量
      ``http_proxy`` / ``https_proxy`` 等，新启动的应用生效。
    """

    GNOME_FAMILY = ("gnome", "cinnamon", "mate", "unity", "pantheon", "budgie")

    def __init__(self, port: int = 7900):
        self.port = int(port)
        self.env_file = ENV_PROXY_FILE

    @property
    def backend(self) -> str:
        return "gsettings" if self._gsettings_available() else "env"

    @property
    def available(self) -> bool:
        return True

    def _gsettings_available(self) -> bool:
        if not shutil.which("gsettings"):
            return False
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if desktop and not any(name in desktop for name in self.GNOME_FAMILY):
            return False
        ok, schemas = _run_gsettings(["list-schemas"])
        return ok and "org.gnome.system.proxy" in schemas.splitlines()

    def mode(self) -> str:
        if self.backend == "env":
            return "manual" if self._env_proxy_active() else "none"
        return parse_gvariant_string(_gsettings_get("org.gnome.system.proxy", "mode"))

    def _env_proxy_active(self) -> bool:
        try:
            text = self.env_file.read_text(encoding="utf-8")
        except OSError:
            return False
        return f"127.0.0.1:{self.port}" in text

    def is_enabled(self) -> bool:
        if self.backend == "env":
            return self._env_proxy_active()
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
        if self.backend == "env":
            return self._apply_env()
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
        if self.backend == "env":
            return self._disable_env()
        ok = _gsettings_set("org.gnome.system.proxy", "mode", "none")
        return (True, "系统代理已关闭") if ok else (False, "关闭系统代理失败")

    def _apply_env(self) -> tuple[bool, str]:
        values = _env_proxy_values(self.port)
        try:
            self.env_file.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# 由 Cat Meta 写入；关闭系统代理时会删除此文件。"]
            lines += [f"{key}={value}" for key, value in values.items()]
            self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            return False, f"写入代理环境变量失败：{exc}"
        _publish_session_env(values)
        return True, "系统代理已写入会话环境（重新登录后对所有应用生效）"

    def _disable_env(self) -> tuple[bool, str]:
        try:
            self.env_file.unlink(missing_ok=True)
        except OSError as exc:
            return False, f"移除代理环境变量失败：{exc}"
        _publish_session_env({key: "" for key in _env_proxy_values(self.port)}, unset=True)
        return True, "系统代理已关闭"

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
