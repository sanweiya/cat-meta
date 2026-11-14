"""TUN 模式下 systemd-resolved 的可选 polkit 规则。

mihomo 的 TUN 会调用 `resolvectl domain/default-route/dns` 把系统 DNS 指向
TUN 网卡，这几个动作默认需要管理员授权（polkit `auth_admin_keep`），因此
首次启用 TUN 时会连续弹出几个密码框。这里提供一个可选的、范围受限的规则：
只放行这 4 个 resolve1 动作、只对当前本地用户生效，其它 polkit 动作不受影响。
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

RULE_PATH = Path("/etc/polkit-1/rules.d/49-cat-meta-tun.rules")
MARKER = "Cat Meta TUN"
ACTIONS = (
    "org.freedesktop.resolve1.set-domains",
    "org.freedesktop.resolve1.set-default-route",
    "org.freedesktop.resolve1.set-dns-servers",
    "org.freedesktop.resolve1.revert",
)


def rule_text(user: str) -> str:
    actions = ",\n        ".join(f'"{action}"' for action in ACTIONS)
    return f"""// {MARKER}：允许用户 {user} 在 TUN 模式下配置 systemd-resolved 的 DNS。
// 由 Cat Meta 生成；删除本文件即可恢复系统默认的授权行为。
polkit.addRule(function(action, subject) {{
    var allowed = [
        {actions}
    ];
    if (allowed.indexOf(action.id) >= 0 && subject.user === "{user}" && subject.local) {{
        return polkit.Result.YES;
    }}
}});
"""


def rule_installed() -> bool:
    try:
        return MARKER in RULE_PATH.read_text(encoding="utf-8")
    except OSError:
        return False


def install_rule() -> tuple[bool, str]:
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return False, "系统缺少 pkexec，无法请求授权"
    user = getpass.getuser()
    fd, tmp_name = tempfile.mkstemp(prefix="cat-meta-polkit-", suffix=".rules")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rule_text(user))
        result = subprocess.run(
            [pkexec, "install", "-D", "-m", "0644", str(tmp), str(RULE_PATH)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"安装 polkit 规则失败：{exc}"
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "已取消或授权失败").strip()
        return False, detail
    if not rule_installed():
        return False, "规则已写入但未生效，请检查 polkit 服务"
    return True, "已启用 TUN 免密授权"


def remove_rule() -> tuple[bool, str]:
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return False, "系统缺少 pkexec，无法请求授权"
    try:
        result = subprocess.run(
            [pkexec, "rm", "-f", str(RULE_PATH)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"移除 polkit 规则失败：{exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "已取消或授权失败").strip()
        return False, detail
    return True, "已恢复系统默认授权"
