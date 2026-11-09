"""Agent runner: WebSocket transport, safety rules, the tool loop with a scripted (mock) provider against the
fake editor, and each real provider's wire handling with fakes (no model is ever called)."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

from unreal_mcp import ws
from unreal_mcp.agent_providers import (
    AnthropicProvider,
    ClaudeCodeProvider,
    MockProvider,
    OpenAICompatProvider,
    ToolCall,
    ToolOutcome,
    ToolSpec,
)
from unreal_mcp.agent_runner import Runner, Session, format_context, load_specialists, parse_agent_md
from unreal_mcp.agent_safety import classify
from unreal_mcp.config import Settings
from unreal_mcp.connection import UnrealConnection

REPO = Path(__file__).resolve().parents[2]


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------------------------------- fake editor
def marker_of(code: str) -> str:
    return json.loads(code.split("print(")[-1].split(" + _mcp_line")[0])


class Editor:
    """Fake editor state behind FakeUnreal: execute_python emulates scene_compose spawns; agent_turn counts."""

    def __init__(self, fake):
        self.actors: list[str] = []
        self.turns: list[tuple[str, int]] = []
        self.scripts: list[str] = []
        fake.on("execute_python", self.execute_python)
        fake.on("agent_turn", self.agent_turn)
        fake.on("list_commands", lambda p: {"commands": ["ping", "execute_python"], "plugin_version": "0.4.0"})

    def execute_python(self, p):
        code = p["code"]
        self.scripts.append(code)
        result = {"ok": True, "result": {"note": "ran"}, "stdout": ""}
        if "ARGS = " in code or "ARGS=" in code or "operations" in code:
            # scene_compose renders ARGS into the script; pull the spawn labels back out
            for label in ("Agent_Cube", "Doomed"):
                if label in code:
                    self.actors.append(label)
                    result["result"] = {"succeeded": 1, "failed": 0, "results": [{"ok": True, "label": label}]}
        line = marker_of(code) + json.dumps(result)
        return {"log": [{"type": "Info", "output": line}]}

    def agent_turn(self, p):
        self.turns.append((p["action"], p["turn"]))
        return {"turn": p["turn"], "steps": 1 if p["action"] == "end" else 0}


@pytest.fixture
def editor(fake):
    return Editor(fake)


@pytest.fixture
def runner(fake, tmp_path):
    conn = UnrealConnection(Settings(port=fake.port))
    conn.MAX_CONNECT_ATTEMPTS = 1
    r = Runner(Settings(port=fake.port), port=0, token="tok", default_provider="mock", conn=conn,
               provider_options={"mock": {}}, agents_dir=REPO / "agents")
    yield r
    conn.disconnect()


SPAWN_SCRIPT = [
    {"text": "Looking at the editor.", "tool_calls": [{"name": "unreal_status", "args": {}}]},
    {"text": "Spawning a cube.", "tool_calls": [{"name": "scene_compose", "args": {"operations": [
        {"op": "spawn", "asset": "/Engine/BasicShapes/Cube.Cube", "label": "Agent_Cube", "location": [0, 0, 100]}]}}]},
    {"text": "Done: Agent_Cube is in the level."},
]


async def collect_turn(session: Session, events: list, msg: dict) -> list[dict]:
    await session.handle(msg)
    await session.task
    return events


def make_session(runner) -> tuple[Session, list[dict]]:
    events: list[dict] = []

    async def send(e):
        events.append(e)

    return Session(runner, send), events


# ------------------------------------------------------------------------------------------------ tests
def test_mock_conversation_creates_actor(runner, editor):
    async def go():
        session, events = make_session(runner)
        await collect_turn(session, events, {"type": "user_message", "text": "add a cube", "provider": "mock",
                                             "mock_script": SPAWN_SCRIPT,
                                             "context": {"level": "/Game/Maps/Arena", "selected_actors": []}})
        return session, events

    session, events = run(go())
    assert editor.actors == ["Agent_Cube"]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "turn_start" and kinds[-1] == "status" and events[-1]["state"] == "done"
    calls = [e for e in events if e["type"] == "tool_call"]
    results = [e for e in events if e["type"] == "tool_result"]
    assert [c["name"] for c in calls] == ["unreal_status", "scene_compose"]
    assert all(r["ok"] for r in results), results
    assert '"succeeded": 1' in results[1]["result"]
    end = next(e for e in events if e["type"] == "turn_end")
    assert end["tool_calls"] == 2 and end["undo_steps"] == 1 and end["stop_reason"] == "end_turn"
    assert editor.turns == [("begin", 1), ("end", 1)]
    # the provider saw the editor context and got both tool results back
    prov = session.provider
    assert isinstance(prov, MockProvider)
    assert "<editor_context>" in prov.user_messages[0] and "/Game/Maps/Arena" in prov.user_messages[0]
    assert [o.call.name for o in prov.results] == ["unreal_status", "scene_compose"]
    texts = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "Spawning a cube." in texts and "Done: Agent_Cube" in texts


def test_destructive_call_needs_confirmation(runner, editor):
    script = [{"text": "Deleting.", "tool_calls": [{"name": "asset_manage", "args": {
        "operations": [{"op": "delete", "path": "/Game/Old/SM_Rock"}]}}]}, {"text": "ok"}]

    async def go(allow: bool):
        session, events = make_session(runner)

        async def answer():
            while not any(e["type"] == "confirm_request" for e in events):
                await asyncio.sleep(0.01)
            req = next(e for e in events if e["type"] == "confirm_request")
            await session.handle({"type": "confirm", "id": req["id"], "allow": allow})

        t = asyncio.create_task(answer())
        await collect_turn(session, events, {"type": "user_message", "text": "x", "mock_script": script})
        await t
        return events

    denied = run(go(False))
    req = next(e for e in denied if e["type"] == "confirm_request")
    assert req["tool"] == "asset_manage" and "not undoable" in req["reason"]
    res = next(e for e in denied if e["type"] == "tool_result")
    assert res["ok"] is False and res.get("denied") and editor.scripts == []
    allowed = run(go(True))
    assert any(e["type"] == "tool_result" and not e.get("denied") for e in allowed)
    assert len(editor.scripts) == 1  # asset_manage ran its editor script once


def test_confirm_mode_off_skips_prompts(runner, editor):
    runner.confirm_mode = "off"
    script = [{"tool_calls": [{"name": "asset_manage", "args": {"operations": [{"op": "delete", "path": "/Game/A"}]}}]},
              {"text": "ok"}]
    session, events = make_session(runner)
    run(collect_turn(session, events, {"type": "user_message", "text": "x", "mock_script": script}))
    assert not any(e["type"] == "confirm_request" for e in events)


def test_stop_cancels_turn_and_closes_tool_calls(runner, editor):
    slow = [{"text": "thinking", "tool_calls": [{"name": "unreal_status", "args": {}}]}] * 50
    runner.provider_options["mock"] = {"delay": 0.05}

    async def go():
        session, events = make_session(runner)
        await session.handle({"type": "user_message", "text": "loop", "mock_script": slow})
        await asyncio.sleep(0.3)
        await session.handle({"type": "stop"})
        return session, events

    session, events = run(go())
    end = next(e for e in events if e["type"] == "turn_end")
    assert end["stop_reason"] == "stopped" and end["tool_calls"] < 50
    assert any(e["type"] == "notice" and e["text"] == "Stopped." for e in events)


def test_unknown_tool_and_bad_args_are_reported_not_raised(runner, editor):
    script = [{"tool_calls": [{"name": "no_such_tool", "args": {}}, {"name": "execute_python", "args": {}}]},
              {"text": "ok"}]
    session, events = make_session(runner)
    run(collect_turn(session, events, {"type": "user_message", "text": "x", "mock_script": script}))
    res = [e for e in events if e["type"] == "tool_result"]
    assert "unknown tool" in res[0]["result"] and "missing required argument(s): code" in res[1]["result"]


def test_websocket_end_to_end(runner, editor):
    async def go():
        server = await runner.start()
        port = runner.port
        url = f"ws://127.0.0.1:{port}/?token=tok"
        with pytest.raises(ws.ConnectionClosed):
            await ws.connect(f"ws://127.0.0.1:{port}/?token=wrong")
        with pytest.raises(ws.ConnectionClosed):
            await ws.connect(url, headers={"Origin": "https://evil.example"})
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"200 OK" in await reader.read(200)
        writer.close()

        sock = await ws.connect(url, headers={"Sec-WebSocket-Protocol": "ws"})
        assert sock.headers.get("sec-websocket-protocol") == "ws"
        hello = json.loads(await sock.recv())
        assert hello["type"] == "hello" and {p["id"] for p in hello["providers"]} >= {
            "anthropic", "openai", "claude_code", "mock"}
        assert {s["id"] for s in hello["specialists"]} >= {"general", "unreal-blueprint", "unreal-verifier"}
        await sock.send(json.dumps({"type": "user_message", "text": "add a cube", "provider": "mock",
                                    "mock_script": SPAWN_SCRIPT}))
        events = []
        while True:
            e = json.loads(await sock.recv())
            events.append(e)
            if e["type"] == "turn_end":
                break
        await sock.close()
        server.close()
        return events

    events = run(go())
    assert editor.actors == ["Agent_Cube"]
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["unreal_status", "scene_compose"]


def test_ws_large_and_fragmented_messages():
    async def go():
        got = []

        async def echo(sock):
            while True:
                got.append(await sock.recv())
                await sock.send(got[-1])

        server = await ws.serve(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        c = await ws.connect(f"ws://127.0.0.1:{port}/")
        big = "x" * 200_000
        await c.send(big)
        assert await c.recv() == big
        # hand-built fragmented text message: "he" + "llo"
        await c._frame(0x1 & 0x0F, b"")  # empty text frame is a full message
        assert await c.recv() == ""
        c.writer.write(bytes([0x01, 0x82]) + b"\0\0\0\0" + b"he" + bytes([0x80, 0x83]) + b"\0\0\0\0" + b"llo")
        await c.writer.drain()
        assert await c.recv() == "hello"
        await c.send(b"\x00\x01")
        assert await c.recv() == b"\x00\x01"
        await c.close()
        server.close()

    run(go())


def test_safety_rules():
    assert classify("asset_manage", {"operations": [{"op": "duplicate", "source": "a", "destination": "b"}]}) is None
    assert "not undoable" in classify("asset_manage", {"operations": [{"op": "delete", "path": "/Game/A"}]})
    assert classify("level_open", {"path": "/Game/M", "discard_changes": True})
    assert classify("editor_action", {"action": "load_level", "discard_changes": True})
    assert classify("asset_import", {"files": ["a.fbx"], "replace_existing": True})
    assert classify("cpp_source", {"action": "write", "path": "Source/X.cpp"})
    assert classify("cpp_source", {"action": "read", "path": "Source/X.cpp"}) is None
    assert classify("execute_python", {"code": "unreal.EditorAssetLibrary.delete_asset('/Game/A')"})
    assert classify("execute_python", {"code": "print(1)"}) is None
    # undoable actor deletes only ask in strict mode
    op = {"operations": [{"op": "delete", "target": "Lamp"}]}
    assert classify("scene_compose", op) is None and classify("scene_compose", op, "strict")
    assert classify("scene_compose", op, "off") is None
    assert classify("scene_query", {}, "all") is None and classify("scene_compose", op, "all")
    assert classify("material_edit", {"path": "/Game/M", "clear": True})


def test_specialists_parse_and_tool_subsets(runner):
    specs = load_specialists(REPO / "agents")
    expected = {"unreal-blueprint", "unreal-python-batch", "unreal-level-designer", "unreal-vfx",
                "unreal-verifier", "unreal-tech-artist", "unreal-animator"}
    assert expected <= set(specs)
    all_tools = {t.name for t in run(runner.tool_specs([]))}
    for name in expected:
        s = specs[name]
        assert s["description"] and len(s["prompt"]) > 200, name
        assert s["tools"], name
        unknown = set(s["tools"]) - all_tools - {"niagara_apply"}  # niagara_apply lands on a sibling branch
        assert not unknown, (name, unknown)
    bp = run(runner.tool_specs(specs["unreal-blueprint"]["tools"]))
    assert {"bp_export", "bp_apply"} <= {t.name for t in bp} and len(bp) < len(all_tools)
    assert parse_agent_md("no frontmatter") is None


def test_format_context():
    assert format_context(None) == ""
    s = format_context({"level": "/Game/M", "selected_actors": [{"label": "Lamp", "class": "PointLight"}],
                        "selected_assets": [{"path": "/Game/SM_Rock", "class": "StaticMesh"}], "pie": True})
    assert "Lamp (PointLight)" in s and "/Game/SM_Rock (StaticMesh)" in s and "Play In Editor" in s


# ----------------------------------------------------------------------------------- provider wire tests
TOOLS = [ToolSpec("unreal_status", "status", {"type": "object", "properties": {}}),
         ToolSpec("scene_compose", "compose", {"type": "object", "properties": {"operations": {"type": "array"}},
                                               "required": ["operations"]})]


def test_openai_provider_streams_text_and_tool_calls():
    sse = [
        {"choices": [{"delta": {"content": "Spawning "}}]},
        {"choices": [{"delta": {"content": "a cube."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {
            "name": "scene_compose", "arguments": '{"operations": [{"op": "spa'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'wn"}]}'}}]},
                      "finish_reason": "tool_calls"}]},
    ]
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in sse) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    p = OpenAICompatProvider("qwen", "SYS", TOOLS, {"transport": httpx.MockTransport(handler),
                                                    "base_url": "http://local/v1", "api_key": "k"})
    p.add_user("hi")
    deltas = []

    async def emit(e):
        deltas.append(e["text"])

    r = run(p.step(emit))
    assert "".join(deltas) == "Spawning a cube."
    assert r.tool_calls[0].name == "scene_compose" and r.tool_calls[0].args == {"operations": [{"op": "spawn"}]}
    assert seen["body"]["tools"][0]["function"]["name"] == "unreal_status" and seen["auth"] == "Bearer k"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    p.add_tool_results([ToolOutcome(r.tool_calls[0], True, '{"success": true}', image_png_b64="AAAA")])
    assert p.messages[-1]["role"] == "tool" and p.messages[-1]["tool_call_id"] == "call_1"
    assert "no images" in p.messages[-1]["content"]


class FakeBlock:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeStream:
    def __init__(self, final, events):
        self.final, self.events = final, events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e
        return gen()

    async def get_final_message(self):
        return self.final


class FakeAnthropic:
    def __init__(self, final, events):
        self.calls = []
        outer = self

        class Messages:
            def stream(self, **params):
                outer.calls.append(params)
                return FakeStream(final, events)

        class Beta:
            messages = Messages()

        self.messages = Messages()
        self.beta = Beta()


def test_anthropic_provider_manual_loop_shapes():
    pytest.importorskip("anthropic")
    final = FakeBlock(stop_reason="tool_use", content=[
        FakeBlock(type="thinking", thinking="", signature="sig"),
        FakeBlock(type="text", text="Spawning."),
        FakeBlock(type="tool_use", id="toolu_1", name="scene_compose", input={"operations": []})])
    client = FakeAnthropic(final, [FakeBlock(type="text", text="Spawning.")])
    p = AnthropicProvider("", "SYS", TOOLS, {"client": client})
    p.add_user("hi")
    out = []

    async def emit(e):
        out.append(e)

    r = run(p.step(emit))
    params = client.calls[0]
    assert params["model"] == "claude-opus-5-5" and params["thinking"] == {"type": "adaptive"}
    assert params["betas"] == ["server-side-fallback-2026-07-01"] and params["fallbacks"] == "default"
    assert params["tools"][1]["eager_input_streaming"] is True and params["system"] == "SYS"
    assert r.tool_calls[0].id == "toolu_1" and out[0]["text"] == "Spawning."
    assert p.messages[-1]["content"] is final.content  # thinking blocks passed back unchanged
    p.add_tool_results([ToolOutcome(r.tool_calls[0], False, "boom", image_png_b64="iVBO")])
    blk = p.messages[-1]["content"][0]
    assert blk["type"] == "tool_result" and blk["is_error"] is True and blk["content"][1]["type"] == "image"


def test_anthropic_refusal_stops_turn():
    pytest.importorskip("anthropic")
    final = FakeBlock(stop_reason="refusal", stop_details=FakeBlock(category="cyber"), content=[])
    p = AnthropicProvider("", "S", TOOLS, {"client": FakeAnthropic(final, [])})
    p.add_user("x")

    async def emit(e):
        pass

    r = run(p.step(emit))
    assert r.stop_reason == "refusal" and "cyber" in r.error and not r.tool_calls


def test_claude_code_provider_relays_stream_json(tmp_path):
    events = [
        {"type": "system", "subtype": "init", "session_id": "sess-1",
         "mcp_servers": [{"name": "unreal", "status": "connected"}]},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Spawning."},
            {"type": "tool_use", "id": "tu1", "name": "mcp__unreal__scene_compose", "input": {"operations": []}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                  "content": [{"type": "text", "text": "{\"success\": true}"}]}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "done", "session_id": "sess-1",
         "total_cost_usd": 0.0},
    ]
    fake = tmp_path / "claude"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        open({str(tmp_path / 'argv.json')!r}, 'w').write(json.dumps(sys.argv[1:]))
        for e in {events!r}:
            print(json.dumps(e), flush=True)
        """))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    p = ClaudeCodeProvider("", "SPECIALIST", TOOLS, {"claude_bin": str(fake), "approve_url": "ws://x/approve",
                                                    "read_only_tools": ["unreal_status"]})
    out = []

    async def emit(e):
        out.append(e)

    r = run(p.run_turn("add a cube", emit))
    assert r.error is None and r.stop_reason == "success" and p.session_id == "sess-1"
    call = next(e for e in out if e["type"] == "tool_call")
    assert call["name"] == "scene_compose"
    res = next(e for e in out if e["type"] == "tool_result")
    assert res["ok"] and res["name"] == "scene_compose"
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[:2] == ["-p", "add a cube"] and "--strict-mcp-config" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[argv.index("--permission-prompt-tool") + 1] == "mcp__unrealmcp_agent__approve"
    assert argv[argv.index("--allowedTools") + 1] == "mcp__unreal__unreal_status"
    cfg = json.loads(argv[argv.index("--mcp-config") + 1])
    assert {"unreal", "unrealmcp_agent"} == set(cfg["mcpServers"])
    # second turn resumes the session
    run(p.run_turn("again", emit))
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[argv.index("--resume") + 1] == "sess-1"


def test_claude_code_approval_round_trip(runner, editor):
    """agent_permission's Approver -> runner /approve -> session rules (and the panel's confirm for destructive)."""
    from unreal_mcp.agent_permission import Approver

    async def go():
        await runner.start()
        session, events = make_session(runner)
        runner.sessions[session.key] = session
        url = runner.approve_url(session.key)
        a = Approver(url)
        ok = await a.ask("mcp__unreal__scene_query", {})
        builtin = await a.ask("Bash", {"command": "rm -rf /"})

        async def answer():
            while not any(e["type"] == "confirm_request" for e in events):
                await asyncio.sleep(0.01)
            req = next(e for e in events if e["type"] == "confirm_request")
            await session.handle({"type": "confirm", "id": req["id"], "allow": False})

        t = asyncio.create_task(answer())
        destructive = await a.ask("mcp__unreal__asset_manage", {"operations": [{"op": "delete", "path": "/Game/A"}]})
        await t
        runner.server.close()
        return ok, builtin, destructive

    ok, builtin, destructive = run(go())
    assert ok == {"behavior": "allow", "updatedInput": {}}
    assert builtin["behavior"] == "deny" and "not an UnrealMCP tool" in builtin["message"]
    assert destructive["behavior"] == "deny" and "declined" in destructive["message"]


def test_runner_cli_once_with_mock(fake, editor, tmp_path, capsys, monkeypatch):
    from unreal_mcp import agent_runner

    script = tmp_path / "s.json"
    script.write_text(json.dumps(SPAWN_SCRIPT))
    monkeypatch.setenv("UNREAL_MCP_PORT", str(fake.port))
    rc = agent_runner.main(["--once", "add a cube", "--provider", "mock", "--mock-script", str(script)])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rc == 0 and editor.actors == ["Agent_Cube"]
    assert lines[-1]["type"] in ("turn_end", "status")


def test_token_file_is_private(runner, tmp_path, monkeypatch):
    from unreal_mcp import agent_runner
    monkeypatch.setattr(agent_runner, "HOME", tmp_path)
    runner.port = 12345
    path = runner.write_token_file()
    data = json.loads(path.read_text())
    assert data["token"] == "tok" and data["port"] == 12345
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_mock_provider_records_and_finishes():
    p = MockProvider("", "", [], {"script": [{"text": "a"}]})
    out = []

    async def emit(e):
        out.append(e)

    assert run(p.step(emit)).text == "a"
    assert run(p.step(emit)).text == "(mock script finished)"
    p.add_tool_results([ToolOutcome(ToolCall("1", "x", {}), True, "")])
    assert len(p.results) == 1


def test_demo_mock_script_runs(runner, editor):
    """scripts/agent_mock_demo.json (used in the WINDOWS_TEST checklist) names real tools with valid arguments."""
    script = json.loads((REPO / "scripts" / "agent_mock_demo.json").read_text())

    async def go():
        session, events = make_session(runner)

        async def deny():
            while not any(e["type"] == "confirm_request" for e in events):
                await asyncio.sleep(0.01)
            req = next(e for e in events if e["type"] == "confirm_request")
            await session.handle({"type": "confirm", "id": req["id"], "allow": False})

        await collect_turn(session, events, {"type": "user_message", "text": "demo", "mock_script": script})
        t = asyncio.create_task(deny())
        await collect_turn(session, events, {"type": "user_message", "text": "next"})
        await t
        return events

    events = run(go())
    results = [e for e in events if e["type"] == "tool_result"]
    assert [r["name"] for r in results] == ["scene_brief", "scene_compose", "scene_assert", "asset_manage"]
    assert all(r["ok"] for r in results[:2]) and results[3].get("denied")
    assert not any("invalid arguments" in r["result"] or "unknown tool" in r["result"] for r in results)


def test_claude_code_can_attach_epic_native_server():
    p = ClaudeCodeProvider("", "S", TOOLS, {"native_mcp_url": "http://127.0.0.1:8000/mcp"})
    cfg = p.mcp_config()["mcpServers"]
    assert cfg["unreal-native"] == {"type": "http", "url": "http://127.0.0.1:8000/mcp"} and "unreal" in cfg
    assert "unreal-native" not in ClaudeCodeProvider("", "S", TOOLS, {}).mcp_config()["mcpServers"]


def test_native_tool_classification():
    from unreal_mcp.agent_safety import classify_native
    assert classify_native("DeleteAssets", {}) and classify_native("SaveLevel", {})
    assert classify_native("GetSelectedActors", {}) is None
    assert classify_native("GetSelectedActors", {}, "strict") and classify_native("DeleteAssets", {}, "off") is None


def test_skill_and_command_resources(fake, editor):
    """unreal:// resources and the commands/*.md prompts on the real server module."""
    import importlib

    from unreal_mcp import connection
    server = importlib.import_module("unreal_mcp_server_advanced")
    uris = {str(r.uri) for r in run(server.mcp.list_resources())}
    assert {"unreal://project", "unreal://level/summary", "unreal://selection"} <= uris
    prompts = {p.name for p in run(server.mcp.list_prompts())}
    assert {"unreal-status", "unreal-playtest", "unreal-screenshot", "unreal-package", "unreal-bench"} <= prompts
    conn = connection.UnrealConnection(Settings(port=fake.port))
    old = connection._connection
    connection._connection = conn
    try:
        fake.on("project_context", lambda p: {"project_name": "Demo", "engine_version": "5.8.4"})
        body = run(server.mcp.read_resource("unreal://project"))
        text = body[0].content if isinstance(body, list | tuple) else str(body)
        assert '"project_name": "Demo"' in str(text)
    finally:
        connection._connection = old
        conn.disconnect()
