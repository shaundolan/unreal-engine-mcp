"""Model providers for the agent runner.

Each provider keeps its own conversation history in its native format and exposes one step at a time:
``step()`` makes one model call (streaming text through ``emit``) and returns the tool calls the model
asked for; the runner executes them and hands the outcomes back with ``add_tool_results()``.
The Claude Code provider is different: the ``claude`` CLI runs its own loop and calls the MCP server
itself, so it implements ``run_turn()`` and the runner only relays its events.

Providers:
  mock         scripted steps (tests, demos, live checks); never calls a model
  anthropic    Anthropic API through the official SDK, with the user's own ANTHROPIC_API_KEY
  openai       any OpenAI-compatible /v1/chat/completions endpoint (llama.cpp, vLLM, Ollama, LM Studio)
  claude_code  the `claude` CLI in headless mode (claude -p, stream-json) with this MCP server attached
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Emit = Callable[[dict[str, Any]], Awaitable[None]]

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
OPENAI_DEFAULT_BASE = "http://127.0.0.1:8080/v1"
MAX_RESULT_CHARS = 24000


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolOutcome:
    call: ToolCall
    ok: bool
    text: str
    image_png_b64: str | None = None


@dataclass
class StepResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    error: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


def validate_args(spec: ToolSpec | None, args: Any) -> str | None:
    """Cheap structural check of model-produced arguments before running a tool (FastMCP validates types)."""
    if not isinstance(args, dict):
        return f"arguments must be a JSON object, got {type(args).__name__}"
    if spec is None:
        return None
    missing = [k for k in spec.schema.get("required", []) if k not in args]
    if missing:
        return f"missing required argument(s): {', '.join(missing)}"
    return None


class Provider:
    id = "base"
    label = "Base"
    manages_tools = False  # True: the provider runs tools itself (run_turn), the runner only relays

    def __init__(self, model: str, system: str, tools: list[ToolSpec], options: dict[str, Any] | None = None):
        self.model = model
        self.system = system
        self.tools = tools
        self.options = options or {}

    @classmethod
    def default_model(cls) -> str:
        return ""

    @classmethod
    def status(cls) -> tuple[bool, str]:
        return True, ""

    def add_user(self, text: str) -> None:
        raise NotImplementedError

    async def step(self, emit: Emit) -> StepResult:
        raise NotImplementedError

    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------------------------- mock
class MockProvider(Provider):
    """Replays a script. Each step: {"text": "...", "tool_calls": [{"name": ..., "args": {...}}]}.
    A step without tool_calls ends the turn; the next user message continues with the next step.
    When the script runs out it answers "(mock script finished)". Records what it saw, for tests."""

    id = "mock"
    label = "Mock (scripted, no model)"

    DEFAULT_SCRIPT = [
        {"text": "Checking the editor connection.", "tool_calls": [{"name": "unreal_status", "args": {}}]},
        {"text": "The editor is connected. (This is the mock provider: pick a real provider to chat.)"},
    ]

    def __init__(self, model: str, system: str, tools: list[ToolSpec], options: dict[str, Any] | None = None):
        super().__init__(model or "script", system, tools, options)
        script = self.options.get("script")
        if script is None and self.options.get("script_path"):
            script = json.loads(Path(self.options["script_path"]).read_text(encoding="utf-8"))
        self.script: list[dict[str, Any]] = list(script if script is not None else self.DEFAULT_SCRIPT)
        self.pos = 0
        self.user_messages: list[str] = []
        self.results: list[ToolOutcome] = []
        self.delay = float(self.options.get("delay", 0))

    @classmethod
    def default_model(cls) -> str:
        return "script"

    def add_user(self, text: str) -> None:
        self.user_messages.append(text)

    async def step(self, emit: Emit) -> StepResult:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.pos >= len(self.script):
            text = "(mock script finished)"
            await emit({"type": "text_delta", "text": text})
            return StepResult(text=text)
        s = self.script[self.pos]
        self.pos += 1
        text = s.get("text", "")
        for chunk in _chunks(text, 24):
            await emit({"type": "text_delta", "text": chunk})
        calls = [ToolCall(id=f"mock_{uuid.uuid4().hex[:8]}", name=c["name"], args=dict(c.get("args") or {}))
                 for c in s.get("tool_calls") or []]
        return StepResult(text=text, tool_calls=calls, stop_reason="tool_use" if calls else "end_turn")

    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        self.results.extend(outcomes)


def _chunks(text: str, n: int):
    for i in range(0, len(text), n):
        yield text[i:i + n]


# ----------------------------------------------------------------------------------------- anthropic
class AnthropicProvider(Provider):
    """Anthropic Messages API with streaming, adaptive thinking and client tools (manual loop, so the
    runner can confirm destructive calls and stop between tools). The key comes from the environment
    (ANTHROPIC_API_KEY, or any credential the SDK resolves by itself); the runner never stores it."""

    id = "anthropic"
    label = "Anthropic API (your ANTHROPIC_API_KEY)"

    def __init__(self, model: str, system: str, tools: list[ToolSpec], options: dict[str, Any] | None = None):
        super().__init__(model or self.default_model(), system, tools, options)
        import anthropic  # optional dependency: uv sync --extra anthropic

        self._anthropic = anthropic
        self.client = self.options.get("client") or anthropic.AsyncAnthropic()
        self.messages: list[dict[str, Any]] = []
        self.effort = self.options.get("effort", "medium")
        self.max_tokens = int(self.options.get("max_tokens", 64000))
        self.fallbacks = self.options.get("fallbacks", True)
        self._specs = {t.name: t for t in tools}

    @classmethod
    def default_model(cls) -> str:
        return os.environ.get("UNREAL_MCP_ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL)

    @classmethod
    def status(cls) -> tuple[bool, str]:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "anthropic SDK not installed (cd Python && uv sync --extra anthropic)"
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True, "key from environment"
        if (Path.home() / ".config" / "anthropic").is_dir():
            return True, "credentials from an `ant auth login` profile"
        return False, "set ANTHROPIC_API_KEY in the environment the runner starts from"

    def _tool_defs(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description[:4000], "input_schema": t.schema,
                 "eager_input_streaming": True} for t in self.tools]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    async def _stream(self, emit: Emit, params: dict[str, Any]):
        if self.fallbacks:
            try:
                ctx = self.client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"],
                                                       fallbacks="default", **params)
            except TypeError:  # SDK too old for the fallbacks parameter
                self.fallbacks = False
                ctx = self.client.messages.stream(**params)
        else:
            ctx = self.client.messages.stream(**params)
        async with ctx as stream:
            async for event in stream:
                if getattr(event, "type", "") == "text":
                    await emit({"type": "text_delta", "text": event.text})
            return await stream.get_final_message()

    async def step(self, emit: Emit) -> StepResult:
        params = {"model": self.model, "max_tokens": self.max_tokens, "system": self.system,
                  "tools": self._tool_defs(), "messages": self.messages,
                  "thinking": {"type": "adaptive"}, "output_config": {"effort": self.effort}}
        last_error = None
        for _ in range(3):
            try:
                msg = await self._stream(emit, params)
                break
            except ValueError as e:  # tool-input JSON the SDK could not parse: re-issue the turn
                last_error = e
        else:
            return StepResult(stop_reason="error", error=f"model produced unparseable tool input: {last_error}")
        self.messages.append({"role": "assistant", "content": msg.content})
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        stop = getattr(msg, "stop_reason", "end_turn") or "end_turn"
        if stop == "refusal":
            details = getattr(msg, "stop_details", None)
            cat = getattr(details, "category", None) if details else None
            return StepResult(text=text, stop_reason="refusal", error=f"the model declined (category: {cat})")
        calls = [ToolCall(id=b.id, name=b.name, args=b.input if isinstance(b.input, dict) else {"_raw": b.input})
                 for b in msg.content if getattr(b, "type", "") == "tool_use"]
        if stop == "max_tokens" and calls:
            self.add_tool_results([ToolOutcome(c, False, "not run: tool input was truncated (max_tokens)")
                                   for c in calls])
            return StepResult(text=text, stop_reason="max_tokens", error="tool input truncated at max_tokens")
        if stop == "pause_turn":
            return StepResult(text=text, stop_reason="pause_turn")
        return StepResult(text=text, tool_calls=calls, stop_reason=stop)

    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        blocks = []
        for o in outcomes:
            content: list[dict[str, Any]] = [{"type": "text", "text": o.text or "(empty)"}]
            if o.image_png_b64:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                            "data": o.image_png_b64}})
            blocks.append({"type": "tool_result", "tool_use_id": o.call.id, "content": content,
                           "is_error": not o.ok})
        if blocks:  # all results of one assistant turn go in ONE user message
            self.messages.append({"role": "user", "content": blocks})


# -------------------------------------------------------------------------------------------- openai
class OpenAICompatProvider(Provider):
    """Any OpenAI-compatible chat completions endpoint, streamed. Meant for local models
    (llama.cpp `llama-server --jinja`, vLLM, Ollama, LM Studio). Images from viewport_capture are sent
    as a follow-up user message only when UNREAL_MCP_OPENAI_VISION=1 (most local models are text-only)."""

    id = "openai"
    label = "OpenAI-compatible endpoint (local models)"

    def __init__(self, model: str, system: str, tools: list[ToolSpec], options: dict[str, Any] | None = None):
        super().__init__(model or self.default_model(), system, tools, options)
        self.base_url = (self.options.get("base_url") or self.base()).rstrip("/")
        self.api_key = self.options.get("api_key") or os.environ.get("UNREAL_MCP_OPENAI_API_KEY") \
            or os.environ.get("OPENAI_API_KEY") or ""
        self.vision = bool(self.options.get("vision", os.environ.get("UNREAL_MCP_OPENAI_VISION") == "1"))
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        self.transport = self.options.get("transport")  # httpx transport, for tests

    @staticmethod
    def base() -> str:
        return os.environ.get("UNREAL_MCP_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") \
            or OPENAI_DEFAULT_BASE

    @classmethod
    def default_model(cls) -> str:
        return os.environ.get("UNREAL_MCP_OPENAI_MODEL", "local")

    @classmethod
    def status(cls) -> tuple[bool, str]:
        import httpx
        try:
            r = httpx.get(cls.base().rstrip("/") + "/models", timeout=1.0)
            return r.status_code < 500, f"{cls.base()} answered {r.status_code}"
        except httpx.HTTPError as e:
            return False, f"{cls.base()} not reachable ({type(e).__name__}); set UNREAL_MCP_OPENAI_BASE_URL"

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def _tool_defs(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description[:2000],
                                                  "parameters": t.schema}} for t in self.tools]

    async def step(self, emit: Emit) -> StepResult:
        import httpx
        body = {"model": self.model, "messages": self.messages, "tools": self._tool_defs(), "stream": True}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish = "stop"
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), transport=self.transport) as client:
            async with client.stream("POST", self.base_url + "/chat/completions", json=body,
                                     headers=headers) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode("utf-8", "replace")[:500]
                    return StepResult(stop_reason="error", error=f"HTTP {resp.status_code}: {detail}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            text_parts.append(delta["content"])
                            await emit({"type": "text_delta", "text": delta["content"]})
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "args": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
                        finish = choice.get("finish_reason") or finish
        text = "".join(text_parts)
        tool_calls = []
        for i in sorted(calls):
            c = calls[i]
            try:
                args = json.loads(c["args"]) if c["args"].strip() else {}
            except json.JSONDecodeError:
                args = {"_invalid_json": c["args"]}
            tool_calls.append(ToolCall(id=c["id"] or f"call_{uuid.uuid4().hex[:8]}", name=c["name"], args=args))
        msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": json.dumps(c.args)}} for c in tool_calls]
        self.messages.append(msg)
        return StepResult(text=text, tool_calls=tool_calls, stop_reason="tool_use" if tool_calls else finish)

    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        images = []
        for o in outcomes:
            text = o.text if o.ok else f"ERROR: {o.text}"
            if o.image_png_b64:
                text += "\n(image captured" + (", attached below)" if self.vision else "; this model gets no images)")
                images.append(o.image_png_b64)
            self.messages.append({"role": "tool", "tool_call_id": o.call.id, "content": text})
        if self.vision and images:
            self.messages.append({"role": "user", "content": [
                {"type": "text", "text": "Images returned by the tool calls above:"},
                *({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}} for b in images)]})


# --------------------------------------------------------------------------------------- claude code
class ClaudeCodeProvider(Provider):
    """Runs `claude -p` (headless Claude Code) per turn, with this repo's MCP server attached through
    --mcp-config and --strict-mcp-config, and --resume to keep the conversation across turns.
    Tool calls that are not pre-approved go to --permission-prompt-tool, a tiny MCP server
    (agent_permission.py) that asks the runner, which applies the same confirmation rules as for the
    in-process providers. Uses whatever login the `claude` CLI already has."""

    id = "claude_code"
    label = "Claude Code CLI (claude -p)"
    manages_tools = True
    SERVER = "unreal"
    APPROVER = "unrealmcp_agent"
    NATIVE = "unreal-native"

    def __init__(self, model: str, system: str, tools: list[ToolSpec], options: dict[str, Any] | None = None):
        super().__init__(model or self.default_model(), system, tools, options)
        self.session_id: str | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.claude = self.options.get("claude_bin") or os.environ.get("UNREAL_MCP_CLAUDE_BIN") or "claude"
        self.python_dir = Path(self.options.get("python_dir") or Path(__file__).resolve().parents[1])
        self.approve_url = self.options.get("approve_url", "")
        self.native_url = self.options.get("native_mcp_url") or ""
        self.read_only = list(self.options.get("read_only_tools") or [])
        self.allowed = {t.name for t in tools}

    @classmethod
    def default_model(cls) -> str:
        return os.environ.get("UNREAL_MCP_CLAUDE_CODE_MODEL", "")  # empty: the CLI's own default

    @classmethod
    def status(cls) -> tuple[bool, str]:
        exe = shutil.which(os.environ.get("UNREAL_MCP_CLAUDE_BIN") or "claude")
        return (True, exe) if exe else (False, "`claude` CLI not on PATH (npm i -g @anthropic-ai/claude-code)")

    def mcp_config(self) -> dict[str, Any]:
        env = {k: v for k, v in os.environ.items() if k.startswith("UNREAL_MCP_") and k != "UNREAL_MCP_AGENT_TOKEN"}
        servers: dict[str, Any] = {self.SERVER: {
            "command": "uv", "args": ["--directory", str(self.python_dir), "run", "unreal_mcp_server_advanced.py"],
            "env": env}}
        if self.native_url:  # Epic's ModelContextProtocol plugin (HTTP, in the editor)
            servers[self.NATIVE] = {"type": "http", "url": self.native_url}
        if self.approve_url:
            servers[self.APPROVER] = {
                "command": "uv", "args": ["--directory", str(self.python_dir), "run", "python", "-m",
                                          "unreal_mcp.agent_permission", "--url", self.approve_url],
                "env": env}
        return {"mcpServers": servers}

    def command(self, prompt: str) -> list[str]:
        cmd = [self.claude, "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--mcp-config", json.dumps(self.mcp_config()), "--strict-mcp-config",
               "--append-system-prompt", self.system]
        if self.read_only:
            cmd += ["--allowedTools", ",".join(f"mcp__{self.SERVER}__{t}" for t in self.read_only)]
        if self.approve_url:
            cmd += ["--permission-prompt-tool", f"mcp__{self.APPROVER}__approve"]
        if self.model:
            cmd += ["--model", self.model]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def strip(self, name: str) -> str:
        prefix = f"mcp__{self.SERVER}__"
        return name[len(prefix):] if name.startswith(prefix) else name

    async def run_turn(self, prompt: str, emit: Emit) -> StepResult:
        self.proc = await asyncio.create_subprocess_exec(
            *self.command(prompt), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)
        result = StepResult()
        names: dict[str, str] = {}
        assert self.proc.stdout is not None
        try:
            async for raw in self.proc.stdout:
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._relay(ev, emit, result, names)
        finally:
            rc = await self.proc.wait()
            if rc not in (0, None) and not result.error:
                err = (await self.proc.stderr.read()).decode("utf-8", "replace")[-800:] if self.proc.stderr else ""
                result.error = f"claude exited with {rc}: {err.strip()}"
            self.proc = None
        return result

    async def _relay(self, ev: dict[str, Any], emit: Emit, result: StepResult, names: dict[str, str]) -> None:
        kind = ev.get("type")
        if ev.get("session_id"):
            self.session_id = ev["session_id"]
        if kind == "system" and ev.get("subtype") == "init":
            bad = [s.get("name") for s in ev.get("mcp_servers", []) if s.get("status") not in ("connected", None)]
            if bad:
                await emit({"type": "notice", "text": f"Claude Code could not start MCP server(s): {bad}"})
        elif kind == "assistant" and not ev.get("parent_tool_use_id"):
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text"):
                    result.text += b["text"]
                    await emit({"type": "text_delta", "text": b["text"]})
                elif b.get("type") == "tool_use":
                    names[b.get("id", "")] = self.strip(b.get("name", ""))
                    await emit({"type": "tool_call", "id": b.get("id"), "name": self.strip(b.get("name", "")),
                                "args": b.get("input") or {}})
        elif kind == "user" and not ev.get("parent_tool_use_id"):
            content = (ev.get("message") or {}).get("content") or []
            for b in content if isinstance(content, list) else []:
                if b.get("type") != "tool_result":
                    continue
                c = b.get("content")
                if isinstance(c, list):
                    has_image = any(x.get("type") == "image" for x in c if isinstance(x, dict))
                    c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                    if has_image:
                        c = (c + "\n[image]").strip()
                tid = b.get("tool_use_id", "")
                await emit({"type": "tool_result", "id": tid, "name": names.get(tid, ""),
                            "ok": not b.get("is_error"), "result": str(c or "")[:MAX_RESULT_CHARS]})
        elif kind == "result":
            result.stop_reason = ev.get("subtype") or "result"
            if ev.get("is_error"):
                result.error = str(ev.get("result") or ev.get("subtype") or "claude reported an error")
            if ev.get("total_cost_usd") is not None:
                await emit({"type": "notice", "text": f"Claude Code turn cost ${ev['total_cost_usd']:.4f}"})

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()

    async def close(self) -> None:
        await self.stop()


PROVIDERS: dict[str, type[Provider]] = {p.id: p for p in (AnthropicProvider, OpenAICompatProvider,
                                                          ClaudeCodeProvider, MockProvider)}
