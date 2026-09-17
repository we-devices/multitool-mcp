"""Client for the JSON-over-WebSocket protocol implemented by WE Multitool."""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .websocket import WebSocket, WebSocketError


class DeviceError(RuntimeError):
    pass


Message = dict[str, Any]
Matcher = Callable[[Message], bool]


class DeviceClient:
    """Serializes commands because the firmware protocol has no request IDs."""

    def __init__(self, url: str, timeout: float = 5.0, transport: WebSocket | None = None) -> None:
        self.url, self.timeout = url, timeout
        self._ws = transport or WebSocket(url, timeout)
        self._lock = threading.RLock()
        self._pending: deque[Message] = deque()

    @property
    def connected(self) -> bool:
        return self._ws.connected

    def close(self) -> None:
        with self._lock:
            self._ws.close()
            self._pending.clear()

    def request(self, command: Message, matcher: Matcher, timeout: float | None = None) -> Message:
        with self._lock:
            try:
                self._ws.send_text(json.dumps(command, separators=(",", ":")))
                deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DeviceError(f"Timed out waiting for response to {command['type']}")
                    message = self._receive(remaining)
                    if matcher(message):
                        return message
                    self._pending.append(message)
            except (OSError, socket.timeout, WebSocketError) as exc:
                self._ws.close()
                raise DeviceError(f"Device communication failed: {exc}") from exc

    def send(self, command: Message) -> None:
        with self._lock:
            try:
                self._ws.send_text(json.dumps(command, separators=(",", ":")))
            except (OSError, socket.timeout, WebSocketError) as exc:
                self._ws.close()
                raise DeviceError(f"Device communication failed: {exc}") from exc

    def read_uart(self, timeout: float, max_bytes: int) -> bytes:
        with self._lock:
            output, deadline = bytearray(), time.monotonic() + timeout
            while len(output) < max_bytes:
                message = self._take_pending(self._is_uart_data)
                if message is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        if not self._ws.connected:
                            self._ws.connect()
                        message = self._receive(remaining)
                    except socket.timeout:
                        break
                    except (OSError, WebSocketError) as exc:
                        self._ws.close()
                        raise DeviceError(f"Device communication failed: {exc}") from exc
                    if not self._is_uart_data(message):
                        self._pending.append(message)
                        continue
                try:
                    output.extend(base64.b64decode(message["data"], validate=True))
                except (ValueError, TypeError) as exc:
                    raise DeviceError("Device returned invalid base64 UART data") from exc
            return bytes(output[:max_bytes])

    def _receive(self, timeout: float) -> Message:
        raw = self._ws.receive_text(timeout)
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeviceError(f"Device returned invalid JSON: {raw[:200]}") from exc
        if not isinstance(message, dict):
            raise DeviceError("Device response is not a JSON object")
        return message

    def _take_pending(self, matcher: Matcher) -> Message | None:
        for index, message in enumerate(self._pending):
            if matcher(message):
                del self._pending[index]
                return message
        return None

    @staticmethod
    def _is_uart_data(message: Message) -> bool:
        return message.get("type") == "uart_response" and "error" not in message
