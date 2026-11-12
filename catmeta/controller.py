"""应用状态编排：内核、订阅、流量、代理与系统集成。"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

from .api import ApiError, MihomoAPI
from .mihomo import CoreError, CoreManager
from .paths import ensure_dirs
from .profiles import Profile, ProfileError, ProfileStore
from .settings import Settings
from .system import SystemProxy, notify, set_autostart
from .util import format_speed
from .ws import WebSocketClient

log = logging.getLogger("catmeta.controller")

ListenCallback = Callable[..., None]


class ControllerError(Exception):
    pass


MODE_LABELS = {"rule": "规则", "global": "全局", "direct": "直连"}


class Controller:
    def __init__(self, settings: Settings | None = None, profiles: ProfileStore | None = None):
        ensure_dirs()
        self.settings = settings or Settings()
        self.profiles = profiles or ProfileStore()
        self.core = CoreManager(self.settings)
        self.system_proxy = SystemProxy(int(self.settings.mixed_port))
        self.state = "stopped"
        self.last_error = ""
        self.started_at = 0.0
        self.connections: dict[str, Any] = {}
        self.connection_count = 0
        self.memory = 0
        self.total_up = 0
        self.total_down = 0
        self.speed_up = 0
        self.speed_down = 0
        self.history: deque[tuple[float, int, int]] = deque(maxlen=120)
        self.logs: deque[dict[str, Any]] = deque(maxlen=2000)
        self._listeners: dict[str, list[ListenCallback]] = {}
        self._ws_traffic: WebSocketClient | None = None
        self._ws_logs: WebSocketClient | None = None
        self._start_lock = threading.RLock()
        self._stop_lock = threading.RLock()
        self._api_failures = 0
        self._last_poll = 0.0
        self._last_node_poll = 0.0
        self._node_poll_lock = threading.Lock()
        self.current_proxy_group = ""
        self.current_proxy_name = ""
        self._ui_visible = False
        self._tick_source = 0
        self._update_lock = threading.Lock()
        self._shutting_down = False
        self._unseen_errors = 0
        self.core.on_exit(self._core_exited)
        # 只有当前系统代理确实指向本应用端口时才认为它处于开启状态。
        try:
            if self.system_proxy.is_enabled():
                self.settings.set("system_proxy", True)
        except Exception:
            log.debug("检测系统代理失败", exc_info=True)

    # ---------------- 事件 ----------------
    def subscribe(self, event: str, callback: ListenCallback) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: ListenCallback) -> None:
        try:
            self._listeners.get(event, []).remove(callback)
        except ValueError:
            pass

    def emit(self, event: str, *args: Any) -> None:
        if threading.current_thread() is threading.main_thread():
            self._dispatch(event, *args)
        else:
            GLib.idle_add(self._dispatch, event, *args)

    def _dispatch(self, event: str, *args: Any) -> bool:
        for callback in list(self._listeners.get(event, [])):
            try:
                callback(*args)
            except Exception:
                log.exception("事件 %s 的监听器出错", event)
        for callback in list(self._listeners.get("*", [])):
            try:
                callback(event, *args)
            except Exception:
                log.exception("通配事件监听器出错")
        return False

    # ---------------- 状态 ----------------
    def _set_state(self, state: str, error: str = "") -> None:
        changed = state != self.state or error != self.last_error
        self.state = state
        self.last_error = error
        if state == "running":
            self.started_at = time.time()
        if changed:
            self.emit("state", state, error)
            self.emit("metrics")

    @property
    def running(self) -> bool:
        return self.state == "running" and self.core.running

    @property
    def current_profile(self) -> Profile | None:
        return self.profiles.current()

    @property
    def api(self) -> MihomoAPI:
        return self.core.api()

    def mode(self) -> str:
        return str(self.settings.mode or "rule")

    def start(self) -> None:
        with self._start_lock:
            if self.running:
                return
            if self.state == "starting":
                return
            self._set_state("starting")
            profile = self.profiles.current()
            if not profile:
                self._set_state("error", "还没有可用的配置，请先导入订阅")
                raise ControllerError(self.last_error)
            try:
                if not self.core.installed:
                    raise CoreError("尚未安装 mihomo 内核，请先在设置中下载内核")
                data = self.profiles.data(profile.id)
                self.core.start(data, progress=lambda _percent, text: self.emit("startup-progress", text))
                self.api.set_mode(self.mode())
                self._api_failures = 0
                self._last_node_poll = time.time()
                self.connection_count = 0
                self.connections = {}
                self.memory = 0
                self.total_up = 0
                self.total_down = 0
                self.speed_up = 0
                self.speed_down = 0
                self.history.clear()
                self._start_websockets()
                self._set_state("running")
                if self._ui_visible:
                    self._poll_current_proxy()
                if self.settings.system_proxy:
                    ok, message = self.system_proxy.apply()
                    if not ok:
                        self.settings.set("system_proxy", False)
                        self.emit("settings", self.settings.as_dict())
                        log.warning("系统代理启用失败：%s", message)
                self.emit("profiles")
                self.emit("traffic")
            except (CoreError, ProfileError, ApiError, OSError) as exc:
                self._stop_websockets()
                try:
                    self.core.stop()
                except Exception:
                    pass
                message = str(exc)
                if isinstance(exc, ApiError):
                    message = f"控制器连接失败：{exc}"
                self._clear_current_proxy()
                self._set_state("error", message)
                raise ControllerError(message) from exc
            except Exception as exc:
                self._stop_websockets()
                try:
                    self.core.stop()
                except Exception:
                    pass
                self._clear_current_proxy()
                self._set_state("error", f"启动失败：{exc}")
                raise ControllerError(self.last_error) from exc

    def stop(self, *, disable_proxy: bool = True) -> None:
        with self._stop_lock:
            if self.state == "stopped" and not self.core.running:
                return
            self._set_state("stopping")
            self._stop_websockets()
            try:
                self.core.stop()
            except Exception:
                log.exception("停止内核失败")
            if disable_proxy and self.settings.system_proxy:
                try:
                    self.system_proxy.disable()
                except Exception:
                    log.debug("关闭系统代理失败", exc_info=True)
                self.settings.set("system_proxy", False)
            self.speed_up = 0
            self.speed_down = 0
            self.started_at = 0.0
            self.connection_count = 0
            self.connections = {}
            self._clear_current_proxy()
            self._set_state("stopped")
            self.emit("traffic")
            self.emit("settings", self.settings.as_dict())

    def toggle(self) -> None:
        if self.running or self.state == "starting":
            self.stop()
        else:
            self.start()

    def restart(self) -> None:
        with self._start_lock:
            if self.state in ("running", "error", "starting"):
                self.stop(disable_proxy=False)
            self.start()
        self.emit("profiles")

    def _core_exited(self, code: int | None) -> None:
        GLib.idle_add(self._handle_core_exit, code)

    def _handle_core_exit(self, code: int | None) -> bool:
        if self._shutting_down:
            return False
        self._stop_websockets()
        if self.settings.system_proxy:
            try:
                self.system_proxy.disable()
            except Exception:
                pass
            self.settings.set("system_proxy", False)
        tail = self.core.log_tail(4)
        message = f"mihomo 内核意外退出（代码 {code}）" + (f"\n{tail}" if tail else "")
        self._unseen_errors += 1
        self._clear_current_proxy()
        self._set_state("error", message)
        self.emit("settings", self.settings.as_dict())
        notify("Cat Meta 内核已退出", "请检查日志或重新启动")
        return False

    # ---------------- WebSocket 与指标 ----------------
    def _start_websockets(self) -> None:
        self._stop_websockets()
        port = int(self.settings.api_port)
        secret = str(self.settings.api_secret or "")
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        self._ws_traffic = WebSocketClient(
            f"ws://127.0.0.1:{port}/traffic",
            headers=headers,
            on_message=self._on_traffic,
            name="traffic",
        )
        self._ws_traffic.start()
        level = str(self.settings.log_level or "info")
        self._ws_logs = WebSocketClient(
            f"ws://127.0.0.1:{port}/logs?level={level}",
            headers=headers,
            on_message=self._on_log,
            name="logs",
        )
        self._ws_logs.start()

    def _stop_websockets(self) -> None:
        for client in (self._ws_traffic, self._ws_logs):
            if client:
                try:
                    client.stop()
                except Exception:
                    pass
        self._ws_traffic = None
        self._ws_logs = None

    def _on_traffic(self, message: str) -> None:
        try:
            data = json.loads(message)
            up = int(data.get("up") or 0)
            down = int(data.get("down") or 0)
            # 兼容 API 返回的总量字段。
            if "upTotal" in data:
                self.total_up = int(data.get("upTotal") or self.total_up)
            if "downTotal" in data:
                self.total_down = int(data.get("downTotal") or self.total_down)
        except (ValueError, TypeError):
            return
        self.speed_up, self.speed_down = max(0, up), max(0, down)
        self.history.append((time.time(), self.speed_up, self.speed_down))
        self.emit("traffic")

    def _on_log(self, message: str) -> None:
        try:
            data = json.loads(message)
        except ValueError:
            return
        entry = {
            "type": str(data.get("type") or "info").lower(),
            "payload": str(data.get("payload") or ""),
            "time": time.time(),
        }
        self.logs.append(entry)
        self.emit("log", entry)

    def start_ticker(self) -> None:
        if self._tick_source:
            return
        self._tick_source = GLib.timeout_add(1000, self._tick)

    def stop_ticker(self) -> None:
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0

    def _tick(self) -> bool:
        if self._shutting_down:
            return False
        now = time.time()
        if self.running and self._ui_visible and now - self._last_poll >= 2.0:
            self._last_poll = now
            self._poll_metrics()
        if self.running and self._ui_visible and now - self._last_node_poll >= 8.0:
            self.refresh_current_node()
        self.emit("tick")
        return True

    def _poll_metrics(self) -> None:
        try:
            data = self.api.connections()
        except ApiError:
            self._api_failures += 1
            if self._api_failures >= 3 and self.core.running:
                log.warning("连续无法访问 mihomo API，标记为异常")
                self._set_state("error", "与 mihomo API 的连接已中断")
            return
        self._api_failures = 0
        self.connections = data
        conns = data.get("connections") if isinstance(data.get("connections"), list) else []
        self.connection_count = len(conns)
        try:
            self.memory = int(data.get("memory") or 0)
        except (TypeError, ValueError):
            self.memory = 0
        if self.memory <= 0:
            self.memory = self.core.memory_usage()
        try:
            self.total_up = int(data.get("uploadTotal") or self.total_up)
            self.total_down = int(data.get("downloadTotal") or self.total_down)
        except (TypeError, ValueError):
            pass
        self.emit("connections", data)
        self.emit("metrics")

    def session_summary(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "error": self.last_error,
            "running": self.running,
            "uptime": max(0, time.time() - self.started_at) if self.started_at else 0,
            "memory": self.memory,
            "connections": self.connection_count,
            "up": self.speed_up,
            "down": self.speed_down,
            "total_up": self.total_up,
            "total_down": self.total_down,
            "version": self.core.version(),
            "mixed_port": int(self.settings.mixed_port),
            "api_port": int(self.settings.api_port),
            "mode": self.mode(),
            "profile": self.current_profile.name if self.current_profile else "",
        }

    # ---------------- 当前节点 ----------------
    @staticmethod
    def _pick_current_proxy(data: dict[str, Any]) -> tuple[str, str]:
        """从 /proxies 结果中选出用户最关心的当前节点。"""
        candidates: list[tuple[int, int, str, str]] = []
        for name, info in data.items():
            if name == "GLOBAL" or not isinstance(info, dict):
                continue
            if info.get("type") not in ("Selector", "URLTest", "Fallback", "LoadBalance", "Relay"):
                continue
            if not isinstance(info.get("all"), list):
                continue
            now = str(info.get("now") or "")
            if not now:
                continue
            named = 0 if ("选择节点" in name or "节点选择" in name) else 1
            selector = 0 if info.get("type") == "Selector" else 1
            candidates.append((named, selector, name, now))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            return candidates[0][2], candidates[0][3]
        global_info = data.get("GLOBAL")
        if isinstance(global_info, dict) and global_info.get("now"):
            return "GLOBAL", str(global_info["now"])
        return "", ""

    def _set_current_proxy(self, group: str, name: str) -> None:
        if group == self.current_proxy_group and name == self.current_proxy_name:
            return
        self.current_proxy_group = group
        self.current_proxy_name = name
        self.emit("proxy-node", group, name)

    def _clear_current_proxy(self) -> None:
        self._set_current_proxy("", "")

    def _poll_current_proxy(self) -> None:
        if self._shutting_down or not self.running or not self._ui_visible:
            return
        if not self._node_poll_lock.acquire(blocking=False):
            return
        try:
            data = self.api.proxies()
            self._set_current_proxy(*self._pick_current_proxy(data))
        except ApiError:
            pass
        finally:
            self._node_poll_lock.release()

    def set_ui_visible(self, visible: bool) -> None:
        """窗口隐藏到托盘时暂停当前节点轮询。"""
        visible = bool(visible)
        if visible == self._ui_visible:
            return
        self._ui_visible = visible
        if visible and self.running:
            self._last_poll = 0.0
            self._last_node_poll = 0.0
            self.refresh_current_node(force=True)

    def refresh_current_node(self, force: bool = False) -> None:
        if not self.running or self._shutting_down or not self._ui_visible:
            return
        now = time.time()
        if not force and now - self._last_node_poll < 1.5:
            return
        self._last_node_poll = now
        threading.Thread(target=self._poll_current_proxy, name="catmeta-current-node", daemon=True).start()

    # ---------------- 代理与模式 ----------------
    def set_mode(self, mode: str) -> None:
        if mode not in MODE_LABELS:
            raise ControllerError("无效的模式")
        self.settings.set("mode", mode)
        if self.running:
            try:
                self.api.set_mode(mode)
            except ApiError as exc:
                raise ControllerError(f"切换模式失败：{exc}") from exc
        self.emit("mode", mode)
        self.emit("settings", self.settings.as_dict())

    def select_proxy(self, group: str, name: str) -> None:
        if not self.running:
            raise ControllerError("内核未运行")
        try:
            self.api.select(group, name)
        except ApiError as exc:
            raise ControllerError(f"切换节点失败：{exc}") from exc
        self._set_current_proxy(group, name)
        self.emit("proxies")

    def test_group(self, group: str) -> dict[str, int]:
        if not self.running:
            raise ControllerError("内核未运行")
        try:
            return self.api.group_delay(group, str(self.settings.test_url), 5000)
        except ApiError as exc:
            raise ControllerError(f"延迟测试失败：{exc}") from exc

    def test_proxy(self, name: str) -> int:
        if not self.running:
            raise ControllerError("内核未运行")
        try:
            return self.api.delay(name, str(self.settings.test_url), 5000)
        except ApiError as exc:
            raise ControllerError(f"延迟测试失败：{exc}") from exc

    def proxies(self) -> dict[str, Any]:
        if not self.running:
            return {}
        try:
            return self.api.proxies()
        except ApiError:
            return {}

    def rules(self) -> list[dict[str, Any]]:
        if not self.running:
            return []
        try:
            return self.api.rules()
        except ApiError:
            return []

    # ---------------- 订阅 ----------------
    def use_profile(self, profile_id: str, *, restart: bool = True) -> None:
        self.profiles.set_current(profile_id)
        self.emit("profiles")
        if self.running and restart:
            self.restart()

    def update_profile(self, profile_id: str) -> Profile:
        proxy_port = int(self.settings.mixed_port) if self.running else None
        try:
            profile = self.profiles.update(profile_id, proxy_port=proxy_port)
        except ProfileError:
            raise
        self.emit("profiles")
        if self.running and profile_id == self.profiles.current_id:
            self.restart()
        return profile

    def delete_profile(self, profile_id: str) -> None:
        was_current = profile_id == self.profiles.current_id
        self.profiles.delete(profile_id)
        self.emit("profiles")
        if was_current and self.running:
            if self.profiles.current():
                self.restart()
            else:
                self.stop()

    def save_profile_content(self, profile_id: str, text: str) -> None:
        self.profiles.save_content(profile_id, text)
        self.emit("profiles")
        if self.running and profile_id == self.profiles.current_id:
            self.restart()

    def add_profile_remote(self, url: str, name: str = "") -> Profile:
        proxy_port = int(self.settings.mixed_port) if self.running else None
        profile = self.profiles.add_remote(url, name, proxy_port=proxy_port)
        self.emit("profiles")
        return profile

    def add_profile_local(self, path: str, name: str = "") -> Profile:
        profile = self.profiles.add_local_file(path, name)
        self.emit("profiles")
        return profile

    def add_profile_manual(self, name: str, text: str) -> Profile:
        profile = self.profiles.add_manual(name, text)
        self.emit("profiles")
        return profile

    # ---------------- 设置 ----------------
    def save_settings(self, values: dict[str, Any], *, restart: bool = True) -> None:
        old = self.settings.as_dict()
        restart_keys = {
            "mixed_port",
            "api_port",
            "allow_lan",
            "ipv6",
            "tun",
            "tun_stack",
            "log_level",
            "api_secret",
            "core_path",
        }
        new = copy.deepcopy(old)
        for key, value in values.items():
            if key in old:
                new[key] = value
        if new.get("mixed_port") != old.get("mixed_port") or new.get("api_port") != old.get("api_port"):
            for key in ("mixed_port", "api_port"):
                try:
                    port = int(new[key])
                except (TypeError, ValueError):
                    raise ControllerError("端口必须是数字") from None
                if not 1024 <= port <= 65535:
                    raise ControllerError("端口需在 1024–65535 之间")
                new[key] = port
            if int(new["mixed_port"]) == int(new["api_port"]):
                raise ControllerError("混合代理端口与控制器端口不能相同")
        self.settings.update(new)
        self.system_proxy.port = int(new["mixed_port"])
        if bool(new.get("auto_start")) != bool(old.get("auto_start")):
            set_autostart(bool(new.get("auto_start")))
        needs_restart = bool(restart_keys.intersection({k for k, v in new.items() if old.get(k) != v}))
        if old.get("mode") != new.get("mode"):
            self.set_mode(str(new.get("mode")))
        if self.running:
            if needs_restart:
                self.restart()
            else:
                self.api.patch_configs(
                    {
                        "mode": str(new.get("mode") or "rule"),
                        "log-level": str(new.get("log-level") or new.get("log_level") or "info"),
                    }
                )
        if bool(new.get("system_proxy")) and not bool(old.get("system_proxy")):
            self.set_system_proxy(True)
        elif not bool(new.get("system_proxy")) and bool(old.get("system_proxy")):
            self.set_system_proxy(False)
        self.emit("settings", self.settings.as_dict())
        self.emit("mode", self.mode())
        self.emit("metrics")

    def set_system_proxy(self, enabled: bool) -> tuple[bool, str]:
        if enabled:
            if not self.running:
                try:
                    self.start()
                except Exception:
                    self.settings.set("system_proxy", False)
                    self.emit("settings", self.settings.as_dict())
                    raise
            ok, message = self.system_proxy.apply()
            if not ok:
                self.settings.set("system_proxy", False)
                self.emit("settings", self.settings.as_dict())
                raise ControllerError(message)
            self.settings.set("system_proxy", True)
        else:
            ok, message = self.system_proxy.disable()
            self.settings.set("system_proxy", False)
            if not ok:
                self.emit("settings", self.settings.as_dict())
                raise ControllerError(message)
        self.emit("settings", self.settings.as_dict())
        return True, message

    def set_quick_toggle(self, key: str, enabled: bool) -> None:
        if key == "system_proxy":
            self.set_system_proxy(enabled)
            return
        if key == "tun":
            self.save_settings({"tun": bool(enabled)}, restart=True)
            return
        if key == "allow_lan":
            self.save_settings({"allow_lan": bool(enabled)}, restart=True)
            return
        raise ControllerError("未知开关")

    # ---------------- 订阅自动更新 ----------------
    def maybe_auto_update(self, force: bool = False) -> None:
        if not self.settings.auto_update_profiles:
            return
        now = time.time()
        last = float(self.settings.last_profile_update_check or 0)
        if not force and now - last < 1800:
            return
        due = self.profiles.due_for_update(int(self.settings.update_interval_hours))
        if not due:
            self.settings.set("last_profile_update_check", now)
            return
        if not self._update_lock.acquire(blocking=False):
            return
        self.settings.set("last_profile_update_check", now)

        def worker() -> None:
            updated_ids: list[str] = []
            errors: list[str] = []
            try:
                for profile in due:
                    try:
                        self.profiles.update(profile.id, proxy_port=int(self.settings.mixed_port) if self.running else None)
                        updated_ids.append(profile.id)
                    except ProfileError as exc:
                        errors.append(f"{profile.name}: {exc}")
                self.emit("profiles")
                if updated_ids:
                    current = self.profiles.current_id
                    if self.running and current in updated_ids:
                        GLib.idle_add(self._auto_restart)
                    notify("订阅已自动更新", f"更新了 {len(updated_ids)} 个配置")
                if errors:
                    log.warning("自动更新部分失败：%s", "；".join(errors))
            finally:
                self._update_lock.release()

        threading.Thread(target=worker, name="catmeta-autoupdate", daemon=True).start()

    def _auto_restart(self) -> bool:
        try:
            self.restart()
        except Exception:
            log.exception("自动更新后重启失败")
        return False

    # ---------------- 关闭 ----------------
    def shutdown(self) -> None:
        self._shutting_down = True
        self.stop_ticker()
        self._stop_websockets()
        # 退出应用时始终关闭系统代理，避免代理指向已经停止的内核而断网。
        # 不修改 settings.system_proxy：它同时记录“下次启动是否恢复系统代理”。
        try:
            if self.system_proxy.is_enabled():
                ok, message = self.system_proxy.disable()
                if ok:
                    log.info("退出时已关闭系统代理")
                else:
                    log.warning("退出时关闭系统代理失败：%s", message)
        except Exception:
            log.exception("退出时关闭系统代理失败")
        if self.settings.stop_core_on_quit:
            self.core.stop()
            self.state = "stopped"

    def rename_profile(self, profile_id: str, name: str) -> None:
        self.profiles.rename(profile_id, name)
        self.emit("profiles")

    def close_connection(self, connection_id: str) -> None:
        if not self.running:
            raise ControllerError("内核未运行")
        try:
            self.api.close_connection(connection_id)
        except ApiError as exc:
            raise ControllerError(f"关闭连接失败：{exc}") from exc

    def close_all_connections(self) -> None:
        if not self.running:
            raise ControllerError("内核未运行")
        try:
            self.api.close_all_connections()
        except ApiError as exc:
            raise ControllerError(f"断开连接失败：{exc}") from exc

    def clear_logs(self) -> None:
        self.logs.clear()
        self.emit("logs-cleared")
