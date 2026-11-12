"""网络请求与下载（订阅、内核更新）。"""

from __future__ import annotations

import base64
import gzip
import io
import os
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from . import VERSION

USER_AGENT = f"CatMeta/{VERSION} (Linux; mihomo)"
ProgressCallback = Callable[[int, int], None]


class NetworkError(Exception):
    pass


def _session() -> requests.Session:
    session = requests.Session()
    # 绝不能继承系统代理：内核 API 与订阅下载都需要可预测的直连行为。
    session.trust_env = False
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _try_get(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None,
    proxy_port: int | None,
    stream: bool = False,
) -> requests.Response:
    session = _session()
    errors: list[str] = []
    attempts: list[dict[str, str] | None] = [None]
    if proxy_port:
        proxy = f"http://127.0.0.1:{int(proxy_port)}"
        attempts.append({"http": proxy, "https": proxy})
    for proxies in attempts:
        try:
            resp = session.get(
                url,
                timeout=(6, timeout),
                headers=headers,
                proxies=proxies,
                stream=stream,
                allow_redirects=True,
            )
            if resp.status_code >= 400:
                errors.append(f"HTTP {resp.status_code}")
                resp.close()
                continue
            return resp
        except requests.RequestException as exc:
            errors.append(type(exc).__name__)
            session.close()
            session = _session()
    raise NetworkError("网络请求失败：" + "；".join(dict.fromkeys(errors)))


def fetch_text(
    url: str,
    *,
    timeout: float = 25,
    headers: dict[str, str] | None = None,
    proxy_port: int | None = None,
) -> tuple[str, dict[str, str]]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise NetworkError("仅支持 http/https 链接")
    resp = _try_get(url, timeout=timeout, headers=headers, proxy_port=proxy_port)
    try:
        content = resp.content
        encoding = resp.encoding or "utf-8"
        try:
            text = content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            text = content.decode("utf-8", errors="replace")
        return text, {k.lower(): v for k, v in resp.headers.items()}
    finally:
        resp.close()


def download_file(
    url: str,
    destination: Path,
    *,
    timeout: float = 90,
    headers: dict[str, str] | None = None,
    proxy_port: int | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    resp = _try_get(url, timeout=timeout, headers=headers, proxy_port=proxy_port, stream=True)
    total = int(resp.headers.get("Content-Length") or 0)
    fd, tmp = tempfile.mkstemp(prefix=f".{destination.name}-", dir=str(destination.parent))
    downloaded = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
        os.replace(tmp, destination)
        return destination
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if isinstance(exc, NetworkError):
            raise
        raise NetworkError(f"下载失败：{exc}") from exc
    finally:
        resp.close()


def maybe_decompress_gzip(path: Path) -> Path:
    """如果文件是 gzip 数据，则原地解压并返回新路径。"""
    path = Path(path)
    with path.open("rb") as handle:
        magic = handle.read(2)
    if magic != b"\x1f\x8b":
        return path
    try:
        with gzip.open(path, "rb") as source:
            data = source.read()
    except OSError as exc:
        raise NetworkError(f"gzip 解压失败：{exc}") from exc
    target = path.with_suffix("")
    target.write_bytes(data)
    path.unlink(missing_ok=True)
    return target


def b64decode_if_needed(text: str) -> str:
    """部分订阅会返回 base64 文本，这里做一次宽松检测。"""
    compact = "".join(text.split())
    if len(compact) < 32 or ":" in compact[:80] and compact.lstrip().startswith(("proxies:", "port:")):
        return text
    if any(token in text for token in ("proxies:", "proxy-groups:", "rules:", "mixed-port:")):
        return text
    try:
        decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return text
    if "proxies:" in decoded or "proxy-groups:" in decoded:
        return decoded
    return text


def github_latest_release(repo: str, proxy_port: int | None = None) -> dict[str, Any]:
    import json

    text, _ = fetch_text(
        f"https://api.github.com/repos/{repo}/releases/latest",
        timeout=25,
        headers={"Accept": "application/vnd.github+json"},
        proxy_port=proxy_port,
    )
    try:
        return json.loads(text)
    except ValueError as exc:
        raise NetworkError("GitHub 返回了无效数据") from exc
