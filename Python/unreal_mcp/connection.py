"""Client connection to the UnrealMCP editor plugin.

Keeps the public surface the legacy tools and helpers rely on:
``UnrealConnection.send_command(command, params) -> dict`` and the module-level
``get_unreal_connection()`` / ``reset_unreal_connection()``.

Retry policy: a command is retried only when it provably never reached the editor
(connect failed, or the send failed before completing). Once a request is on the wire a
failure is reported, not retried, so mutating commands are never applied twice.
"""

from __future__ import annotations

import itertools
import json
import logging
import socket
import threading
import time
from typing import Any

from .config import Settings
from .protocol import ProtocolError, encode_frame, read_frame

logger = logging.getLogger("unreal_mcp.connection")

LARGE_OPERATION_COMMANDS = frozenset({
    "get_available_materials", "create_town", "create_castle_fortress", "construct_mansion",
    "create_suspension_bridge", "create_aqueduct", "create_maze",
})


class UnrealConnection:
    MAX_CONNECT_ATTEMPTS = 4
    BASE_RETRY_DELAY = 0.5
    MAX_RETRY_DELAY = 5.0
    CONNECT_TIMEOUT = 10.0
    DEFAULT_TIMEOUT = 30.0
    LARGE_OP_TIMEOUT = 300.0

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.socket: socket.socket | None = None
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self._last_error: str | None = None

    # ------------------------------------------------------------------ public API
    @property
    def connected(self) -> bool:
        return self.socket is not None

    def send_command(self, command: str, params: dict[str, Any] | None = None,
                     timeout: float | None = None) -> dict[str, Any]:
        """Send one command and return the normalized reply.

        Never raises for editor-side or transport failures; returns
        ``{"status": "error", "error": ...}`` instead, matching the legacy contract.
        """
        params = dict(params or {})
        if timeout is None:
            timeout = self._timeout_for(command, params)
        try:
            with self._lock:
                if self.settings.protocol == "legacy":
                    raw = self._roundtrip_legacy(command, params, timeout)
                else:
                    raw = self._roundtrip_framed(command, params, timeout)
        except Exception as e:  # transport failure: report, never crash the tool
            logger.warning("command %s failed: %s", command, e)
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}
        return normalize_response(raw)

    def disconnect(self) -> None:
        with self._lock:
            self._close()

    # ------------------------------------------------------------------ internals
    def _timeout_for(self, command: str, params: dict[str, Any]) -> float:
        # Commands carrying their own editor-side timeout get that plus slack.
        if isinstance(params.get("timeout_s"), (int, float)):
            return float(params["timeout_s"]) + 10.0
        return self.LARGE_OP_TIMEOUT if command in LARGE_OPERATION_COMMANDS else self.DEFAULT_TIMEOUT

    def _open_socket(self) -> socket.socket:
        last: Exception | None = None
        for attempt in range(self.MAX_CONNECT_ATTEMPTS):
            try:
                s = socket.create_connection((self.settings.host, self.settings.port),
                                             timeout=self.CONNECT_TIMEOUT)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                self._last_error = None
                return s
            except OSError as e:
                last = e
                self._last_error = str(e)
                if attempt + 1 < self.MAX_CONNECT_ATTEMPTS:
                    time.sleep(min(self.BASE_RETRY_DELAY * (2 ** attempt), self.MAX_RETRY_DELAY))
        raise ConnectionError(
            f"cannot reach Unreal at {self.settings.host}:{self.settings.port} "
            f"(is the editor running with the UnrealMCP plugin enabled?): {last}")

    def _close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None

    def _roundtrip_framed(self, command: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        req_id = next(self._ids)
        message: dict[str, Any] = {"id": req_id, "type": command, "params": params}
        if self.settings.token:
            message["token"] = self.settings.token
        frame = encode_frame(message)

        # A stale persistent socket fails on send; that is safe to retry once on a fresh one.
        for attempt in (0, 1):
            if self.socket is None:
                self.socket = self._open_socket()
            try:
                self.socket.settimeout(timeout)
                self.socket.sendall(frame)
                break
            except OSError:
                self._close()
                if attempt == 1:
                    raise
        try:
            while True:
                reply = read_frame(self.socket)
                # Skip a late reply to an earlier, timed-out request on this socket.
                if reply.get("id") in (None, req_id):
                    return reply
                logger.info("discarding stale reply id=%s (want %s)", reply.get("id"), req_id)
        except (OSError, ProtocolError, ConnectionError):
            # Request reached the wire: do not retry; drop the socket so the next call reconnects.
            self._close()
            raise

    def _roundtrip_legacy(self, command: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        sock = self._open_socket()
        try:
            sock.settimeout(timeout)
            sock.sendall(json.dumps({"type": command, "params": params}).encode("utf-8"))
            data = bytearray()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data.extend(chunk)
                try:
                    return json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue  # legacy replies have no length; keep reading until it parses
            return json.loads(data.decode("utf-8"))
        finally:
            sock.close()


def normalize_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"status": "error", "error": f"unexpected reply type {type(response).__name__}"}
    if response.get("status") == "error":
        response.setdefault("error", response.get("message", "Unknown error"))
    elif response.get("success") is False:
        return {"status": "error", "error": response.get("error") or response.get("message", "Unknown error")}
    return response


_connection: UnrealConnection | None = None
_connection_lock = threading.Lock()


def get_unreal_connection() -> UnrealConnection:
    global _connection
    with _connection_lock:
        if _connection is None:
            _connection = UnrealConnection()
        return _connection


def reset_unreal_connection() -> None:
    global _connection
    with _connection_lock:
        if _connection is not None:
            _connection.disconnect()
        _connection = None
