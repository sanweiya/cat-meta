#!/usr/bin/env bash
# Cat Meta 安装脚本：默认安装到当前用户，--system 安装到 /usr。
set -euo pipefail

MODE="user"
if [[ "${1:-}" == "--system" ]]; then
  MODE="system"
elif [[ "${1:-}" == "--user" || -z "${1:-}" ]]; then
  MODE="user"
else
  echo "用法：$0 [--user|--system]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$MODE" == "system" ]]; then
  LIBDIR="/usr/lib/cat-meta"
  BINDIR="/usr/bin"
  APPDIR="/usr/share/applications"
  ICONROOT="/usr/share/icons/hicolor"
  SUDO=""
  if [[ $EUID -ne 0 ]]; then SUDO="sudo"; fi
else
  LIBDIR="${HOME}/.local/lib/cat-meta"
  BINDIR="${HOME}/.local/bin"
  APPDIR="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
  ICONROOT="${XDG_DATA_HOME:-${HOME}/.local/share}/icons/hicolor"
  SUDO=""
fi

if ! python3 -c "import gi; gi.require_version('Gtk','3.0')" 2>/dev/null; then
  echo "错误：缺少 PyGObject / GTK 3。请先安装 python3-gi、gir1.2-gtk-3.0。" >&2
  exit 1
fi

echo "安装 Cat Meta 到 ${LIBDIR} ..."
$SUDO mkdir -p "$LIBDIR"
# 只清理受控安装目录，避免误删。
case "$LIBDIR" in
  "$HOME"/.local/lib/cat-meta|/usr/lib/cat-meta) $SUDO rm -rf -- "$LIBDIR" ;;
  *) echo "意外的安装目录：$LIBDIR" >&2; exit 1 ;;
esac
$SUDO mkdir -p "$LIBDIR"
$SUDO cp -a "$ROOT/catmeta" "$ROOT/run.py" "$LIBDIR/"
if [[ -f "$ROOT/LICENSE" ]]; then $SUDO cp -a "$ROOT/LICENSE" "$LIBDIR/"; fi
$SUDO find "$LIBDIR" -type d -name __pycache__ -prune -exec rm -rf {} +
$SUDO find "$LIBDIR" -type f -name '*.pyc' -delete
$SUDO mkdir -p "$BINDIR" "$APPDIR" "$ICONROOT/scalable/apps"
$SUDO install -m 0755 "$ROOT/scripts/cat-meta-launcher" "$BINDIR/cat-meta"
# 让启动器指向实际安装目录。
$SUDO sed -i "s|@LIBDIR@|$LIBDIR|g" "$BINDIR/cat-meta"
$SUDO install -m 0644 "$ROOT/packaging/cat-meta.desktop" "$APPDIR/cat-meta.desktop"
$SUDO install -m 0644 "$ROOT/catmeta/assets/icons/hicolor/scalable/apps/cat-meta.svg" "$ICONROOT/scalable/apps/cat-meta.svg"
$SUDO install -m 0644 "$ROOT/catmeta/assets/icons/hicolor/scalable/apps/cat-meta-symbolic.svg" "$ICONROOT/scalable/apps/cat-meta-symbolic.svg"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  $SUDO gtk-update-icon-cache -f -t "$ICONROOT" >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  $SUDO update-desktop-database "$APPDIR" >/dev/null 2>&1 || true
fi

if [[ "$MODE" == "user" ]]; then
  cat <<MSG

安装完成。

  启动：cat-meta
  如果命令找不到，请确认 ~/.local/bin 在 PATH 中，或直接运行：
    ${BINDIR}/cat-meta

首次使用：
  1. 打开“订阅”，导入机场链接或本地 YAML。
  2. 在“设置 → 内核”下载 mihomo（网络可用时会自动完成）。
  3. 回到概览启动代理；TUN 需要“设置 → 内核 → 授权 TUN”。
MSG
else
  cat <<MSG

系统安装完成：/usr/bin/cat-meta
首次使用请先在“设置 → 内核”下载 mihomo，TUN 需要执行：
  sudo $(dirname "$0")/setup-tun.sh
（脚本会通过 SUDO_USER 找到桌面用户的数据目录，也可以显式传入内核路径。）
MSG
fi
