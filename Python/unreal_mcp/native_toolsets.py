"""Manifest of UnrealMCP tools exposed as Epic ToolsetRegistry toolsets (UE 5.8 native MCP server).

The UnrealMCPToolsets editor plugin reads ``UnrealMCPToolsets/Resources/UnrealMCPToolsets.json`` and registers one
``FToolset`` per entry with Epic's ToolsetRegistry, so the tools appear in the editor's own MCP server
(``http://127.0.0.1:<port>/mcp``) and in Epic's AI Assistant. The manifest is GENERATED from this server's FastMCP
tool definitions (name, description, input schema), so both front doors share one schema and one implementation:

* ``command`` tools call the same C++ command handler (``UEpicUnrealMCPBridge`` registry) the TCP server uses;
* ``editor_script`` tools run the same ``editor_scripts/<name>.py`` text ``run_editor_script`` sends;
* ``frame_stats`` is the one async tool (start, poll every frame on the editor ticker, resolve).

Regenerate after changing a curated tool:  ``cd Python && uv run python -m unreal_mcp.native_toolsets``
(``tests/test_native_toolsets.py`` fails when the committed manifest drifts or a backend stops matching the
Python tool's own call).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "UnrealMCPToolsets" / "Resources" / "UnrealMCPToolsets.json"
VERSION = "0.3.0"


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str = "command"            # command | editor_script | frame_stats
    target: str = ""                 # command / script name (default: the tool name)
    rename: dict[str, str] = field(default_factory=dict)   # tool arg -> command/script param
    ok_flag: bool = False            # result carries "ok": false on failure (bp_apply & co)
    timeout_s: float = 120.0


@dataclass(frozen=True)
class Toolset:
    name: str
    description: str
    tools: tuple[Tool, ...]


# Tools whose Python wrapper is a pure passthrough (after defaults, None-dropping and renames) to one C++ command
# or one editor script. Orchestrated tools (pie_test, landscape_edit, anim_edit, package_project, the legacy world
# builders) need several round trips and are not here yet: see docs/EPIC_NATIVE_MCP.md.
CURATED: tuple[Toolset, ...] = (
    Toolset("UnrealMCP.Blueprint",
            "UnrealMCP whole-Blueprint authoring: export a Blueprint or Animation Blueprint as a JSON graph spec and "
            "apply a batch of edits (variables, components, functions, nodes, pins, links, AnimGraph state machines) "
            "atomically in one undo step, compiled, with structured errors and dry_run.",
            (Tool("bp_export"), Tool("bp_apply", ok_flag=True, timeout_s=300),
             Tool("animbp_export"), Tool("animbp_apply", ok_flag=True, timeout_s=300))),
    Toolset("UnrealMCP.UI",
            "UnrealMCP UMG authoring: export and apply whole widget trees with canvas anchors and slots in one call.",
            (Tool("widget_export"), Tool("widget_apply", ok_flag=True, timeout_s=300))),
    Toolset("UnrealMCP.AI",
            "UnrealMCP AI authoring: Blackboard keys plus a whole Behavior Tree from one nested spec, and export.",
            (Tool("bt_export"), Tool("bt_build", ok_flag=True))),
    Toolset("UnrealMCP.Scene",
            "UnrealMCP level tools: compact level brief, actor inspection, level structure, and scene_compose "
            "(batched spawn/modify/delete with grid/circle/line patterns in ONE undo step).",
            (Tool("scene_brief", "editor_script"), Tool("actor_inspect", "editor_script"),
             Tool("level_inspect", "editor_script"), Tool("scene_compose", "editor_script", timeout_s=300))),
    Toolset("UnrealMCP.Assets",
            "UnrealMCP asset search and dependency/referencer graphs.",
            (Tool("search_assets", "editor_script", rename={"class_name": "class"}),
             Tool("asset_references", "editor_script"))),
    Toolset("UnrealMCP.Editor",
            "UnrealMCP editor control: project context, safe level switching, console commands, Output Log tail "
            "with a cursor.",
            (Tool("project_context"), Tool("level_open"), Tool("console_command"), Tool("editor_log"))),
    Toolset("UnrealMCP.Reflection",
            "UnrealMCP reflection: describe any UObject/UClass, get/set any UPROPERTY (undoable), call any UFUNCTION.",
            (Tool("object_describe", rename={"class_name": "class"}), Tool("property_get"), Tool("property_set"),
             Tool("function_call"))),
    Toolset("UnrealMCP.Play",
            "UnrealMCP runtime: inject real key / Enhanced Input action presses into a running PIE session and "
            "measure frame, game, render, RHI and GPU times.",
            (Tool("pie_input"), Tool("frame_stats", "frame_stats", timeout_s=60))),
)


def _schema_tools() -> dict[str, Any]:
    """name -> MCP Tool from the real server (the single source of descriptions and input schemas)."""
    import importlib

    server = importlib.import_module("unreal_mcp_server_advanced")
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def editor_script_body(name: str) -> str:
    """The rendered editor script without its leading ARGS line (the plugin prepends ARGS per call)."""
    from .editor_scripts import render

    first, _, rest = render(name, {}).partition("\n")
    assert first.startswith("ARGS = "), first
    return rest


def build_manifest() -> dict[str, Any]:
    from .tools.python_exec import _WRAPPER

    schemas = _schema_tools()
    toolsets = []
    for ts in CURATED:
        tools = []
        for t in ts.tools:
            mcp_tool = schemas[t.name]
            backend: dict[str, Any] = {"kind": t.kind, "timeout_s": t.timeout_s}
            if t.kind == "editor_script":
                backend["script"] = editor_script_body(t.target or t.name)
            elif t.kind == "command":
                backend["command"] = t.target or t.name
            if t.rename:
                backend["rename"] = dict(t.rename)
            if t.ok_flag:
                backend["ok_flag"] = True
            tools.append({"name": t.name, "description": (mcp_tool.description or "").strip(),
                          "inputSchema": mcp_tool.inputSchema, "backend": backend})
        toolsets.append({"name": ts.name, "description": ts.description, "tools": tools})
    return {"version": VERSION, "generated_by": "python -m unreal_mcp.native_toolsets",
            "python_wrapper": _WRAPPER, "toolsets": toolsets}


def dumps(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=1, sort_keys=True) + "\n"


def native_params(tool: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """What the plugin sends to the command / script for these tool arguments (mirror of FUnrealMCPToolset::
    PrepareParams): schema defaults filled in, nulls dropped, renames applied."""
    props = tool["inputSchema"].get("properties", {})
    merged = {k: v["default"] for k, v in props.items() if "default" in v}
    merged.update(arguments)
    rename = tool["backend"].get("rename", {})
    return {rename.get(k, k): v for k, v in merged.items() if v is not None}


def main() -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(dumps(build_manifest()), encoding="utf-8")
    m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    n = sum(len(ts["tools"]) for ts in m["toolsets"])
    print(f"wrote {MANIFEST_PATH} ({len(m['toolsets'])} toolsets, {n} tools)")


if __name__ == "__main__":
    main()
