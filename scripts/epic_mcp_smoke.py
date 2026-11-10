"""Smoke test of Epic's native editor MCP server with the UnrealMCPToolsets plugin loaded.

    cd Python && uv run python ../scripts/epic_mcp_smoke.py [--url http://127.0.0.1:8568/mcp]

Initializes a session, lists tools (works in tool-search and eager mode), checks the UnrealMCP.* toolsets are
there, then calls some of ours (UnrealMCP.Editor.project_context, UnrealMCP.Scene.scene_brief,
UnrealMCP.Play.frame_stats) and of Epic's (EditorAppToolset.IsPIERunning, SceneTools.get_current_level).
Exit code 0 = all passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Python"))

from unreal_mcp.epic_client import EpicMCPClient, content_text  # noqa: E402
from unreal_mcp.tools.epic import EpicGateway  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    a = ap.parse_args()
    client = EpicMCPClient(a.url, timeout=120)
    info = client.initialize()
    print("server:", info.get("serverInfo"), "protocol:", info.get("protocolVersion"))
    tools = client.list_tools()
    names = [t["name"] for t in tools]
    mode = "tool_search" if "list_toolsets" in names else "eager"
    print(f"tools/list: {len(names)} tools ({mode})")
    gw = EpicGateway(client)
    toolsets = gw.toolsets()
    ours = sorted(t for t in toolsets if t.startswith("UnrealMCP."))
    print(f"toolsets: {len(toolsets)} total, ours: {ours}")
    if mode == "eager":
        print("our tools in tools/list:", sorted(n for n in names if n.startswith("UnrealMCP.")))
    ok = len(ours) == 8

    def call(ts: str, tool: str, args: dict | None = None, expect_error: bool = False):
        nonlocal ok
        t0 = time.monotonic()
        try:
            out = gw.call(ts, tool, args or {}, 120)
        except Exception as e:  # noqa: BLE001 (smoke script: report and continue)
            ok = ok and expect_error
            print(f"{'ok  ' if expect_error else 'FAIL'} {ts}.{tool} -> error: {str(e)[:300]}")
            return None
        if expect_error:
            ok = False
            print(f"FAIL {ts}.{tool}: expected an error")
        text = json.dumps(out) if not isinstance(out, str) else out
        print(f"ok   {ts}.{tool} ({time.monotonic() - t0:.2f}s): {text[:300]}")
        return out

    call("UnrealMCP.Editor", "project_context")
    call("UnrealMCP.Scene", "scene_brief", {"top_classes": 5})
    call("UnrealMCP.Play", "frame_stats", {"frames": 30, "timeout_s": 30})
    call("UnrealMCP.Blueprint", "bp_export", {"blueprint": "/Game/__does_not_exist__"}, expect_error=True)
    epic_editor = next((t for t in toolsets if t.endswith("EditorAppToolset")), "")
    epic_scene = next((t for t in toolsets if t.endswith("SceneTools")), "")
    if epic_editor:
        call(epic_editor, "IsPIERunning")
    if epic_scene:
        call(epic_scene, "get_current_level")
    if not (epic_editor or epic_scene):
        ok = False
        print("FAIL: no EditorToolset toolsets (enable the EditorToolset plugin)")
    if mode == "tool_search":
        print("describe_toolset UnrealMCP.Blueprint:",
              content_text(client.call_tool("describe_toolset", {"toolset_name": "UnrealMCP.Blueprint"}))[:200])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
