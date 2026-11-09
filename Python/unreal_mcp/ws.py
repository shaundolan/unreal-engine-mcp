"""A minimal RFC 6455 WebSocket server and client on asyncio streams, no dependencies.

Just enough for the agent runner on localhost: text and binary messages, fragmentation, ping/pong
and close. No extensions (permessage-deflate is never negotiated), so frames are never compressed.
The server echoes the first requested subprotocol, which some clients (libwebsockets in Unreal)
require when they send one.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import struct
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 64 * 1024 * 1024

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class ConnectionClosed(Exception):
    pass


def _xor(data: bytes, key: bytes) -> bytes:
    n = len(data)
    if not n:
        return data
    k = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(k, "big")).to_bytes(n, "big")


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


class WebSocket:
    """One open connection. recv() returns str (text) or bytes (binary); raises ConnectionClosed."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, mask: bool,
                 path: str = "/", headers: dict[str, str] | None = None):
        self.reader, self.writer, self.mask = reader, writer, mask
        self.path = path
        self.headers = headers or {}
        self.closed = False
        self._send_lock = asyncio.Lock()

    @property
    def query(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    async def _frame(self, opcode: int, payload: bytes) -> None:
        head = bytearray([0x80 | opcode])
        n = len(payload)
        mbit = 0x80 if self.mask else 0
        if n < 126:
            head.append(mbit | n)
        elif n < 1 << 16:
            head.append(mbit | 126)
            head += struct.pack(">H", n)
        else:
            head.append(mbit | 127)
            head += struct.pack(">Q", n)
        if self.mask:
            key = os.urandom(4)
            head += key
            payload = _xor(payload, key)
        async with self._send_lock:
            self.writer.write(bytes(head) + payload)
            await self.writer.drain()

    async def send(self, data: str | bytes) -> None:
        if self.closed:
            raise ConnectionClosed("send on a closed websocket")
        try:
            if isinstance(data, str):
                await self._frame(OP_TEXT, data.encode("utf-8"))
            else:
                await self._frame(OP_BINARY, data)
        except (ConnectionError, OSError) as e:
            self.closed = True
            raise ConnectionClosed(str(e)) from e

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            b0, b1 = await self.reader.readexactly(2)
            n = b1 & 0x7F
            if n == 126:
                (n,) = struct.unpack(">H", await self.reader.readexactly(2))
            elif n == 127:
                (n,) = struct.unpack(">Q", await self.reader.readexactly(8))
            if n > MAX_MESSAGE:
                raise ConnectionClosed(f"frame too large ({n} bytes)")
            key = await self.reader.readexactly(4) if b1 & 0x80 else None
            payload = await self.reader.readexactly(n) if n else b""
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            self.closed = True
            raise ConnectionClosed(str(e)) from e
        if key:
            payload = _xor(payload, key)
        return bool(b0 & 0x80), b0 & 0x0F, payload

    async def recv(self) -> str | bytes:
        parts: list[bytes] = []
        kind = None
        while True:
            fin, op, payload = await self._read_frame()
            if op == OP_PING:
                await self._frame(OP_PONG, payload)
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                if not self.closed:
                    self.closed = True
                    try:
                        await self._frame(OP_CLOSE, payload[:2])
                    except (ConnectionError, OSError):
                        pass
                raise ConnectionClosed("closed by peer")
            if op in (OP_TEXT, OP_BINARY):
                kind, parts = op, [payload]
            elif op == OP_CONT and kind is not None:
                parts.append(payload)
            else:
                raise ConnectionClosed(f"protocol error: opcode {op}")
            if fin:
                data = b"".join(parts)
                return data.decode("utf-8") if kind == OP_TEXT else data

    async def close(self, code: int = 1000) -> None:
        if not self.closed:
            self.closed = True
            try:
                await self._frame(OP_CLOSE, struct.pack(">H", code))
            except (ConnectionError, OSError):
                pass
        try:
            self.writer.close()
        except (ConnectionError, OSError):
            pass


async def _read_headers(reader: asyncio.StreamReader) -> tuple[str, dict[str, str]]:
    raw = await reader.readuntil(b"\r\n\r\n")
    lines = raw.decode("latin-1").split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return lines[0], headers


Handler = Callable[[WebSocket], Awaitable[None]]
Authorize = Callable[[str, dict[str, str]], str | None]


async def serve(handler: Handler, host: str, port: int, authorize: Authorize | None = None) -> asyncio.Server:
    """Start a server. authorize(path, headers) returns None to accept, or a reason to refuse with 403.
    Plain GET /health (no upgrade) answers 200 "ok" so launchers can probe readiness."""

    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line, headers = await asyncio.wait_for(_read_headers(reader), 10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        parts = request_line.split(" ")
        path = parts[1] if len(parts) > 1 else "/"

        def respond(status: str, body: str = "") -> None:
            writer.write((f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
                          f"Connection: close\r\n\r\n{body}").encode())

        if headers.get("upgrade", "").lower() != "websocket" or "sec-websocket-key" not in headers:
            if urlsplit(path).path == "/health":
                respond("200 OK", "ok")
            else:
                respond("426 Upgrade Required", "websocket only")
            await writer.drain()
            writer.close()
            return
        reason = authorize(path, headers) if authorize else None
        if reason:
            respond("403 Forbidden", reason)
            await writer.drain()
            writer.close()
            return
        resp = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key(headers['sec-websocket-key'])}\r\n")
        proto = headers.get("sec-websocket-protocol")
        if proto:
            resp += f"Sec-WebSocket-Protocol: {proto.split(',')[0].strip()}\r\n"
        writer.write((resp + "\r\n").encode("latin-1"))
        await writer.drain()
        ws = WebSocket(reader, writer, mask=False, path=path, headers=headers)
        try:
            await handler(ws)
        except ConnectionClosed:
            pass
        finally:
            await ws.close()

    return await asyncio.start_server(on_client, host, port, limit=1 << 20)


async def connect(url: str, headers: dict[str, str] | None = None) -> WebSocket:
    """Client side (tests and the CLI): ws://host:port/path?query."""
    u = urlsplit(url)
    reader, writer = await asyncio.open_connection(u.hostname, u.port or 80)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    writer.write((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                  f"{extra}\r\n").encode("latin-1"))
    await writer.drain()
    status, resp_headers = await _read_headers(reader)
    if " 101 " not in status + " ":
        writer.close()
        raise ConnectionClosed(f"handshake refused: {status}")
    if resp_headers.get("sec-websocket-accept") != accept_key(key):
        writer.close()
        raise ConnectionClosed("bad Sec-WebSocket-Accept")
    return WebSocket(reader, writer, mask=True, path=path, headers=resp_headers)
