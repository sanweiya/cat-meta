#!/usr/bin/env bash
# 为 mihomo 授予 TUN 所需能力（无需长期以 root 运行整个应用）。
set -euo pipefail

TARGET_HOME="${HOME}"
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
  TARGET_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
fi

CORE="${1:-${TARGET_HOME}/.local/share/cat-meta/bin/mihomo}"
if [[ ! -x "$CORE" ]]; then
  # 兼容系统级安装时内核仍在用户数据目录的情况。
  if [[ -x /usr/bin/mihomo ]]; then
    CORE=/usr/bin/mihomo
  else
    echo "找不到可执行的 mihomo 内核：$CORE" >&2
    echo "请先在 Cat Meta 的“设置 → 内核”中下载内核，或把路径作为参数传入。" >&2
    exit 1
  fi
fi

if ! command -v setcap >/dev/null 2>&1; then
  echo "缺少 setcap，请安装 libcap2-bin。" >&2
  exit 1
fi

if [[ $EUID -eq 0 ]]; then
  setcap cap_net_admin,cap_net_raw+ep "$CORE"
else
  if command -v pkexec >/dev/null 2>&1; then
    pkexec setcap cap_net_admin,cap_net_raw+ep "$CORE"
  else
    sudo setcap cap_net_admin,cap_net_raw+ep "$CORE"
  fi
fi

echo "已为 $CORE 授予 TUN 权限："
getcap "$CORE"
echo
echo "注意：替换内核二进制后能力会丢失，需要重新执行本脚本。"
