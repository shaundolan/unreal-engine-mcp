"""Permission-prompt MCP server for the agent runner's Claude Code mode.

`claude -p --permission-prompt-tool mcp__unrealmcp_agent__approve` calls this server's ``approve`` tool
for every tool call that is not pre-allowed. It forwards the request to the agent runner over its
WebSocket (/approve), which applies the same confirmation rules as the in-process providers and, for a
destructive call, asks the user in the editor panel. The reply is the JSON Claude Code expects:
{"behavior": "allow", "updatedInput": {...}} or {"behavior": "deny", "message": "..."}.

    python -m unreal_mcp.agent_permission --url ws://127.0.0.1:55600/approve?token=...&session=...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from . import ws


class Approver:
    def __init__(self, url: str):
        self.url = url
        self.sock: ws.WebSocket | None = None
        self.lock = asyncio.Lock()

    async def ask(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            for attempt in (0, 1):
                try:
                    if self.sock is None or self.sock.closed:
                        self.sock = await ws.connect(self.url)
                    rid = uuid.uuid4().hex
                    await self.sock.send(json.dumps({"id": rid, "tool_name": tool_name, "input": tool_input}))
                    reply = json.loads(await self.sock.recv())
                    break
                except (ws.ConnectionClosed, OSError) as e:
                    self.sock = None
                    if attempt:
                        return {"behavior": "deny", "message": f"agent runner unreachable: {e}"}
        if reply.get("allow"):
            return {"behavior": "allow", "updatedInput": tool_input}
        return {"behavior": "deny", "message": reply.get("message") or "denied"}


def build_server(url: str):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("unrealmcp_agent")
    approver = Approver(url)

    @mcp.tool()
    async def approve(tool_name: str, input: dict[str, Any] | None = None, tool_use_id: str = "") -> str:
        """Permission prompt for Claude Code: asks the UnrealMCP agent runner whether this tool call may run."""
        return json.dumps(await approver.ask(tool_name, input or {}))

    return mcp


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="unreal_mcp.agent_permission")
    p.add_argument("--url", required=True)
    args = p.parse_args(argv)
    build_server(args.url).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
