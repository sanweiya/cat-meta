"""GeoIP / GeoSite 数据文件的检测与多源下载。

mihomo 在首次遇到 GEOIP / GEOSITE 规则时会自行下载数据，但默认直连
GitHub 在部分网络环境中会超时。这里在启动前主动准备数据，并依次尝试
jsDelivr、公共 GitHub 加速镜像、本地代理和 GitHub 原始地址。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .net import NetworkError, download_file
from .paths import APP_DATA, ensure_dirs

log = logging.getLogger("catmeta.geodata")

Progress = Callable[[int, str], None]

FILES = {
    "geoip": ("geoip.metadb", 1_500_000),
    "geosite": ("geosite.dat", 500_000),
}
LABELS = {"geoip": "GeoIP", "geosite": "GeoSite"}
TARGETS = {
    "geoip.metadb": "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geoip.metadb",
    "geosite.dat": "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geosite.dat",
    "geoip.dat": "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geoip.dat",
}
RELEASE = "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/{name}"
MIRROR = "https://gh-proxy.com/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/{name}"


class GeoDataError(Exception):
    pass


def required_geodata(config: dict[str, Any]) -> set[str]:
    """从规则中推断需要哪些 Geo 数据。"""
    required: set[str] = set()
    rules = config.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            text = str(rule).strip().upper()
            kind = text.split(",", 1)[0]
            if kind in ("GEOIP", "SRC-GEOIP"):
                required.add("geoip")
            elif kind == "GEOSITE":
                required.add("geosite")
    # rule-providers / sub-rules 中使用 geosite 的情况较少，交由内核自行处理。
    return required


def filenames_for(kinds: Iterable[str], config: dict[str, Any] | None = None) -> list[str]:
    legacy = bool((config or {}).get("geodata-mode"))
    kinds = set(kinds)
    names = []
    if "geoip" in kinds:
        names.append("geoip.dat" if legacy else "geoip.metadb")
    if "geosite" in kinds:
        names.append("geosite.dat")
    return names


def is_ready(name: str, data_dir: Path = APP_DATA) -> bool:
    path = Path(data_dir) / name
    minimum = FILES["geoip"][1] if name.startswith("geoip") else 500_000
    try:
        return path.is_file() and path.stat().st_size >= minimum
    except OSError:
        return False


def status(data_dir: Path = APP_DATA) -> dict[str, bool]:
    return {
        "geoip": is_ready("geoip.metadb", data_dir) or is_ready("geoip.dat", data_dir),
        "geosite": is_ready("geosite.dat", data_dir),
    }


def _candidate_urls(name: str) -> list[str]:
    urls = []
    if name in TARGETS:
        urls.append(TARGETS[name])
    urls.append(MIRROR.format(name=name))
    urls.append(RELEASE.format(name=name))
    return urls


def _download_one(
    name: str,
    data_dir: Path,
    *,
    proxy_ports: list[int] | None,
    progress: Progress | None,
    base: int,
    span: int,
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / name
    minimum = FILES["geoip"][1] if name.startswith("geoip") else 500_000
    attempts: list[tuple[str, int | None]] = []
    for url in _candidate_urls(name):
        attempts.append((url, None))
    for port in proxy_ports or []:
        attempts.append((RELEASE.format(name=name), int(port)))
        attempts.append((MIRROR.format(name=name), int(port)))
    errors: list[str] = []
    for url, proxy in attempts:
        label = "直连" if not proxy else f"代理 {proxy}"
        try:
            if progress:
                progress(base, f"正在下载 {LABELS.get('geoip' if name.startswith('geoip') else 'geosite', name)}（{label}）…")

            def on_chunk(downloaded: int, total: int) -> None:
                if progress and total > 0:
                    progress(base + int(span * min(1.0, downloaded / total)), f"正在下载 {name} · {label}")

            download_file(url, target, timeout=45, proxy_port=proxy, progress=on_chunk)
            if not target.is_file() or target.stat().st_size < minimum:
                size = target.stat().st_size if target.exists() else 0
                target.unlink(missing_ok=True)
                errors.append(f"{url}（{label}）：文件过小 {size} B")
                continue
            with target.open("rb") as handle:
                head = handle.read(256)
            if head.lstrip().startswith((b"<!DOCTYPE", b"<html", b"{", b"[")):
                target.unlink(missing_ok=True)
                errors.append(f"{url}（{label}）：返回了非数据内容")
                continue
            log.info("Geo 数据 %s 下载完成（%s，%s B）", name, label, target.stat().st_size)
            return
        except (NetworkError, OSError) as exc:
            errors.append(f"{url}（{label}）：{exc}")
            target.unlink(missing_ok=True)
    raise GeoDataError(f"无法下载 {name}。\n" + "\n".join(errors[-3:]))


def download_geodata(
    kinds: Iterable[str],
    *,
    data_dir: Path = APP_DATA,
    proxy_ports: list[int] | None = None,
    progress: Progress | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    ensure_dirs()
    names = filenames_for(kinds, config)
    names = [name for name in names if not is_ready(name, data_dir)]
    if not names:
        return
    total_weight = sum(8.4 if name.startswith("geoip") else 4.3 for name in names)
    base = 0.0
    for index, name in enumerate(names):
        weight = 8.4 if name.startswith("geoip") else 4.3
        start = int(base / total_weight * 100)
        span = max(3, int(weight / total_weight * 100))
        if progress:
            progress(start, f"正在准备 {name}…")
        _download_one(name, data_dir, proxy_ports=proxy_ports, progress=progress, base=start, span=span)
        base += weight
    if progress:
        progress(100, "Geo 数据已就绪")


def local_proxy_ports(preferred: int | None = None) -> list[int]:
    """常见本地代理端口，去重后用于 Geo 数据下载回退。"""
    ports: list[int] = []
    for port in (preferred, 7890, 7897, 7898, 7899, 1080):
        try:
            port = int(port) if port else 0
        except (TypeError, ValueError):
            continue
        if 0 < port < 65536 and port not in ports:
            ports.append(port)
    return ports
