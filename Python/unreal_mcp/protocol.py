"""Wire protocol shared with the UnrealMCP C++ plugin.

Framed protocol (plugin >= 0.2):
    frame   := length:uint32 big-endian  ||  payload:UTF-8 JSON (length bytes)
    request := {"id": int, "type": str, "params": object, "token"?: str}
    reply   := {"id": int, "status": "success"|"error", "result"?: object, "error"?: str}

The connection is persistent; requests on one connection are answered in order.

Legacy protocol (original plugin): a bare JSON object per TCP connection, no length
prefix. The plugin still accepts it (it detects a leading '{'), so old clients keep working.
"""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

HEADER = struct.Struct(">I")
MAX_FRAME_BYTES = 256 * 1024 * 1024  # must match MCP_MAX_FRAME_BYTES in MCPServerRunnable.cpp


class ProtocolError(Exception):
    """Malformed or oversized frame."""


def encode_frame(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame of {len(payload)} bytes exceeds limit {MAX_FRAME_BYTES}")
    return HEADER.pack(len(payload)) + payload


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise ConnectionError(f"connection closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def read_frame(sock: socket.socket) -> dict[str, Any]:
    (length,) = HEADER.unpack(recv_exact(sock, HEADER.size))
    if length > MAX_FRAME_BYTES:
        raise ProtocolError(f"peer announced {length}-byte frame, limit {MAX_FRAME_BYTES}")
    payload = recv_exact(sock, length)
    try:
        msg = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolError(f"invalid JSON payload: {e}") from e
    if not isinstance(msg, dict):
        raise ProtocolError("frame payload is not a JSON object")
    return msg
