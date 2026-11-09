"""Gateway to Epic's native editor MCP server (UE 5.8): re-expose its toolsets through UnrealMCP.

Off unless UNREAL_MCP_EPIC_URL is set (e.g. http://127.0.0.1:8000/mcp, the editor's ModelContextProtocol plugin).
Then a client connected to UnrealMCP gets both: our tools, plus Epic's ~870 toolset tools (Niagara, PCG, UMG,
Blueprint DSL, Control Rig, Sequencer, SlateInspector, ...) behind a prefix.

* meta mode (default): ``epic_list_toolsets``, ``epic_describe_toolset``, ``epic_call_tool``. Discovery is lazy,
  so the editor may start after this server, and the tool list stays small (Epic's own default works the same way).
* expand mode: additionally registers every Epic toolset tool as its own tool (``epic_<Toolset>_<tool>``) at
  startup, with Epic's input schema; restricted with UNREAL_MCP_EPIC_TOOLSETS. Needs the editor running at startup.

Works whether Epic's server runs with tool search (default: tools/list = list_toolsets/describe_toolset/call_tool)
or eagerly (bEnableToolSearch=False: every tool listed as "<Toolset>.<tool>").
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mcp.server.fastmcp.tools.base import Tool as _FastMCPTool
from mcp.server.fastmcp.utilities.func_metadata import func_metadata
from mcp.types import ImageContent, TextContent

from ..config import Settings
from ..epic_client import EpicMCPClient, EpicMCPError, content_text

logger = logging.getLogger("UnrealMCP.epic")

META = ("list_toolsets", "describe_toolset", "call_tool")
OURS = "unrealmcp."


def _convert(result: dict[str, Any]) -> Any:
    """Epic tool result -> FastMCP return value (dict for a JSON text reply, else content items)."""
    if result.get("isError"):
        raise RuntimeError(content_text(result) or "Epic tool failed")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    items: list[TextContent | ImageContent] = []
    for c in result.get("content", []):
        if c.get("type") == "image":
            items.append(ImageContent(type="image", data=c.get("data", ""), mimeType=c.get("mimeType", "image/png")))
        elif c.get("type") == "text":
            items.append(TextContent(type="text", text=c.get("text", "")))
    if len(items) == 1 and isinstance(items[0], TextContent):
        try:
            value = json.loads(items[0].text)
        except ValueError:
            return items[0].text
        image = _toolset_image(value)
        return [image] if image else value
    return items


def _toolset_image(value: Any) -> ImageContent | None:
    """Epic's ToolsetRegistry adapter returns FToolsetImage ({MimeType, Data}) as JSON *text*; hand the model a real
    image instead (also when wrapped as {"result": {...}} or {"ReturnValue": {...}})."""
    if isinstance(value, dict) and len(value) == 1:
        inner = next(iter(value.values()))
        if isinstance(inner, dict):
            value = inner
    if not isinstance(value, dict):
        return None
    keys = {k.lower(): v for k, v in value.items()}
    mime, data = keys.get("mimetype"), keys.get("data")
    if isinstance(mime, str) and mime.startswith("image/") and isinstance(data, str) and data:
        return ImageContent(type="image", data=data, mimeType=mime)
    return None


class EpicGateway:
    def __init__(self, client: EpicMCPClient):
        self.client = client

    def _native(self) -> dict[str, dict[str, Any]]:
        return {t["name"]: t for t in self.client.list_tools()}

    @staticmethod
    def parse_catalog(text: str) -> dict[str, str]:
        """list_toolsets text ("- Name: description" lines) -> {name: description}."""
        out = {}
        for line in text.splitlines():
            if line.startswith("- "):
                name, _, desc = line[2:].partition(": ")
                out[name.strip()] = desc.strip()
        return out

    def toolsets(self) -> dict[str, str]:
        native = self._native()
        if "list_toolsets" in native:
            return self.parse_catalog(content_text(self.client.call_tool("list_toolsets")))
        counts: dict[str, int] = {}
        for name in native:
            ts = name.rpartition(".")[0]
            if ts:
                counts[ts] = counts.get(ts, 0) + 1
        return {ts: f"{n} tools" for ts, n in sorted(counts.items())}

    def describe(self, toolset: str) -> dict[str, Any]:
        native = self._native()
        if "describe_toolset" in native:
            r = self.client.call_tool("describe_toolset", {"toolset_name": toolset})
            if r.get("isError"):
                raise RuntimeError(content_text(r))
            return json.loads(content_text(r))
        tools = [t for n, t in native.items() if n.rpartition(".")[0] == toolset]
        if not tools:
            raise RuntimeError(f"toolset {toolset!r} not found")
        return {"name": toolset, "tools": tools}

    def call(self, toolset: str, tool: str, arguments: dict[str, Any] | None, timeout_s: float) -> Any:
        native = self._native()
        if "call_tool" in native and toolset:
            r = self.client.call_tool("call_tool", {"toolset_name": toolset, "tool_name": tool,
                                                    "arguments": arguments or {}}, timeout=timeout_s)
        else:
            r = self.client.call_tool(f"{toolset}.{tool}" if toolset else tool, arguments, timeout=timeout_s)
        return _convert(r)


class _ProxyTool(_FastMCPTool):
    """A FastMCP tool whose schema is Epic's and whose arguments pass straight through (no pydantic model)."""

    async def run(self, arguments: dict[str, Any], context: Any = None) -> Any:
        return self.fn(arguments)


def _noop() -> None:
    return None


def safe_name(prefix: str, toolset: str, tool: str, taken: set[str]) -> str:
    """Client-safe tool name (^[A-Za-z0-9_-]{1,64}$): prefix + last toolset segment + tool."""
    base = re.sub(r"[^A-Za-z0-9_-]", "_", f"{prefix}{toolset.rpartition('.')[2]}_{tool}")[:64]
    name, i = base, 2
    while name in taken:
        suffix = f"_{i}"
        name, i = base[:64 - len(suffix)] + suffix, i + 1
    taken.add(name)
    return name


def expand(mcp, gateway: EpicGateway, prefix: str, filters: tuple[str, ...]) -> list[str]:
    """Register every (filtered) Epic toolset tool as its own FastMCP tool. Returns the registered names."""
    added: list[str] = []
    taken = {t.name for t in mcp._tool_manager.list_tools()}
    for toolset in gateway.toolsets():
        low = toolset.lower()
        if (filters and not any(f.lower() in low for f in filters)) or (not filters and low.startswith(OURS)):
            continue
        for t in gateway.describe(toolset).get("tools", []):
            full = t["name"]
            short = full[len(toolset) + 1:] if full.startswith(toolset + ".") else full.rpartition(".")[2]
            name = safe_name(prefix, toolset, short, taken)

            def forward(arguments: dict[str, Any], _ts=toolset, _tool=short) -> Any:
                return gateway.call(_ts, _tool, arguments, 600.0)

            mcp._tool_manager._tools[name] = _ProxyTool(
                fn=forward, name=name, description=f"[Epic {full}] {t.get('description', '')}".strip(),
                parameters=t.get("inputSchema") or {"type": "object", "properties": {}},
                fn_metadata=func_metadata(_noop), is_async=False)
            added.append(name)
    return added


def register(mcp, get_conn, settings: Settings | None = None, client: EpicMCPClient | None = None):
    settings = settings or Settings.from_env()
    if not settings.epic_url:
        return
    gateway = EpicGateway(client or EpicMCPClient(settings.epic_url))
    p = settings.epic_prefix

    def _err(e: Exception) -> dict[str, Any]:
        return {"success": False, "error": f"Epic MCP server at {settings.epic_url}: {e}. Start the editor with "
                "-ModelContextProtocolStartServer (and ModelContextProtocol enabled); see docs/EPIC_NATIVE_MCP.md."}

    @mcp.tool(name=f"{p}list_toolsets")
    def epic_list_toolsets() -> dict[str, Any]:
        """List the toolsets of Epic's native editor MCP server (UE 5.8 ModelContextProtocol: EditorToolset,
        Niagara, PCG, UMG, StateTree, SlateInspector, ... plus any UnrealMCP.* toolsets). Then
        epic_describe_toolset for a toolset's tools and schemas, and epic_call_tool to run one."""
        try:
            return {"success": True, "toolsets": gateway.toolsets()}
        except (EpicMCPError, OSError, ValueError) as e:
            return _err(e)

    @mcp.tool(name=f"{p}describe_toolset")
    def epic_describe_toolset(toolset_name: str) -> dict[str, Any]:
        """Tools of one Epic toolset: full names, descriptions and JSON input schemas."""
        try:
            return {"success": True, **gateway.describe(toolset_name)}
        except (EpicMCPError, OSError, ValueError, RuntimeError) as e:
            return _err(e)

    @mcp.tool(name=f"{p}call_tool")
    def epic_call_tool(tool_name: str, toolset_name: str = "", arguments: dict[str, Any] | None = None,
                       timeout_s: float = 600.0) -> Any:
        """Call a tool on Epic's native editor MCP server. toolset_name from epic_list_toolsets, tool_name
        without the toolset prefix (e.g. toolset_name="EditorToolset.EditorAppToolset", tool_name="IsPIERunning"),
        arguments per epic_describe_toolset. Without toolset_name, calls a top-level Epic MCP tool."""
        try:
            return gateway.call(toolset_name, tool_name, arguments, timeout_s)
        except (EpicMCPError, OSError) as e:
            return _err(e)

    if settings.epic_mode == "expand":
        try:
            client_ = gateway.client
            old, client_.timeout = client_.timeout, 3.0
            try:
                names = expand(mcp, gateway, p, settings.epic_toolsets)
            finally:
                client_.timeout = old
            logger.info("expanded %d Epic tools from %s", len(names), settings.epic_url)
        except (EpicMCPError, OSError, ValueError, RuntimeError) as e:
            logger.warning("Epic MCP expand skipped (%s): only the epic_* meta tools are available", e)
