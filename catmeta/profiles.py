"""订阅与本地配置管理。"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import yaml

from .net import NetworkError, b64decode_if_needed, fetch_text
from .paths import PROFILES_DIR, PROFILES_FILE, ensure_dirs
from .util import safe_filename


class ProfileError(Exception):
    pass


SAMPLE_PROFILE = """\
# Cat Meta 示例配置
# 导入机场订阅后，即可在此处看到节点、规则与流量信息。
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
ipv6: false

proxies: []

proxy-groups:
  - name: 节点选择
    type: select
    proxies:
      - DIRECT
  - name: 自动选择
    type: url-test
    url: https://www.gstatic.com/generate_204
    interval: 300
    tolerance: 50
    proxies:
      - DIRECT

rules:
  - DOMAIN-SUFFIX,local,DIRECT
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,100.64.0.0/10,DIRECT,no-resolve
  - DOMAIN-SUFFIX,cn,DIRECT
  - MATCH,节点选择
"""


def validate_config_text(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise ProfileError("配置内容为空")
    if len(text.encode("utf-8")) > 20 * 1024 * 1024:
        raise ProfileError("配置文件超过 20 MB")
    text = b64decode_if_needed(text)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        message = str(exc).splitlines()[0] if str(exc) else "格式错误"
        raise ProfileError(f"YAML 解析失败：{message}") from exc
    if not isinstance(data, dict):
        raise ProfileError("配置根节点必须是映射（YAML 对象）")
    if not any(key in data for key in ("proxies", "proxy-groups", "rules", "proxy-providers")):
        raise ProfileError("没有找到 proxies、proxy-groups 或 rules 字段")
    for key in ("proxies", "proxy-groups", "rules"):
        value = data.get(key)
        if value is not None and not isinstance(value, list):
            raise ProfileError(f"{key} 必须是列表")
    return data


def decode_header_title(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
        if decoded and all(ch.isprintable() for ch in decoded):
            return decoded.strip()
    except (ValueError, UnicodeDecodeError):
        pass
    return value


def parse_subscription(headers: dict[str, str]) -> dict[str, int]:
    info = headers.get("subscription-userinfo", "")
    result = {"upload": 0, "download": 0, "total": 0, "expire": 0}
    for part in info.split(";"):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip().lower()
        if key in result:
            try:
                result[key] = int(float(raw.strip()))
            except ValueError:
                pass
    return result


@dataclass
class Profile:
    id: str
    name: str
    kind: str  # remote / local / manual
    url: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    subscription: dict[str, int] = field(default_factory=dict)
    order: int = 0
    auto_update: bool = True

    @property
    def filename(self) -> str:
        return f"{self.id}.yaml"

    @property
    def path(self) -> Path:
        return PROFILES_DIR / self.filename

    @property
    def kind_label(self) -> str:
        return {"remote": "订阅", "local": "本地", "manual": "手动"}.get(self.kind, "配置")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "subscription": dict(self.subscription),
            "order": self.order,
            "auto_update": self.auto_update,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        sub = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
        return cls(
            id=str(data.get("id") or uuid()),
            name=str(data.get("name") or "未命名配置"),
            kind=str(data.get("kind") or "manual"),
            url=str(data.get("url") or ""),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            subscription={k: int(sub.get(k, 0) or 0) for k in ("upload", "download", "total", "expire")},
            order=int(data.get("order") or 0),
            auto_update=bool(data.get("auto_update", True)),
        )


def uuid() -> str:
    return secrets.token_hex(8)


class ProfileStore:
    def __init__(self, path: Path = PROFILES_FILE):
        ensure_dirs()
        self.path = Path(path)
        self._lock = threading.RLock()
        self._profiles: list[Profile] = []
        self._current: str = ""
        existed = self.path.exists()
        self.load()
        if not self._profiles and not existed:
            self._install_sample()

    def load(self) -> None:
        with self._lock:
            data: dict[str, Any] = {}
            try:
                if self.path.exists():
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = raw
            except (OSError, ValueError):
                data = {}
            items = data.get("items") if isinstance(data.get("items"), list) else []
            self._profiles = [Profile.from_dict(item) for item in items if isinstance(item, dict)]
            self._current = str(data.get("current") or "")
            if self._profiles and self._current not in {p.id for p in self._profiles}:
                self._current = self._profiles[0].id
                self.save()

    def save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "current": self._current,
                "items": [p.to_dict() for p in sorted(self._profiles, key=lambda p: p.order)],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".profiles-", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass

    def _install_sample(self) -> None:
        profile = Profile(id=uuid(), name="本地直连", kind="manual", order=0)
        profile.path.write_text(SAMPLE_PROFILE, encoding="utf-8")
        with self._lock:
            self._profiles = [profile]
            self._current = profile.id
            self.save()

    def all(self) -> list[Profile]:
        with self._lock:
            return sorted(self._profiles, key=lambda p: (p.order, p.created_at))

    def get(self, profile_id: str | None) -> Profile | None:
        with self._lock:
            for profile in self._profiles:
                if profile.id == profile_id:
                    return profile
            return None

    def current(self) -> Profile | None:
        with self._lock:
            return self.get(self._current)

    @property
    def current_id(self) -> str:
        with self._lock:
            return self._current

    def set_current(self, profile_id: str) -> Profile:
        profile = self.get(profile_id)
        if not profile:
            raise ProfileError("配置不存在")
        with self._lock:
            self._current = profile_id
            self.save()
        return profile

    def _next_order(self) -> int:
        with self._lock:
            return max((p.order for p in self._profiles), default=-1) + 1

    def _unique_name(self, name: str) -> str:
        existing = {p.name for p in self._profiles}
        if name not in existing:
            return name
        index = 2
        while f"{name} ({index})" in existing:
            index += 1
        return f"{name} ({index})"

    def _write_profile(self, profile: Profile, text: str) -> None:
        profile.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{profile.id}-", dir=str(profile.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, profile.path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass

    def add_remote(self, url: str, name: str = "", proxy_port: int | None = None) -> Profile:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ProfileError("请输入有效的订阅链接（http/https）")
        text, headers = self._download(url, proxy_port)
        data = validate_config_text(text)
        title = decode_header_title(headers.get("profile-title", "")) or name
        if not title:
            parsed = urlparse(url)
            title = parsed.hostname or "远程订阅"
            path_name = Path(parsed.path).stem
            if path_name and path_name not in ("", "download", "sub"):
                title = f"{title} · {path_name}"
        profile = Profile(
            id=uuid(),
            name=self._unique_name(title[:80]),
            kind="remote",
            url=url,
            order=self._next_order(),
            subscription=parse_subscription(headers),
        )
        self._write_profile(profile, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        with self._lock:
            self._profiles.append(profile)
            if not self._current:
                self._current = profile.id
            self.save()
        return profile

    def add_local_file(self, path: str | Path, name: str = "") -> Profile:
        source = Path(path)
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProfileError(f"无法读取文件：{exc}") from exc
        data = validate_config_text(text)
        profile = Profile(
            id=uuid(),
            name=self._unique_name((name or source.stem or "本地配置")[:80]),
            kind="local",
            order=self._next_order(),
        )
        self._write_profile(profile, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        with self._lock:
            self._profiles.append(profile)
            if not self._current:
                self._current = profile.id
            self.save()
        return profile

    def add_manual(self, name: str, text: str) -> Profile:
        data = validate_config_text(text)
        profile = Profile(
            id=uuid(),
            name=self._unique_name((name or "手动配置")[:80]),
            kind="manual",
            order=self._next_order(),
        )
        self._write_profile(profile, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        with self._lock:
            self._profiles.append(profile)
            if not self._current:
                self._current = profile.id
            self.save()
        return profile

    def _download(self, url: str, proxy_port: int | None) -> tuple[str, dict[str, str]]:
        try:
            return fetch_text(
                url,
                timeout=30,
                headers={"Accept": "text/yaml, application/yaml, text/plain, */*"},
                proxy_port=proxy_port,
            )
        except NetworkError as exc:
            raise ProfileError(f"下载订阅失败：{exc}") from exc

    def update(self, profile_id: str, proxy_port: int | None = None) -> Profile:
        profile = self.get(profile_id)
        if not profile:
            raise ProfileError("配置不存在")
        if profile.kind != "remote" or not profile.url:
            raise ProfileError("本地配置无需从网络更新")
        text, headers = self._download(profile.url, proxy_port)
        data = validate_config_text(text)
        self._write_profile(profile, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        profile.updated_at = time.time()
        profile.subscription = parse_subscription(headers) or profile.subscription
        title = decode_header_title(headers.get("profile-title", ""))
        if title:
            profile.name = title[:80]
        with self._lock:
            self.save()
        return profile

    def save_content(self, profile_id: str, text: str) -> Profile:
        profile = self.get(profile_id)
        if not profile:
            raise ProfileError("配置不存在")
        data = validate_config_text(text)
        self._write_profile(profile, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        profile.updated_at = time.time()
        with self._lock:
            self.save()
        return profile

    def rename(self, profile_id: str, name: str) -> Profile:
        profile = self.get(profile_id)
        if not profile:
            raise ProfileError("配置不存在")
        name = (name or "").strip()
        if not name:
            raise ProfileError("名称不能为空")
        profile.name = self._unique_name(name[:80])
        with self._lock:
            self.save()
        return profile

    def delete(self, profile_id: str) -> None:
        profile = self.get(profile_id)
        if not profile:
            return
        with self._lock:
            self._profiles = [p for p in self._profiles if p.id != profile_id]
            if self._current == profile_id:
                self._current = self._profiles[0].id if self._profiles else ""
            self.save()
        try:
            profile.path.unlink(missing_ok=True)
        except OSError:
            pass

    def content(self, profile_id: str | None = None) -> str:
        profile = self.get(profile_id) if profile_id else self.current()
        if not profile:
            raise ProfileError("尚未选择配置")
        try:
            return profile.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProfileError(f"无法读取配置：{exc}") from exc

    def data(self, profile_id: str | None = None) -> dict[str, Any]:
        return validate_config_text(self.content(profile_id))

    def stats(self, profile_id: str | None = None) -> dict[str, int]:
        try:
            data = self.data(profile_id)
        except ProfileError:
            return {"proxies": 0, "groups": 0, "rules": 0, "providers": 0}
        proxies = data.get("proxies") if isinstance(data.get("proxies"), list) else []
        groups = data.get("proxy-groups") if isinstance(data.get("proxy-groups"), list) else []
        rules = data.get("rules") if isinstance(data.get("rules"), list) else []
        providers = data.get("proxy-providers") if isinstance(data.get("proxy-providers"), dict) else {}
        return {
            "proxies": len(proxies),
            "groups": len(groups),
            "rules": len(rules),
            "providers": len(providers),
        }

    def due_for_update(self, interval_hours: int) -> list[Profile]:
        cutoff = time.time() - max(1, int(interval_hours)) * 3600
        return [p for p in self.all() if p.kind == "remote" and p.auto_update and p.updated_at < cutoff]

    def export_to(self, profile_id: str, destination: str | Path) -> None:
        profile = self.get(profile_id)
        if not profile:
            raise ProfileError("配置不存在")
        Path(destination).write_text(self.content(profile_id), encoding="utf-8")
