import socket
import threading

import pytest

from unreal_mcp.config import Settings
from unreal_mcp.connection import UnrealConnection
from unreal_mcp.protocol import MAX_FRAME_BYTES, ProtocolError, encode_frame, read_frame


def test_ping_roundtrip(conn):
    assert conn.send_command("ping") == {"status": "success", "result": {"message": "pong"}, "id": 1}


def test_persistent_connection_reused(fake, conn):
    for _ in range(5):
        assert conn.send_command("ping")["status"] == "success"
    assert fake.connections == 1


def test_large_payload_both_directions(fake, conn):
    blob = "x" * (3 * 1024 * 1024)  # 3 MB: the old 8 KB single-recv plugin could not take this
    fake.on("echo", lambda p: {"data": p["data"]})
    reply = conn.send_command("echo", {"data": blob})
    assert reply["status"] == "success"
    assert reply["result"]["data"] == blob


def test_unicode_payload(fake, conn):
    fake.on("echo", lambda p: p)
    reply = conn.send_command("echo", {"s": "Dubhlán ✓ 日本"})
    assert reply["result"]["s"] == "Dubhlán ✓ 日本"


def test_editor_error_normalized(fake, conn):
    reply = conn.send_command("does_not_exist")
    assert reply["status"] == "error"
    assert "Unknown command" in reply["error"]


def test_success_false_normalized(fake, conn):
    fake.on("bad", lambda p: {"success": False, "error": "nope"})
    reply = conn.send_command("bad")
    assert (reply["status"], reply["error"]) == ("error", "nope")


def test_unreachable_returns_error_not_raise():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = UnrealConnection(Settings(port=port))
    c.MAX_CONNECT_ATTEMPTS = 1
    reply = c.send_command("ping")
    assert reply["status"] == "error"
    assert "cannot reach Unreal" in reply["error"]


def test_timeout_reports_error_and_recovers(fake, conn):
    fake.on("slow", lambda p: {"ok": True})
    fake.delay["slow"] = 1.0
    reply = conn.send_command("slow", timeout=0.2)
    assert reply["status"] == "error"
    assert "timed out" in reply["error"].lower() or "timeout" in reply["error"].lower()
    # Next command works on a fresh socket.
    assert conn.send_command("ping")["status"] == "success"


def test_dropped_reply_is_not_retried(fake, conn):
    calls = []
    fake.on("mutate", lambda p: calls.append(1) or {"ok": True})
    fake.drop_next = True
    reply = conn.send_command("mutate")
    assert reply["status"] == "error"
    assert calls == []  # the fake dropped before handling; crucially we did not resend
    assert len([r for r in fake.requests if r["type"] == "mutate"]) == 0
    assert conn.send_command("mutate")["status"] == "success"
    assert calls == [1]


def test_reconnect_after_server_side_close(fake, conn):
    assert conn.send_command("ping")["status"] == "success"
    conn.socket.close()  # simulate a stale socket
    conn.socket = conn.socket  # keep reference to closed socket
    assert conn.send_command("ping")["status"] == "success"


def test_token_sent_and_enforced(fake):
    fake.token = "s3cret"
    bad = UnrealConnection(Settings(port=fake.port))
    assert "unauthorized" in bad.send_command("ping")["error"]
    good = UnrealConnection(Settings(port=fake.port, token="s3cret"))
    assert good.send_command("ping")["status"] == "success"
    assert fake.requests[-1]["token"] == "s3cret"


def test_legacy_protocol(fake):
    c = UnrealConnection(Settings(port=fake.port, protocol="legacy"))
    for _ in range(3):
        assert c.send_command("ping")["result"] == {"message": "pong"}
    assert fake.connections == 3  # legacy: one connection per command


def test_concurrent_callers_serialized(fake, conn):
    fake.on("echo", lambda p: p)
    results = []

    def worker(i):
        results.append(conn.send_command("echo", {"i": i})["result"]["i"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == list(range(20))


def test_timeout_s_param_extends_client_timeout(conn):
    assert conn._timeout_for("execute_python", {"timeout_s": 100}) == 110
    assert conn._timeout_for("create_town", {}) == conn.LARGE_OP_TIMEOUT
    assert conn._timeout_for("ping", {}) == conn.DEFAULT_TIMEOUT


def test_oversized_frame_rejected():
    class Sock:
        def __init__(self, data):
            self.data = data

        def recv(self, n):
            out, self.data = self.data[:n], self.data[n:]
            return out

    with pytest.raises(ProtocolError):
        read_frame(Sock((MAX_FRAME_BYTES + 1).to_bytes(4, "big")))
    assert encode_frame({"a": 1})[:4] == (7).to_bytes(4, "big")


def test_settings_from_env():
    s = Settings.from_env({"UNREAL_MCP_PORT": "6000", "UNREAL_MCP_TOKEN": "t", "UNREAL_MCP_PROTOCOL": "LEGACY"})
    assert (s.port, s.token, s.protocol) == (6000, "t", "legacy")
    with pytest.raises(ValueError):
        Settings.from_env({"UNREAL_MCP_PROTOCOL": "websocket"})
