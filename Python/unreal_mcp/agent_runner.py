"""UnrealMCP agent runner: the backend of the in-editor "UnrealMCP Agent" panel.

A small local process that runs an agentic tool-use loop over the UnrealMCP tools, in-process (the same
tool modules the MCP server serves, talking to the editor plugin over its TCP port). The panel talks to
it over a localhost WebSocket. Providers are pluggable and model-agnostic (agent_providers.py).

    cd Python && uv run python -m unreal_mcp.agent_runner                 # serve ws://127.0.0.1:55600
    uv run python -m unreal_mcp.agent_runner --once "add a point light" --provider mock --mock-script s.json

Security: the WebSocket can drive execute_python, so every connection needs the runner's token
(?token=... or "Authorization: Bearer ..."). The token is written to ~/.unreal_mcp/agent_runner_<port>.json
(mode 0600) for the panel to read, and connections that carry a browser Origin header are refused.

Protocol (JSON text messages):
  client -> runner  {"type":"user_message","text":..,"context":{..},"provider":..,"model":..,"specialist":..}
                    {"type":"stop"}  {"type":"confirm","id":..,"allow":bool}  {"type":"reset"}
                    {"type":"config","confirm_mode":"off|destructive|strict|all"}
  runner -> client  hello, status{state,detail}, turn_start, text_delta, assistant_text, tool_call,
                    tool_result, confirm_request, notice, error, turn_end{undo_steps}
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from . import ws
from .agent_providers import (
    MAX_RESULT_CHARS,
    PROVIDERS,
    ClaudeCodeProvider,
    Provider,
    StepResult,
    ToolCall,
    ToolOutcome,
    ToolSpec,
    validate_args,
)
from .agent_safety import MODES, READ_ONLY, classify, classify_native
from .config import Settings
from .connection import UnrealConnection

logger = logging.getLogger("unreal_mcp.agent_runner")

VERSION = "0.1.0"
DEFAULT_PORT = 55600
REPO_ROOT = Path(__file__).resolve().parents[2]
HOME = Path(os.path.expanduser("~")) / ".unreal_mcp"

BASE_PROMPT = """You are the UnrealMCP agent, running inside the user's Unreal Editor with tools that act on the \
live editor. Work in a loop: observe (scene_brief, scene_query, actor_inspect, editor_log, viewport_capture), act \
with the most specific tool (scene_compose, bp_apply, material_edit, ...; execute_python only when nothing else fits, \
wrapping edits in `with unreal.ScopedEditorTransaction("..."):`), then verify (scene_assert, pie_test, \
viewport_capture) before saying you are done. Each user message starts with an <editor_context> block describing \
the open level and the user's selection: "this", "these" and "the selected" refer to it. Keep replies short: say what \
you changed and how you verified it. Do not save, delete assets or switch maps unless asked. If a tool fails, read the \
error, fix the call and retry once before reporting the problem."""


# ----------------------------------------------------------------------------------- specialists
def parse_agent_md(text: str) -> dict[str, Any] | None:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        return None
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
    return {"name": meta.get("name", ""), "description": meta.get("description", ""),
            "tools": [t.rsplit("__", 1)[-1] for t in tools
                      if t.startswith("mcp__") and not t.endswith("*") and "unreal-native" not in t],
            "prompt": m.group(2).strip()}


def load_specialists(agents_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """The Claude Code subagents in agents/*.md double as the panel's specialists."""
    out: dict[str, dict[str, Any]] = {"general": {"name": "general", "description": "All UnrealMCP tools",
                                                  "tools": [], "prompt": ""}}
    d = agents_dir or Path(os.environ.get("UNREAL_MCP_AGENTS_DIR") or REPO_ROOT / "agents")
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            spec = parse_agent_md(f.read_text(encoding="utf-8"))
            if spec and spec["name"]:
                out[spec["name"]] = spec
    return out


# -------------------------------------------------------------------------------------- tools
class ToolBox:
    """The UnrealMCP tool modules registered on a private FastMCP instance; calls run in a worker thread."""

    def __init__(self, get_conn):
        from mcp.server.fastmcp import FastMCP

        from .tools import register_all

        self.get_conn = get_conn
        self.mcp = FastMCP("unrealmcp-agent-runner")
        register_all(self.mcp, get_conn)
        self.specs: dict[str, ToolSpec] = {}

    async def load(self) -> dict[str, ToolSpec]:
        if not self.specs:
            for t in await self.mcp.list_tools():
                self.specs[t.name] = ToolSpec(t.name, t.description or "", dict(t.inputSchema or {}))
        return self.specs

    def _call_sync(self, name: str, args: dict[str, Any]):
        return asyncio.run(self.mcp.call_tool(name, args))

    async def call(self, call: ToolCall) -> ToolOutcome:
        spec = self.specs.get(call.name)
        if spec is None:
            return ToolOutcome(call, False, f"unknown tool {call.name!r}")
        err = validate_args(spec, call.args)
        if err:
            return ToolOutcome(call, False, f"invalid arguments: {err}")
        try:
            content = await asyncio.to_thread(self._call_sync, call.name, call.args)
        except Exception as e:  # ToolError, validation errors, transport failures
            return ToolOutcome(call, False, f"{type(e).__name__}: {e}")
        texts, image = [], None
        for c in content:
            if getattr(c, "type", "") == "text":
                texts.append(c.text)
            elif getattr(c, "type", "") == "image":
                image = c.data if isinstance(c.data, str) else base64.b64encode(c.data).decode("ascii")
        text = "\n".join(texts)
        ok = True
        try:
            parsed = json.loads(text) if text else None
            if isinstance(parsed, dict) and parsed.get("success") is False:
                ok = False
        except json.JSONDecodeError:
            pass
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"\n... (truncated, {len(text)} chars)"
        if image and not text:
            text = "viewport image captured"
        return ToolOutcome(call, ok, text, image)


def editor_call(get_conn, command: str, params: dict[str, Any]) -> dict[str, Any]:
    reply = get_conn().send_command(command, params, timeout=15)
    if reply.get("status") == "error":
        return {"success": False, "error": reply.get("error")}
    return {"success": True, **(reply.get("result") or {})}


def format_context(ctx: dict[str, Any] | None) -> str:
    if not ctx:
        return ""
    lines = []
    if ctx.get("level"):
        lines.append(f"Open level: {ctx['level']}")
    if ctx.get("pie"):
        lines.append("Play In Editor is running.")
    sel = ctx.get("selected_actors") or []
    if sel:
        shown = ", ".join(f"{a.get('label')} ({a.get('class')})" if isinstance(a, dict) else str(a) for a in sel[:40])
        lines.append(f"Selected actors ({len(sel)}): {shown}")
    else:
        lines.append("Selected actors: none")
    assets = ctx.get("selected_assets") or []
    if assets:
        shown = ", ".join(f"{a.get('path')} ({a.get('class')})" if isinstance(a, dict) else str(a) for a in assets[:40])
        lines.append(f"Selected in the Content Browser ({len(assets)}): {shown}")
    if ctx.get("content_browser_path"):
        lines.append(f"Content Browser folder: {ctx['content_browser_path']}")
    return "<editor_context>\n" + "\n".join(lines) + "\n</editor_context>\n\n"


# ------------------------------------------------------------------------------------- session
class Session:
    """One conversation. ``send`` delivers events to the client (a WebSocket or a test sink)."""

    def __init__(self, runner: Runner, send):
        self.runner = runner
        self.send_raw = send
        self.provider: Provider | None = None
        self.provider_key: tuple[str, str, str] | None = None
        self.task: asyncio.Task | None = None
        self.pending_confirms: dict[str, asyncio.Future] = {}
        self.confirm_mode = runner.confirm_mode
        self.turn = 0
        self.key = uuid.uuid4().hex

    async def emit(self, event: dict[str, Any]) -> None:
        event.setdefault("turn", self.turn)
        try:
            await self.send_raw(event)
        except ws.ConnectionClosed:
            pass

    async def status(self, state: str, detail: str = "") -> None:
        await self.emit({"type": "status", "state": state, "detail": detail})

    async def handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "user_message":
            if self.task and not self.task.done():
                await self.emit({"type": "error", "message": "a turn is already running; stop it first"})
                return
            self.task = asyncio.create_task(self.run_turn(msg))
        elif kind == "stop":
            await self.stop()
        elif kind == "confirm":
            fut = self.pending_confirms.get(str(msg.get("id")))
            if fut and not fut.done():
                fut.set_result(bool(msg.get("allow")))
        elif kind == "reset":
            await self.stop()
            await self._drop_provider()
            await self.emit({"type": "notice", "text": "New conversation."})
        elif kind == "config":
            if msg.get("confirm_mode") in MODES:
                self.confirm_mode = msg["confirm_mode"]
            await self.emit({"type": "notice", "text": f"Confirmations: {self.confirm_mode}"})
        elif kind == "hello":
            await self.emit(await self.runner.hello())
        else:
            await self.emit({"type": "error", "message": f"unknown message type {kind!r}"})

    async def stop(self) -> None:
        for fut in self.pending_confirms.values():
            if not fut.done():
                fut.set_result(False)
        if isinstance(self.provider, ClaudeCodeProvider):
            await self.provider.stop()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass

    async def _drop_provider(self) -> None:
        if self.provider:
            await self.provider.close()
        self.provider, self.provider_key = None, None

    async def _ensure_provider(self, msg: dict[str, Any]) -> Provider:
        pid = msg.get("provider") or self.runner.default_provider
        cls = PROVIDERS.get(pid)
        if cls is None:
            raise ValueError(f"unknown provider {pid!r}; expected one of {sorted(PROVIDERS)}")
        model = msg.get("model") or cls.default_model()
        specialist = msg.get("specialist") or "general"
        key = (pid, model, specialist)
        if self.provider and self.provider_key == key:
            return self.provider
        if self.provider:
            await self.emit({"type": "notice", "text": "Provider, model or specialist changed: new conversation."})
        await self._drop_provider()
        spec = self.runner.specialists.get(specialist) or self.runner.specialists["general"]
        tools = await self.runner.tool_specs(spec["tools"])
        system = BASE_PROMPT + ("\n\n" + spec["prompt"] if spec["prompt"] else "")
        options = dict(self.runner.provider_options.get(pid, {}))
        if msg.get("mock_script") is not None and pid == "mock":
            options["script"] = msg["mock_script"]
        if pid == "claude_code":
            options.setdefault("approve_url", self.runner.approve_url(self.key))
            options.setdefault("read_only_tools", sorted(READ_ONLY & {t.name for t in tools}))
        self.provider = cls(model, system, tools, options)
        self.provider_key = key
        return self.provider

    async def confirm(self, call: ToolCall, reason: str) -> bool:
        cid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.pending_confirms[cid] = fut
        await self.emit({"type": "confirm_request", "id": cid, "tool_id": call.id, "tool": call.name,
                         "args": call.args, "reason": reason})
        await self.status("waiting_confirm", f"{call.name}: {reason}")
        try:
            return await asyncio.wait_for(fut, self.runner.confirm_timeout)
        except asyncio.TimeoutError:
            await self.emit({"type": "notice", "text": f"No answer for {call.name}: denied."})
            return False
        finally:
            self.pending_confirms.pop(cid, None)

    async def approve_external(self, tool_name: str, args: dict[str, Any]) -> tuple[bool, str]:
        """Permission check for tools Claude Code runs itself (agent_permission.py asks us)."""
        prefix = f"mcp__{ClaudeCodeProvider.SERVER}__"
        native = f"mcp__{ClaudeCodeProvider.NATIVE}__"
        if tool_name.startswith(native):
            name = tool_name[len(native):]
            if name in ("list_toolsets", "describe_toolset"):  # Epic's tool-search meta tools: read-only
                return True, ""
            if name == "call_tool":  # bEnableToolSearch (Epic's default): the real tool is an argument
                name = str(args.get("tool_name", ""))
            reason = classify_native(name, args, self.confirm_mode)
            if not reason:
                return True, ""
            ok = await self.confirm(ToolCall(id="", name=f"unreal-native/{name}", args=args), reason)
            return ok, "" if ok else f"the user declined: {reason}"
        if not tool_name.startswith(prefix):
            if self.runner.claude_code_builtin_tools:
                return True, ""
            return False, f"{tool_name} is not an UnrealMCP tool; the in-editor agent only uses UnrealMCP tools"
        name = tool_name[len(prefix):]
        reason = classify(name, args, self.confirm_mode)
        if not reason:
            return True, ""
        ok = await self.confirm(ToolCall(id="", name=name, args=args), reason)
        if ok:
            await self.status("running_tool", name)
        return ok, "" if ok else f"the user declined: {reason}"

    async def run_turn(self, msg: dict[str, Any]) -> None:
        self.turn += 1
        t0 = time.monotonic()
        tool_count, undo_steps, result = 0, None, StepResult()
        await self.emit({"type": "turn_start"})
        await self.status("thinking")
        began = await asyncio.to_thread(self.runner.turn_marker, "begin", self.turn)
        pending: list[ToolCall] = []
        try:
            provider = await self._ensure_provider(msg)
            prompt = format_context(msg.get("context")) + str(msg.get("text", ""))
            if provider.manages_tools:
                result = await provider.run_turn(prompt, self.emit)
            else:
                provider.add_user(prompt)
                for _ in range(self.runner.max_steps):
                    result = await provider.step(self.emit)
                    if result.text:
                        await self.emit({"type": "assistant_text", "text": result.text})
                    if result.error or not result.tool_calls:
                        break
                    pending = list(result.tool_calls)
                    outcomes = []
                    for call in result.tool_calls:
                        outcomes.append(await self.run_tool(call))
                        pending.remove(call)
                        tool_count += 1
                    provider.add_tool_results(outcomes)
                    await self.status("thinking")
                else:
                    await self.emit({"type": "notice", "text": f"Stopped after {self.runner.max_steps} steps."})
            if result.error:
                await self.emit({"type": "error", "message": result.error})
        except asyncio.CancelledError:
            if pending and self.provider and not self.provider.manages_tools:
                self.provider.add_tool_results([ToolOutcome(c, False, "not run: the user pressed Stop")
                                                for c in pending])
            await self.emit({"type": "notice", "text": "Stopped."})
            result.stop_reason = "stopped"
        except Exception as e:  # provider/API failures surface in the panel, the runner keeps going
            logger.exception("turn failed")
            await self.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            result.stop_reason = "error"
        finally:
            if began.get("success"):
                ended = await asyncio.to_thread(self.runner.turn_marker, "end", self.turn)
                undo_steps = ended.get("steps") if ended.get("success") else None
            await self.emit({"type": "turn_end", "stop_reason": result.stop_reason, "tool_calls": tool_count,
                             "undo_steps": undo_steps, "seconds": round(time.monotonic() - t0, 2)})
            await self.status("error" if result.stop_reason == "error" else "done")

    async def run_tool(self, call: ToolCall) -> ToolOutcome:
        await self.emit({"type": "tool_call", "id": call.id, "name": call.name, "args": call.args})
        reason = classify(call.name, call.args, self.confirm_mode)
        if reason and not await self.confirm(call, reason):
            out = ToolOutcome(call, False, f"not run: the user declined ({reason})")
        else:
            await self.status("running_tool", call.name)
            t0 = time.monotonic()
            out = await self.runner.toolbox.call(call)
            out_ms = int((time.monotonic() - t0) * 1000)
            await self.emit({"type": "tool_result", "id": call.id, "name": call.name, "ok": out.ok,
                             "result": out.text[:8000], "image": bool(out.image_png_b64), "ms": out_ms})
            return out
        await self.emit({"type": "tool_result", "id": call.id, "name": call.name, "ok": False,
                         "result": out.text, "denied": True})
        return out


# -------------------------------------------------------------------------------------- runner
class Runner:
    def __init__(self, settings: Settings | None = None, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 token: str | None = None, default_provider: str = "anthropic", confirm_mode: str = "destructive",
                 provider_options: dict[str, dict[str, Any]] | None = None, agents_dir: Path | None = None,
                 max_steps: int = 40, confirm_timeout: float = 600.0, claude_code_builtin_tools: bool = False,
                 conn: UnrealConnection | None = None):
        self.settings = settings or Settings.from_env()
        self.conn = conn or UnrealConnection(self.settings)
        self.host, self.port = host, port
        self.token = token or secrets.token_urlsafe(24)
        self.default_provider = default_provider
        self.confirm_mode = confirm_mode
        self.provider_options = provider_options or {}
        self.specialists = load_specialists(agents_dir)
        self.max_steps = max_steps
        self.confirm_timeout = confirm_timeout
        self.claude_code_builtin_tools = claude_code_builtin_tools
        self.toolbox = ToolBox(lambda: self.conn)
        self.sessions: dict[str, Session] = {}
        self.server: asyncio.Server | None = None

    def get_conn(self) -> UnrealConnection:
        return self.conn

    async def tool_specs(self, subset: list[str]) -> list[ToolSpec]:
        specs = await self.toolbox.load()
        if not subset:
            return list(specs.values())
        keep = [specs[n] for n in subset if n in specs]
        return keep or list(specs.values())

    def turn_marker(self, action: str, turn: int) -> dict[str, Any]:
        """agent_turn begin/end in the editor: counts the undo transactions one agent turn made."""
        try:
            return editor_call(self.get_conn, "agent_turn", {"action": action, "turn": turn})
        except Exception as e:
            return {"success": False, "error": str(e)}

    def approve_url(self, session_key: str) -> str:
        bound = self.server.sockets[0].getsockname()[1] if self.server else self.port
        return f"ws://127.0.0.1:{bound}/approve?token={self.token}&session={session_key}"

    async def hello(self) -> dict[str, Any]:
        provs = []
        for pid, cls in PROVIDERS.items():
            ok, detail = await asyncio.to_thread(cls.status)
            provs.append({"id": pid, "label": cls.label, "configured": ok, "detail": detail,
                          "default_model": cls.default_model()})
        return {"type": "hello", "version": VERSION, "providers": provs, "default_provider": self.default_provider,
                "specialists": [{"id": k, "description": v["description"]} for k, v in self.specialists.items()],
                "confirm_mode": self.confirm_mode,
                "unreal": {"host": self.settings.host, "port": self.settings.port}}

    def authorize(self, path: str, headers: dict[str, str]) -> str | None:
        origin = headers.get("origin", "")
        if origin.lower().startswith(("http://", "https://")) or origin == "null":
            return "browser origins are not allowed"
        q = {k: v[0] for k, v in parse_qs(path.partition("?")[2]).items()}
        supplied = q.get("token") or headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not supplied or not secrets.compare_digest(supplied, self.token):
            return "bad or missing token"
        return None

    async def handle_ws(self, sock: ws.WebSocket) -> None:
        path = sock.path.partition("?")[0]
        if path == "/approve":
            await self._handle_approver(sock)
            return

        async def send(event: dict[str, Any]) -> None:
            await sock.send(json.dumps(event, default=str))

        session = Session(self, send)
        self.sessions[session.key] = session
        await session.emit(await self.hello())
        await session.status("idle")
        try:
            while True:
                raw = await sock.recv()
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    await session.emit({"type": "error", "message": "messages must be JSON objects"})
                    continue
                await session.handle(msg)
        except ws.ConnectionClosed:
            pass
        finally:
            await session.stop()
            await session._drop_provider()
            self.sessions.pop(session.key, None)

    async def _handle_approver(self, sock: ws.WebSocket) -> None:
        session = self.sessions.get(sock.query.get("session", ""))
        while True:
            req = json.loads(await sock.recv())
            if session is None:
                allow, message = False, "the panel session that started Claude Code is gone"
            else:
                allow, message = await session.approve_external(str(req.get("tool_name", "")),
                                                                req.get("input") or {})
            await sock.send(json.dumps({"id": req.get("id"), "allow": allow, "message": message}))

    async def start(self) -> asyncio.Server:
        await self.toolbox.load()
        self.server = await ws.serve(self.handle_ws, self.host, self.port, self.authorize)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.server

    def write_token_file(self) -> Path:
        HOME.mkdir(parents=True, exist_ok=True)
        path = HOME / f"agent_runner_{self.port}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"port": self.port, "token": self.token, "pid": os.getpid(), "version": VERSION}, f)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path


# ----------------------------------------------------------------------------------------- CLI
async def run_once(runner: Runner, args: argparse.Namespace) -> int:
    """Headless single turn without a panel: prints every event as a JSON line, auto-answers confirmations."""
    await runner.toolbox.load()
    done = asyncio.Event()
    session: Session

    async def send(event: dict[str, Any]) -> None:
        print(json.dumps(event, default=str), flush=True)
        if event["type"] == "confirm_request":
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(session.handle({"type": "confirm", "id": event["id"],
                                                              "allow": args.yes})))
        if event["type"] == "turn_end":
            done.set()

    session = Session(runner, send)
    await session.handle({"type": "user_message", "text": args.once, "provider": args.provider,
                          "model": args.model, "specialist": args.specialist})
    await done.wait()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="unreal_mcp.agent_runner", description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default="127.0.0.1", help="bind address (keep it on loopback)")
    p.add_argument("--port", type=int, default=int(os.environ.get("UNREAL_MCP_AGENT_PORT", DEFAULT_PORT)))
    p.add_argument("--unreal-port", type=int, help="editor plugin port (default UNREAL_MCP_PORT or 55557)")
    p.add_argument("--token", default=os.environ.get("UNREAL_MCP_AGENT_TOKEN"), help="fixed token (default random)")
    p.add_argument("--provider", default=os.environ.get("UNREAL_MCP_AGENT_PROVIDER", "anthropic"),
                   choices=sorted(PROVIDERS))
    p.add_argument("--model", default="")
    p.add_argument("--specialist", default="general")
    p.add_argument("--confirm", default=os.environ.get("UNREAL_MCP_AGENT_CONFIRM", "destructive"), choices=MODES)
    p.add_argument("--mock-script", default=os.environ.get("UNREAL_MCP_MOCK_SCRIPT"),
                   help="JSON file of scripted steps for the mock provider")
    p.add_argument("--openai-base-url", default=None)
    p.add_argument("--native-mcp", nargs="?", const="http://127.0.0.1:8000/mcp",
                   default=os.environ.get("UNREAL_NATIVE_MCP_URL") or None,
                   help="Epic's native MCP server (ModelContextProtocol plugin): enables the epic_* gateway tools for "
                        "every provider and attaches the server directly in Claude Code mode")
    p.add_argument("--max-steps", type=int, default=40)
    p.add_argument("--agents-dir", type=Path, default=None)
    p.add_argument("--claude-code-builtin-tools", action="store_true",
                   help="let Claude Code mode use its own tools (Bash, Edit, ...) besides UnrealMCP's")
    p.add_argument("--once", default=None, help="run one prompt headless, print events as JSON lines, exit")
    p.add_argument("--yes", action="store_true", help="--once: approve confirmation prompts (default deny)")
    args = p.parse_args(argv)

    if args.native_mcp and not os.environ.get("UNREAL_MCP_EPIC_URL"):
        os.environ["UNREAL_MCP_EPIC_URL"] = args.native_mcp  # tools/epic.py registers the gateway from this
    settings = Settings.from_env()
    if args.unreal_port:
        settings = Settings(**{**settings.__dict__, "port": args.unreal_port})
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    options: dict[str, dict[str, Any]] = {"mock": {}, "openai": {}}
    if args.mock_script:
        options["mock"]["script_path"] = args.mock_script
    if args.openai_base_url:
        options["openai"]["base_url"] = args.openai_base_url
    options["claude_code"] = {"python_dir": str(Path(__file__).resolve().parents[1]),
                              "native_mcp_url": args.native_mcp or ""}
    runner = Runner(settings, host=args.host, port=args.port, token=args.token, default_provider=args.provider,
                    confirm_mode=args.confirm, provider_options=options, agents_dir=args.agents_dir,
                    max_steps=args.max_steps, claude_code_builtin_tools=args.claude_code_builtin_tools)
    if args.once is not None:
        return asyncio.run(run_once(runner, args))

    async def serve() -> None:
        server = await runner.start()
        token_file = runner.write_token_file()
        print(json.dumps({"event": "listening", "url": f"ws://{args.host}:{runner.port}/",
                          "token_file": str(token_file), "unreal": f"{settings.host}:{settings.port}"}), flush=True)
        try:
            async with server:
                await server.serve_forever()
        finally:
            try:
                token_file.unlink()
            except OSError:
                pass

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
