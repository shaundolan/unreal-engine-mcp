"""A fake UnrealMCP editor plugin for tests.

Speaks both wire protocols exactly like MCPServerRunnable.cpp: framed (4-byte BE length
prefix, persistent connection) when the first byte is not '{', legacy (bare JSON, one
request per connection) when it is.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from unreal_mcp.protocol import HEADER, read_frame, recv_exact

Handler = Callable[[dict[str, Any]], Any]


class FakeUnreal:
    def __init__(self, token: str | None = None):
        self.token = token
        self.handlers: dict[str, Handler] = {"ping": lambda p: {"message": "pong"}}
        self.requests: list[dict[str, Any]] = []
        self.connections = 0
        self.delay: dict[str, float] = {}
        self.drop_next = False  # close the socket instead of replying, once
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ control
    def on(self, command: str, handler: Handler) -> None:
        self.handlers[command] = handler

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------ server
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _reply_for(self, req: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(req)
        if self.token and req.get("token") != self.token:
            return {"status": "error", "error": "unauthorized: missing or bad token"}
        cmd = req.get("type")
        time.sleep(self.delay.get(cmd, 0))
        handler = self.handlers.get(cmd)
        if handler is None:
            return {"status": "error", "error": f"Unknown command: {cmd}"}
        try:
            result = handler(req.get("params") or {})
        except Exception as e:
            return {"status": "error", "error": str(e)}
        if isinstance(result, dict) and result.get("success") is False:
            return {"status": "error", "error": result.get("error", "failed")}
        return {"status": "success", "result": result}

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            try:
                first = conn.recv(1, socket.MSG_PEEK)
                if not first:
                    return
                if first == b"{":
                    self._serve_legacy(conn)
                else:
                    self._serve_framed(conn)
            except (OSError, ConnectionError):
                return

    def _serve_framed(self, conn: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                req = read_frame(conn)
            except (ConnectionError, OSError):
                return
            if self.drop_next:
                self.drop_next = False
                return
            reply = self._reply_for(req)
            reply["id"] = req.get("id")
            payload = json.dumps(reply).encode("utf-8")
            conn.sendall(HEADER.pack(len(payload)) + payload)

    def _serve_legacy(self, conn: socket.socket) -> None:
        data = b""
        while True:
            data += conn.recv(65536)
            try:
                req = json.loads(data.decode("utf-8"))
                break
            except json.JSONDecodeError:
                continue
        conn.sendall(json.dumps(self._reply_for(req)).encode("utf-8"))


__all__ = ["FakeUnreal", "recv_exact"]
