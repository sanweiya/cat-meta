"""mihomo 外部控制器 REST API。"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote, urlencode

import requests

log = logging.getLogger("catmeta.api")


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MihomoAPI:
    def __init__(self, port: int, secret: str = "", host: str = "127.0.0.1", timeout: float = 6.0):
        self.host = host
        self.port = int(port)
        self.secret = secret or ""
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "CatMeta/1.0"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
        tolerate: tuple[int, ...] = (),
    ) -> Any:
        url = self.base_url + path
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers(),
                timeout=timeout or self.timeout,
                proxies={"http": None, "https": None},
            )
        except requests.RequestException as exc:
            raise ApiError(f"无法连接 mihomo API：{exc}") from exc
        if response.status_code >= 400:
            if response.status_code in tolerate:
                result = {"status": response.status_code, "message": response.text[:300]}
                session.close()
                return result
            message = response.text.strip()[:300]
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    message = str(payload.get("message") or payload.get("error") or message)
            except ValueError:
                pass
            session.close()
            raise ApiError(message or f"HTTP {response.status_code}", response.status_code)
        if not response.content:
            session.close()
            return {}
        try:
            result = response.json()
        except ValueError:
            result = {"raw": response.text}
        session.close()
        return result

    # ---- 基础信息 ----
    def version(self, timeout: float | None = None) -> dict[str, Any]:
        data = self.request("GET", "/version", timeout=timeout or 2.0)
        return data if isinstance(data, dict) else {}

    def configs(self) -> dict[str, Any]:
        data = self.request("GET", "/configs")
        return data if isinstance(data, dict) else {}

    def patch_configs(self, values: dict[str, Any]) -> None:
        self.request("PATCH", "/configs", json_body=values)

    def mode(self) -> str:
        return str(self.configs().get("mode") or "")

    def set_mode(self, mode: str) -> None:
        self.patch_configs({"mode": mode})

    # ---- 代理 ----
    def proxies(self) -> dict[str, Any]:
        data = self.request("GET", "/proxies")
        proxies = data.get("proxies") if isinstance(data, dict) else {}
        return proxies if isinstance(proxies, dict) else {}

    def proxy(self, name: str) -> dict[str, Any]:
        data = self.request("GET", f"/proxies/{quote(name, safe='')}")
        return data if isinstance(data, dict) else {}

    def select(self, group: str, name: str) -> None:
        self.request("PUT", f"/proxies/{quote(group, safe='')}", json_body={"name": name})

    def delay(self, name: str, url: str, timeout_ms: int = 5000) -> int:
        params = {"url": url, "timeout": str(max(1000, int(timeout_ms)))}
        try:
            data = self.request(
                "GET",
                f"/proxies/{quote(name, safe='')}/delay",
                params=params,
                timeout=max(8.0, timeout_ms / 1000 + 3),
                tolerate=(408, 504),
            )
        except ApiError:
            raise
        if not isinstance(data, dict):
            return 0
        try:
            return max(0, int(data.get("delay") or 0))
        except (TypeError, ValueError):
            return 0

    def group_delay(self, group: str, url: str, timeout_ms: int = 5000) -> dict[str, int]:
        params = {"url": url, "timeout": str(max(1000, int(timeout_ms)))}
        data = self.request(
            "GET",
            f"/group/{quote(group, safe='')}/delay",
            params=params,
            timeout=max(8.0, timeout_ms / 1000 + 4),
            tolerate=(408, 504),
        )
        result: dict[str, int] = {}
        if isinstance(data, dict):
            for key, value in data.items():
                try:
                    result[str(key)] = max(0, int(value))
                except (TypeError, ValueError):
                    result[str(key)] = 0
        return result

    def healthcheck(self, name: str) -> None:
        self.request("GET", f"/proxies/{quote(name, safe='')}/healthcheck")

    # ---- 连接 ----
    def connections(self) -> dict[str, Any]:
        data = self.request("GET", "/connections")
        return data if isinstance(data, dict) else {}

    def close_connection(self, connection_id: str) -> None:
        self.request("DELETE", f"/connections/{quote(connection_id, safe='')}")

    def close_all_connections(self) -> None:
        self.request("DELETE", "/connections")

    # ---- 规则 ----
    def rules(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/rules")
        rules = data.get("rules") if isinstance(data, dict) else []
        return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []

    def rule_providers(self) -> dict[str, Any]:
        data = self.request("GET", "/providers/rules")
        providers = data.get("providers") if isinstance(data, dict) else {}
        return providers if isinstance(providers, dict) else {}

    # ---- 代理提供者 ----
    def proxy_providers(self) -> dict[str, Any]:
        data = self.request("GET", "/providers/proxies")
        providers = data.get("providers") if isinstance(data, dict) else {}
        return providers if isinstance(providers, dict) else {}

    def update_proxy_provider(self, name: str) -> None:
        self.request("PUT", f"/providers/proxies/{quote(name, safe='')}")

    def provider_healthcheck(self, name: str) -> None:
        self.request("GET", f"/providers/proxies/{quote(name, safe='')}/healthcheck")

    # ---- 配置重载 ----
    def reload_config(self, path: str, force: bool = True) -> None:
        self.request("PUT", "/configs", params={"force": str(force).lower()}, json_body={"path": path})

    def is_alive(self) -> bool:
        try:
            self.version(timeout=1.5)
            return True
        except ApiError:
            return False

    @staticmethod
    def delay_params(url: str, timeout_ms: int) -> str:
        return "?" + urlencode({"url": url, "timeout": str(timeout_ms)})
