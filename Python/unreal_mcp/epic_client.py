"""Minimal MCP client for Epic's native editor MCP server (UE 5.8 ModelContextProtocol plugin).

Epic's server speaks Streamable HTTP on one endpoint (default http://127.0.0.1:8000/mcp): POST JSON-RPC, a
``Mcp-Session-Id`` header after ``initialize``, replies as ``application/json`` (or ``text/event-stream`` when
the request carries a progressToken). It has no auth and only accepts localhost Origins. This client covers what
the gateway needs (initialize, tools/list with pagination, tools/call) with ``requests``, so it works with the
mcp SDK version this project pins.
"""

from __future__ import annotations

import itertools
import json
import threading
from typing import Any

import requests

PROTOCOL_VERSION = "2025-06-18"


class EpicMCPError(RuntimeError):
    pass


class EpicMCPClient:
    def __init__(self, url: str, timeout: float = 30.0, session: requests.Session | None = None):
        self.url = url
        self.timeout = timeout
        self._http = session or requests.Session()
        self._session_id: str | None = None
        self._protocol = PROTOCOL_VERSION
        self._ids = itertools.count(1)
        self._lock = threading.RLock()
        self.server_info: dict[str, Any] = {}

    # -- transport ---------------------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
            h["MCP-Protocol-Version"] = self._protocol
        return h

    @staticmethod
    def _parse_body(resp: requests.Response, want_id: int | None) -> dict[str, Any] | None:
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            found = None
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if not data:
                        continue
                    msg = json.loads(data)
                    if want_id is None or msg.get("id") == want_id:
                        found = msg
            return found
        if not resp.text.strip():
            return None
        return resp.json()

    def _post(self, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any] | None:
        resp = self._http.post(self.url, data=json.dumps(payload), headers=self._headers(),
                               timeout=timeout or self.timeout)
        if resp.status_code in (400, 404) and self._session_id and payload.get("method") != "initialize":
            raise _SessionExpired(resp.text)
        if resp.status_code >= 400:
            raise EpicMCPError(f"HTTP {resp.status_code} from {self.url}: {resp.text[:300]}")
        if payload.get("method") == "initialize":
            self._session_id = resp.headers.get("Mcp-Session-Id") or self._session_id
        return self._parse_body(resp, payload.get("id"))

    # -- protocol ----------------------------------------------------------------------------------------------
    def initialize(self) -> dict[str, Any]:
        with self._lock:
            self._session_id = None
            msg = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": "initialize", "params": {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "unreal-mcp-gateway", "version": "0.3.0"}}})
            if not msg or "result" not in msg:
                raise EpicMCPError(f"initialize failed: {msg}")
            self._protocol = msg["result"].get("protocolVersion", PROTOCOL_VERSION)
            self.server_info = msg["result"].get("serverInfo", {})
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return msg["result"]

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        with self._lock:
            for attempt in range(2):
                if not self._session_id:
                    self.initialize()
                payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}}
                try:
                    msg = self._post(payload, timeout)
                except _SessionExpired:
                    if attempt:
                        raise EpicMCPError("Epic MCP session rejected twice") from None
                    self._session_id = None  # editor restarted: new session
                    continue
                if msg is None:
                    raise EpicMCPError(f"no reply to {method}")
                if "error" in msg:
                    raise EpicMCPError(f"{method}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result")
        raise EpicMCPError("unreachable")

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += result.get("tools", [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                  timeout: float | None = None) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)


class _SessionExpired(Exception):
    pass


def content_text(result: dict[str, Any]) -> str:
    return "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
