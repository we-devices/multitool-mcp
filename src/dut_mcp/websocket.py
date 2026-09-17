"""Small RFC 6455 client used to keep the MCP bridge dependency-free."""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
from urllib.parse import urlsplit


class WebSocketError(RuntimeError):
    pass


class WebSocket:
    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout
        self._socket: socket.socket | None = None

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        if self._socket is not None:
            return
        uri = urlsplit(self.url)
        if uri.scheme not in {"ws", "wss"} or not uri.hostname:
            raise WebSocketError("Device URL must use ws:// or wss://")
        port = uri.port or (443 if uri.scheme == "wss" else 80)
        raw = socket.create_connection((uri.hostname, port), timeout=self.timeout)
        if uri.scheme == "wss":
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=uri.hostname)
        raw.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = uri.path or "/"
        if uri.query:
            path += "?" + uri.query
        host = uri.hostname if uri.port is None else f"{uri.hostname}:{uri.port}"
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        try:
            raw.sendall(request.encode("ascii"))
            response = self._receive_headers(raw)
            status = response.split("\r\n", 1)[0]
            if " 101 " not in status:
                raise WebSocketError(f"WebSocket upgrade failed: {status}")
            headers = {}
            for line in response.split("\r\n")[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    headers[name.strip().lower()] = value.strip()
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
            if headers.get("sec-websocket-accept") != expected:
                raise WebSocketError("WebSocket server returned an invalid accept key")
        except Exception:
            raw.close()
            raise
        self._socket = raw

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is None:
            return
        try:
            self._send_frame(0x8, b"", sock=sock)
        except OSError:
            pass
        finally:
            sock.close()

    def send_text(self, text: str) -> None:
        self.connect()
        self._send_frame(0x1, text.encode("utf-8"))

    def receive_text(self, timeout: float | None = None) -> str:
        if self._socket is None:
            raise WebSocketError("WebSocket is not connected")
        previous_timeout = self._socket.gettimeout()
        if timeout is not None:
            self._socket.settimeout(timeout)
        try:
            fragments = bytearray()
            while True:
                final, opcode, payload = self._receive_frame()
                if opcode == 0x8:
                    self.close()
                    raise WebSocketError("Device closed the WebSocket connection")
                if opcode == 0x9:
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode not in {0x0, 0x1}:
                    raise WebSocketError(f"Unsupported WebSocket opcode: {opcode}")
                fragments.extend(payload)
                if final:
                    return fragments.decode("utf-8")
        finally:
            if self._socket is not None:
                self._socket.settimeout(previous_timeout)

    @staticmethod
    def _receive_headers(sock: socket.socket) -> str:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketError("Connection closed during WebSocket upgrade")
            data.extend(chunk)
            if len(data) > 65536:
                raise WebSocketError("WebSocket upgrade headers are too large")
        return bytes(data).split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")

    def _receive_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        final, opcode, masked = bool(first & 0x80), first & 0x0F, bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        return final, opcode, payload

    def _read_exact(self, length: int) -> bytes:
        assert self._socket is not None
        data = bytearray()
        while len(data) < length:
            chunk = self._socket.recv(length - len(data))
            if not chunk:
                raise WebSocketError("Device closed the WebSocket connection")
            data.extend(chunk)
        return bytes(data)

    def _send_frame(self, opcode: int, payload: bytes, sock: socket.socket | None = None) -> None:
        target = sock or self._socket
        if target is None:
            raise WebSocketError("WebSocket is not connected")
        mask, length = os.urandom(4), len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        target.sendall(bytes(header) + mask + masked)
