"""mihomo 内核的发现、下载、配置生成与进程管理。"""

from __future__ import annotations

import copy
import logging
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from . import VERSION
from .api import ApiError, MihomoAPI
from .geodata import GeoDataError, download_geodata, filenames_for, is_ready, local_proxy_ports, required_geodata
from .net import NetworkError, download_file, github_latest_release, maybe_decompress_gzip
from .paths import APP_DATA, BIN_DIR, CORE_LOG, PROJECT_ROOT, RUNTIME_DIR, ensure_dirs
from .settings import Settings

log = logging.getLogger("catmeta.core")

KNOWN_VERSION = "v1.19.33"
RELEASE_REPO = "MetaCubeX/mihomo"


class CoreError(Exception):
    pass


ProgressCallback = Callable[[int, str], None]
ExitCallback = Callable[[int | None], None]


def _arch() -> str:
    machine = platform.machine().lower()
    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "armv7",
        "armv6l": "armv6",
    }.get(machine, machine)


def parse_version(text: str) -> str:
    match = re.search(r"(v\d+\.\d+\.\d+)", text or "")
    return match.group(1) if match else ""


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:4])


class CoreManager:
    def __init__(self, settings: Settings):
        ensure_dirs()
        self.settings = settings
        self._process: subprocess.Popen[bytes] | None = None
        self._started_at = 0.0
        self._version_cache: tuple[str, str] | None = None
        self._lock = threading.RLock()
        self._exit_callbacks: list[ExitCallback] = []
        self._watch_thread: threading.Thread | None = None
        self._intentional_stop = False
        self.runtime_config = RUNTIME_DIR / "config.yaml"

    # ---------- 路径与版本 ----------
    @property
    def binary(self) -> Path | None:
        configured = str(self.settings.core_path or "").strip()
        if configured:
            path = Path(os.path.expanduser(configured))
            if path.is_file() and os.access(path, os.X_OK):
                return path
        for candidate in (
            BIN_DIR / "mihomo",
            PROJECT_ROOT / "bin" / "mihomo",
            Path("/usr/local/bin/mihomo"),
            Path("/usr/bin/mihomo"),
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        found = shutil.which("mihomo")
        return Path(found) if found else None

    @property
    def installed(self) -> bool:
        return self.binary is not None

    def version(self, refresh: bool = False) -> str:
        binary = self.binary
        if not binary:
            return ""
        key = str(binary)
        if not refresh and self._version_cache and self._version_cache[0] == key:
            return self._version_cache[1]
        try:
            result = subprocess.run(
                [str(binary), "-v"],
                capture_output=True,
                text=True,
                timeout=8,
                env=self._env(),
            )
            text = (result.stdout or "") + (result.stderr or "")
            version = parse_version(text)
        except (OSError, subprocess.SubprocessError):
            version = ""
        self._version_cache = (key, version)
        return version

    def core_description(self) -> str:
        version = self.version()
        return f"mihomo {version}" if version else "未找到 mihomo 内核"

    def latest_version(self) -> str:
        try:
            data = github_latest_release(RELEASE_REPO, proxy_port=self._download_proxy_port())
            return str(data.get("tag_name") or "")
        except NetworkError as exc:
            raise CoreError(f"检查内核更新失败：{exc}") from exc

    def download_core(self, progress: ProgressCallback | None = None) -> Path:
        def report(percent: int, text: str) -> None:
            if progress:
                try:
                    progress(max(0, min(100, percent)), text)
                except Exception:
                    pass

        report(2, "正在获取最新版本信息…")
        try:
            release = github_latest_release(RELEASE_REPO, proxy_port=self._download_proxy_port())
            tag = str(release.get("tag_name") or KNOWN_VERSION)
        except NetworkError:
            tag = KNOWN_VERSION
            release = {}
        assets = release.get("assets") if isinstance(release.get("assets"), list) else []
        arch = _arch()
        names = [str(a.get("name") or "") for a in assets if isinstance(a, dict)]
        chosen = ""
        for name in names:
            if f"linux-{arch}-compatible-" in name and name.endswith(".gz"):
                chosen = name
                break
        if not chosen:
            for name in names:
                if f"linux-{arch}-" in name and name.endswith(".gz") and "go1" not in name and "-v1-" not in name and "-v2-" not in name and "-v3-" not in name:
                    chosen = name
                    break
        if not chosen:
            chosen = f"mihomo-linux-{arch}-compatible-{tag}.gz"
        url = next(
            (str(a.get("browser_download_url")) for a in assets if isinstance(a, dict) and a.get("name") == chosen),
            f"https://github.com/{RELEASE_REPO}/releases/download/{tag}/{chosen}",
        )
        report(4, f"正在下载 {tag}…")

        def on_chunk(downloaded: int, total: int) -> None:
            if total > 0:
                report(4 + int(downloaded / total * 82), "正在下载内核…")
            else:
                report(min(85, 5 + downloaded // (512 * 1024)), "正在下载内核…")

        tmp_gz = BIN_DIR / ".mihomo-download.gz"
        try:
            download_file(
                url,
                tmp_gz,
                timeout=180,
                proxy_port=self._download_proxy_port(),
                progress=on_chunk,
            )
        except NetworkError as exc:
            raise CoreError(f"下载 mihomo 失败：{exc}") from exc
        report(88, "正在解压内核…")
        tmp_new = BIN_DIR / ".mihomo-new"
        try:
            if tmp_gz.read_bytes()[:2] == b"\x1f\x8b":
                import gzip

                with gzip.open(tmp_gz, "rb") as source, open(tmp_new, "wb") as target:
                    shutil.copyfileobj(source, target)
                tmp_gz.unlink(missing_ok=True)
            else:
                os.replace(tmp_gz, tmp_new)
            os.chmod(tmp_new, 0o755)
            report(94, "正在验证内核…")
            result = subprocess.run([str(tmp_new), "-v"], capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                raise CoreError("下载的内核无法运行")
            os.replace(tmp_new, BIN_DIR / "mihomo")
        except CoreError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise CoreError(f"安装内核失败：{exc}") from exc
        finally:
            tmp_gz.unlink(missing_ok=True)
            tmp_new.unlink(missing_ok=True)
        self.settings.set("core_path", str(BIN_DIR / "mihomo"))
        self._version_cache = None
        report(100, "内核已安装")
        return BIN_DIR / "mihomo"

    # ---------- 配置生成 ----------
    def build_config_data(self, profile_data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(profile_data, dict):
            raise CoreError("配置文件格式无效")
        config = copy.deepcopy(profile_data)
        config["mixed-port"] = int(self.settings.mixed_port)
        config["allow-lan"] = bool(self.settings.allow_lan)
        config["mode"] = str(self.settings.mode or "rule")
        config["log-level"] = str(self.settings.log_level or "info")
        config["ipv6"] = bool(self.settings.ipv6)
        config["external-controller"] = f"127.0.0.1:{int(self.settings.api_port)}"
        config["secret"] = str(self.settings.api_secret or "")
        if bool(self.settings.allow_lan):
            config["bind-address"] = "*"
        config.setdefault("unified-delay", True)
        config.setdefault("tcp-concurrent", True)
        config.setdefault("find-process-mode", "off")
        # 记录节点选择，重启后保持。
        profile_section = config.get("profile")
        if not isinstance(profile_section, dict):
            profile_section = {}
        profile_section["store-selected"] = True
        profile_section.setdefault("store-fake-ip", True)
        config["profile"] = profile_section

        tun_existing = config.get("tun") if isinstance(config.get("tun"), dict) else {}
        tun = dict(tun_existing)
        tun["enable"] = bool(self.settings.tun)
        if self.settings.tun:
            tun["stack"] = str(self.settings.tun_stack or "mixed")
            tun["auto-route"] = True
            tun["auto-detect-interface"] = True
            tun.setdefault("strict-route", False)
            tun["dns-hijack"] = ["any:53", "tcp://any:53"]
            dns = config.get("dns") if isinstance(config.get("dns"), dict) else {}
            dns["enable"] = True
            dns.setdefault("enhanced-mode", "fake-ip")
            dns.setdefault("fake-ip-range", "198.18.0.1/16")
            dns.setdefault("default-nameserver", ["223.5.5.5", "119.29.29.29"])
            dns.setdefault("nameserver", ["https://doh.pub/dns-query", "https://dns.alidns.com/dns-query"])
            config["dns"] = dns
        config["tun"] = tun

        proxies = config.get("proxies")
        groups = config.get("proxy-groups")
        providers = config.get("proxy-providers")
        if not proxies and not groups and not providers:
            raise CoreError("配置中没有任何代理组或节点，请先导入有效订阅")
        rules = config.get("rules")
        if not isinstance(rules, list) or not rules:
            config["rules"] = ["MATCH,DIRECT"]
        return config

    def write_runtime_config(self, profile_data: dict[str, Any]) -> Path:
        config = self.build_config_data(profile_data)
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=str(RUNTIME_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False, width=4096)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.runtime_config)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
        return self.runtime_config

    def validate_config(self, path: Path | None = None, timeout: float = 120) -> tuple[bool, str]:
        binary = self.binary
        if not binary:
            raise CoreError("未找到 mihomo 内核，请先下载内核")
        path = path or self.runtime_config
        try:
            result = subprocess.run(
                [str(binary), "-t", "-d", str(APP_DATA), "-f", str(path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env(),
            )
        except subprocess.TimeoutExpired as exc:
            return False, "配置校验超时"
        except OSError as exc:
            raise CoreError(f"无法运行内核：{exc}") from exc
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        return result.returncode == 0, output

    # ---------- 进程 ----------
    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def started_at(self) -> float:
        return self._started_at

    def on_exit(self, callback: ExitCallback) -> None:
        self._exit_callbacks.append(callback)

    def _notify_exit(self, code: int | None) -> None:
        if self._intentional_stop:
            return
        for callback in list(self._exit_callbacks):
            try:
                callback(code)
            except Exception:
                log.exception("内核退出回调失败")

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["MIHOMO_HOME"] = str(APP_DATA)
        return env

    def _download_proxy_port(self) -> int | None:
        port = int(self.settings.mixed_port)
        return port if self.running else None

    def _check_ports(self) -> None:
        if int(self.settings.mixed_port) == int(self.settings.api_port):
            raise CoreError("混合代理端口与控制器端口不能相同")
        for port, label in ((int(self.settings.mixed_port), "混合代理端口"), (int(self.settings.api_port), "API 端口")):
            if not (1 <= port <= 65535):
                raise CoreError(f"{label} {port} 无效")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                raise CoreError(f"{label} {port} 已被占用：{exc.strerror or exc}") from exc
            finally:
                sock.close()

    def _looks_like_core(self, argv: list[str]) -> bool:
        """判断进程是否为使用本应用运行时配置的 mihomo。"""
        if not argv or "-t" in argv:
            return False
        config = ""
        data = ""
        for index, arg in enumerate(argv):
            if arg in ("-f", "--config") and index + 1 < len(argv):
                config = argv[index + 1]
            elif arg.startswith("--config="):
                config = arg.split("=", 1)[1]
            elif arg in ("-d", "--directory") and index + 1 < len(argv):
                data = argv[index + 1]
            elif arg.startswith("--directory="):
                data = arg.split("=", 1)[1]
        if not config:
            return False
        try:
            if Path(config).expanduser().resolve() != self.runtime_config.resolve():
                return False
            if data and Path(data).expanduser().resolve() != APP_DATA.resolve():
                return False
        except (OSError, RuntimeError):
            return False
        return True

    def _stale_core_pids(self) -> list[int]:
        """扫描使用本应用运行时配置、但不属于当前 CoreManager 的 mihomo。"""
        current_pid = os.getpid()
        own_pid = 0
        process = self._process
        if process is not None and process.poll() is None:
            own_pid = process.pid
        pids: list[int] = []
        try:
            entries = os.scandir("/proc")
        except OSError:
            return pids
        with entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                pid = int(entry.name)
                if pid in (current_pid, own_pid):
                    continue
                try:
                    raw = Path(entry.path, "cmdline").read_bytes()
                except OSError:
                    continue
                if not raw:
                    continue
                argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
                if self._looks_like_core(argv):
                    pids.append(pid)
        return pids

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            state = stat.rsplit(")", 1)[1].split()[0]
            return state != "Z"
        except (OSError, IndexError):
            return True

    def _terminate_stale_process(self, pid: int, timeout: float = 6.0) -> bool:
        """结束上次会话遗留的内核；仅在它独占会话时清理整个会话。"""
        if not self._pid_alive(pid):
            return True
        try:
            pgid = os.getpgid(pid)
            sid = os.getsid(pid)
        except OSError:
            pgid = pid
            sid = 0
        if pgid == pid and sid == pid and pgid != os.getpgrp():
            target = -pid
        else:
            target = pid
        try:
            os.kill(target, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except OSError as exc:
            log.warning("无法结束遗留内核进程 %s：%s", pid, exc)
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(0.1)
        try:
            os.kill(target, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError as exc:
            log.warning("无法强制结束遗留内核进程 %s：%s", pid, exc)
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(0.1)
        return not self._pid_alive(pid)

    def _cleanup_stale_core(self) -> None:
        """启动前清理注销/崩溃后残留的 mihomo，避免端口被旧进程占用。

        mihomo 运行在独立会话中，桌面注销时应用可能被直接杀掉而来不及
        stop()，子进程会继续监听端口。这里只处理明确使用本应用
        runtime/config.yaml 的进程，不会动其他 mihomo 实例。
        """
        pids = self._stale_core_pids()
        if not pids:
            return
        log.warning("发现上次会话遗留的 mihomo 进程：%s，正在清理", ", ".join(map(str, pids)))
        survivors = [pid for pid in pids if not self._terminate_stale_process(pid)]
        if survivors:
            raise CoreError(
                "检测到上次会话遗留的 mihomo 内核进程（PID "
                + "、".join(map(str, survivors))
                + "），但无法结束它。请在系统监视器中结束该进程后重试。"
            )
        # 等待内核释放监听套接字。
        time.sleep(0.2)
        log.info("已清理上次会话遗留的 mihomo 进程")

    def api(self) -> MihomoAPI:
        return MihomoAPI(int(self.settings.api_port), str(self.settings.api_secret or ""))

    def start(self, profile_data: dict[str, Any], progress: Callable[[int, str], None] | None = None) -> None:
        with self._lock:
            if self.running:
                return
            binary = self.binary
            if not binary:
                raise CoreError("尚未安装 mihomo 内核")
            if self.settings.tun and not self.net_admin_available():
                raise CoreError(
                    "TUN 模式需要网络管理权限。请在“设置 → 内核”中点击“授权 TUN”，"
                    "或运行 scripts/setup-tun.sh。"
                )
            if progress:
                progress(0, "正在检查上次会话的内核进程…")
            self._cleanup_stale_core()
            self._check_ports()
            config_preview = self.build_config_data(profile_data)
            required = required_geodata(config_preview)
            missing = [name for name in filenames_for(required, config_preview) if not is_ready(name)]
            if missing:
                if progress:
                    progress(0, "首次使用 Geo 规则，正在准备 GeoIP / GeoSite 数据…")
                try:
                    download_geodata(
                        required,
                        proxy_ports=local_proxy_ports(int(self.settings.mixed_port)),
                        progress=lambda percent, text: progress(percent, text) if progress else None,
                        config=config_preview,
                    )
                except GeoDataError as exc:
                    raise CoreError(
                        "缺少 GeoIP / GeoSite 数据，且自动下载失败。\n"
                        f"{exc}\n"
                        "请检查网络后重试；也可以在设置中手动指定可用的本地代理端口。"
                    ) from exc
            if progress:
                progress(100, "正在校验配置…")
            config_path = self.write_runtime_config(profile_data)
            ok, output = self.validate_config(config_path)
            if not ok:
                tail = "\n".join(output.splitlines()[-6:])
                raise CoreError(f"配置校验失败：\n{tail}")
            CORE_LOG.parent.mkdir(parents=True, exist_ok=True)
            try:
                log_handle = open(CORE_LOG, "w", encoding="utf-8", buffering=1)
                log_handle.write(f"===== Cat Meta {VERSION} · {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                log_handle.flush()
                self._intentional_stop = False
                self._process = subprocess.Popen(
                    [str(binary), "-d", str(APP_DATA), "-f", str(config_path)],
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    env=self._env(),
                    start_new_session=True,
                )
                log_handle.close()
            except OSError as exc:
                raise CoreError(f"启动内核失败：{exc}") from exc
            self._started_at = time.time()
            if progress:
                progress(100, "正在启动 mihomo 内核…")
            process = self._process
            self._watch_thread = threading.Thread(
                target=self._watch_process, args=(process,), name="catmeta-core-watch", daemon=True
            )
            self._watch_thread.start()

        api = self.api()
        # 首次使用带有 GEOIP / GEOSITE 规则的配置时，mihomo 会先下载
        # Geo 数据，可能耗时较久；这里给足时间，避免误杀正在下载的进程。
        deadline = time.time() + 100
        while time.time() < deadline:
            if process.poll() is not None:
                self._process = None
                tail = self.log_tail(12)
                raise CoreError("mihomo 启动后立即退出。" + ("\n" + tail if tail else ""))
            try:
                api.version(timeout=1.2)
                log.info("mihomo 已启动，API %s", api.base_url)
                return
            except ApiError:
                time.sleep(0.25)
        self.stop()
        raise CoreError("mihomo API 在 100 秒内未就绪。\n" + self.log_tail(12))

    def _watch_process(self, process: subprocess.Popen[bytes]) -> None:
        code = process.wait()
        with self._lock:
            if self._process is process:
                self._process = None
        if not self._intentional_stop:
            log.warning("mihomo 已退出，代码 %s", code)
            self._notify_exit(code)

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._intentional_stop = True
            self._process = None
        if not process:
            return
        if process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                try:
                    process.terminate()
                except OSError:
                    pass
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    try:
                        process.kill()
                    except OSError:
                        pass
        self._started_at = 0.0

    def restart(self, profile_data: dict[str, Any]) -> None:
        self.stop()
        time.sleep(0.25)
        self.start(profile_data)

    # ---------- 附加功能 ----------
    def log_tail(self, lines: int = 40) -> str:
        try:
            if not CORE_LOG.exists():
                return ""
            data = CORE_LOG.read_text(encoding="utf-8", errors="replace")
            return "\n".join(data.splitlines()[-max(1, lines):])
        except OSError:
            return ""

    def memory_usage(self) -> int:
        process = self._process
        if not process or process.poll() is not None:
            return 0
        try:
            import psutil

            proc = psutil.Process(process.pid)
            total = proc.memory_info().rss
            for child in proc.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except (psutil.Error, OSError):
                    pass
            return total
        except Exception:
            return 0

    def net_admin_available(self) -> bool:
        if os.geteuid() == 0:
            return True
        binary = self.binary
        if not binary:
            return False
        try:
            result = subprocess.run(["getcap", str(binary)], capture_output=True, text=True, timeout=5)
            caps = (result.stdout or "").lower()
            return "cap_net_admin" in caps and "cap_net_raw" in caps
        except (OSError, subprocess.SubprocessError):
            return False

    def grant_tun_permissions(self) -> tuple[bool, str]:
        binary = self.binary
        if not binary:
            return False, "请先下载 mihomo 内核"
        pkexec = shutil.which("pkexec")
        if not pkexec:
            return False, "系统缺少 pkexec，无法请求授权"
        try:
            result = subprocess.run(
                [pkexec, "setcap", "cap_net_admin,cap_net_raw+ep", str(binary)],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"授权失败：{exc}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "已取消或授权失败").strip()
            return False, detail
        return self.net_admin_available(), "TUN 权限已授予"
