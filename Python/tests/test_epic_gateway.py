"""Gateway to Epic's native MCP server, against a fake of its Streamable HTTP endpoint (both tool modes)."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp.server.fastmcp import FastMCP

from unreal_mcp.config import Settings
from unreal_mcp.epic_client import EpicMCPClient
from unreal_mcp.tools import epic

TOOLSETS = {
    "EditorToolset.EditorAppToolset": [
        {"name": "EditorToolset.EditorAppToolset.IsPIERunning", "description": "Whether PIE runs.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "EditorToolset.EditorAppToolset.CaptureEditorImage", "description": "Screenshot.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "EditorToolset.EditorAppToolset.CaptureViewport", "description": "Viewport as FToolsetImage.",
         "inputSchema": {"type": "object", "properties": {}}}],
    "UnrealMCP.Blueprint": [
        {"name": "UnrealMCP.Blueprint.bp_export", "description": "Export.",
         "inputSchema": {"type": "object", "properties": {"blueprint": {"type": "string"}}}}],
}


def _run(name, args):
    short = name.rpartition(".")[2]
    if short == "IsPIERunning":
        return {"content": [{"type": "text", "text": "false"}]}
    if short == "CaptureEditorImage":
        return {"content": [{"type": "image", "data": "iVBORw0K", "mimeType": "image/png"}]}
    if short == "CaptureViewport":   # what the ToolsetRegistry adapter really sends: FToolsetImage as JSON text
        return {"content": [{"type": "text", "text": json.dumps({"ReturnValue": {"MimeType": "image/jpeg",
                                                                                 "Data": "/9j/4AAQ"}})}]}
    if short == "bp_export":
        return {"content": [{"type": "text", "text": json.dumps({"success": True, "bp": args.get("blueprint")})}]}
    return {"isError": True, "content": [{"type": "text", "text": f"Tool '{name}' not found"}]}


class FakeEpic:
    def __init__(self, tool_search=True):
        self.tool_search = tool_search
        self.sessions = set()
        self.calls = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method, sid = msg.get("method"), self.headers.get("Mcp-Session-Id")
                if method == "initialize":
                    sid = f"s{len(fake.sessions)}"
                    fake.sessions.add(sid)
                    return self._reply(msg, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                             "serverInfo": {"name": "unreal-mcp"}}, sid)
                if sid not in fake.sessions:
                    self.send_response(404)
                    self.end_headers()
                    return
                if "id" not in msg:
                    self.send_response(202)
                    self.end_headers()
                    return
                fake.calls.append((method, msg.get("params")))
                if method == "tools/list":
                    return self._reply(msg, {"tools": fake.tools_list()})
                p = msg["params"]
                name, args = p["name"], p.get("arguments") or {}
                if name == "list_toolsets":
                    text = "".join(f"- {n}: about {n}\n" for n in TOOLSETS)
                    return self._reply(msg, {"content": [{"type": "text", "text": text}]})
                if name == "describe_toolset":
                    ts = args["toolset_name"]
                    return self._reply(msg, {"content": [{"type": "text", "text": json.dumps(
                        {"name": ts, "tools": TOOLSETS[ts]})}]})
                if name == "call_tool":
                    return self._reply(msg, _run(f"{args['toolset_name']}.{args['tool_name']}", args["arguments"]),
                                       sse=True)
                return self._reply(msg, _run(name, args))

            def _reply(self, msg, result, sid=None, sse=False):
                body = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})
                if sse:
                    body = f"event: message\ndata: {body}\n\n"
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
                if sid:
                    self.send_header("Mcp-Session-Id", sid)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tools_list(self):
        if self.tool_search:
            return [{"name": n, "description": n, "inputSchema": {"type": "object"}} for n in epic.META]
        return [t for tools in TOOLSETS.values() for t in tools]

    def close(self):
        self.httpd.shutdown()


@pytest.fixture(params=[True, False], ids=["tool_search", "eager"])
def epic_server(request):
    server = FakeEpic(tool_search=request.param)
    yield server
    server.close()


def _mcp(url, mode="meta", toolsets=()):
    mcp = FastMCP("t")
    epic.register(mcp, None, Settings(epic_url=url, epic_mode=mode, epic_toolsets=toolsets))
    return mcp


def _call(mcp, name, args=None):
    return asyncio.run(mcp.call_tool(name, args or {}))


def test_off_without_url():
    mcp = FastMCP("t")
    epic.register(mcp, None, Settings())
    assert asyncio.run(mcp.list_tools()) == []


def test_meta_tools_list_describe_call(epic_server):
    mcp = _mcp(epic_server.url)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"epic_list_toolsets", "epic_describe_toolset", "epic_call_tool"}
    listed = json.loads(_call(mcp, "epic_list_toolsets")[0].text)
    assert "EditorToolset.EditorAppToolset" in listed["toolsets"]
    described = json.loads(_call(mcp, "epic_describe_toolset", {"toolset_name": "UnrealMCP.Blueprint"})[0].text)
    assert described["tools"][0]["name"] == "UnrealMCP.Blueprint.bp_export"
    out = _call(mcp, "epic_call_tool", {"toolset_name": "UnrealMCP.Blueprint", "tool_name": "bp_export",
                                        "arguments": {"blueprint": "/Game/BP"}})
    assert json.loads(out[0].text) == {"success": True, "bp": "/Game/BP"}
    img = _call(mcp, "epic_call_tool", {"toolset_name": "EditorToolset.EditorAppToolset",
                                        "tool_name": "CaptureEditorImage"})
    assert img[0].type == "image" and img[0].mimeType == "image/png"
    vp = _call(mcp, "epic_call_tool", {"toolset_name": "EditorToolset.EditorAppToolset",
                                       "tool_name": "CaptureViewport"})
    assert vp[0].type == "image" and vp[0].mimeType == "image/jpeg" and vp[0].data == "/9j/4AAQ"


def test_error_is_reported(epic_server):
    mcp = _mcp(epic_server.url)
    with pytest.raises(Exception, match="not found"):
        _call(mcp, "epic_call_tool", {"toolset_name": "EditorToolset.EditorAppToolset", "tool_name": "Nope"})


def test_expand_registers_prefixed_tools_except_ours(epic_server):
    mcp = _mcp(epic_server.url, mode="expand")
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"epic_EditorAppToolset_IsPIERunning", "epic_EditorAppToolset_CaptureEditorImage"} <= names
    assert not any("bp_export" in n for n in names)          # UnrealMCP.* is already native here
    assert _call(mcp, "epic_EditorAppToolset_IsPIERunning")[0].text == "false"


def test_expand_filter_and_unreachable():
    server = FakeEpic()
    try:
        mcp = _mcp(server.url, mode="expand", toolsets=("unrealmcp",))
        names = {t.name for t in asyncio.run(mcp.list_tools())}
        assert "epic_Blueprint_bp_export" in names and not any("IsPIERunning" in n for n in names)
        out = _call(mcp, "epic_Blueprint_bp_export", {"blueprint": "/Game/X"})
        assert json.loads(out[0].text)["bp"] == "/Game/X"
    finally:
        server.close()
    # Editor not running: expand is skipped, meta tools still register and report a clear error.
    mcp = _mcp("http://127.0.0.1:9/mcp", mode="expand")
    assert len(asyncio.run(mcp.list_tools())) == 3
    assert "Epic MCP server" in json.loads(_call(mcp, "epic_list_toolsets")[0].text)["error"]


def test_session_expiry_reinitializes(epic_server):
    client = EpicMCPClient(epic_server.url)
    client.list_tools()
    epic_server.sessions.clear()                               # editor restarted
    assert client.list_tools()


def test_safe_names_are_unique_and_short():
    taken: set[str] = set()
    a = epic.safe_name("epic_", "x.Some.VeryLongToolsetName" * 3, "tool.name" * 10, taken)
    b = epic.safe_name("epic_", "x.Some.VeryLongToolsetName" * 3, "tool.name" * 10, taken)
    assert a != b and len(a) <= 64 and len(b) <= 64 and "." not in a
