"""极简 RFC 6455 WebSocket 客户端，用于 mihomo 的流量与日志推送。

只依赖标准库，避免额外安装 websocket-client；支持文本帧、分片、ping/pong
以及断线自动重连。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import socket
import ssl
import struct
import threading
import time
from typing import Callable
from urllib.parse import urlparse

log = logging.getLogger("catmeta.ws")

MessageCallback = Callable[[str], None]
StateCallback = Callable[[bool], None]


class WebSocketClient:
    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        on_message: MessageCallback,
        on_state: StateCallback | None = None,
        name: str = "ws",
    ):
        self.url = url
        self.headers = headers or {}
        self.on_message = on_message
        self.on_state = on_state
        self.name = name
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending = b""

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"catmeta-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            sock = self._sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._set_connected(False)

    def _set_connected(self, value: bool) -> None:
        if value:
            self._connected.set()
        else:
            self._connected.clear()
        if self.on_state:
            try:
                self.on_state(value)
            except Exception:
                log.exception("WebSocket 状态回调失败")

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._connect()
                backoff = 1.0
                self._read_loop()
            except Exception as exc:
                if not self._stop.is_set():
                    log.debug("%s 连接中断：%s", self.name, exc)
            finally:
                self._set_connected(False)
                with self._lock:
                    sock, self._sock = self._sock, None
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if self._stop.is_set():
                break
            # 分段等待，保证停止及时生效。
            waited = 0.0
            while waited < backoff and not self._stop.is_set():
                time.sleep(min(0.2, backoff - waited))
                waited += 0.2
            backoff = min(backoff * 2, 15.0)

    def _connect(self) -> None:
        parsed = urlparse(self.url)
        secure = parsed.scheme == "wss"
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if secure else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        sock = socket.create_connection((host, port), timeout=8)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if secure:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        headers = {
            "Host": f"{host}:{port}",
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": key,
            "Sec-WebSocket-Version": "13",
            "User-Agent": "CatMeta/1.0",
        }
        headers.update(self.headers)
        request = f"GET {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        sock.sendall(request.encode("latin-1"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket 握手连接被关闭")
            response += chunk
            if len(response) > 65536:
                raise ConnectionError("WebSocket 握手响应过大")
        head, _, remainder = response.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 101 " not in status_line:
            raise ConnectionError(f"WebSocket 握手失败：{status_line}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        if expected.encode() not in head:
            raise ConnectionError("WebSocket 握手校验失败")
        if remainder:
            # 正常 mihomo 不会和握手同包发送数据；保留缓冲以正确处理。
            self._pending = remainder
        else:
            self._pending = b""
        with self._lock:
            self._sock = sock
        self._connected.set()
        if self.on_state:
            try:
                self.on_state(True)
            except Exception:
                log.exception("WebSocket 状态回调失败")

    def _recv_exact(self, count: int) -> bytes:
        data = b""
        if self._pending:
            data, self._pending = self._pending[:count], self._pending[count:]
        while len(data) < count:
            with self._lock:
                sock = self._sock
            if sock is None:
                raise ConnectionError("套接字已关闭")
            try:
                chunk = sock.recv(count - len(data))
            except socket.timeout:
                if self._stop.is_set():
                    raise ConnectionError("已停止")
                continue
            if not chunk:
                raise ConnectionError("服务器关闭连接")
            data += chunk
        return data

    def _read_loop(self) -> None:
        fragments = []
        fragment_opcode = 0
        while not self._stop.is_set():
            header = self._recv_exact(2)
            first, second = header[0], header[1]
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            if length > 16 * 1024 * 1024:
                raise ConnectionError("WebSocket 帧过大")
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))

            if opcode == 0x8:  # close
                return
            if opcode == 0x9:  # ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                fragments = [payload]
                fragment_opcode = opcode
            elif opcode == 0x0:
                fragments.append(payload)
            else:
                continue
            if not fin:
                continue
            data = b"".join(fragments)
            fragments = []
            if fragment_opcode == 0x1:
                try:
                    message = data.decode("utf-8", errors="replace")
                except Exception:
                    continue
                try:
                    self.on_message(message)
                except Exception:
                    log.exception("WebSocket 消息回调失败")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        with self._lock:
            sock = self._sock
        if not sock:
            return
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        try:
            sock.sendall(header + mask + masked)
        except OSError:
            pass
