"""Which tool calls need the user's confirmation before the agent runner executes them.

The classifier looks at the tool name and its arguments and returns a human-readable reason when the
call is destructive: it deletes assets, overwrites files or maps on disk, or throws away unsaved work.
Undoable actor edits (spawn, move, delete inside one scene_compose) are not destructive by default,
because "Undo last agent turn" reverts them; ``strict`` mode asks for those too.

execute_python is opaque, so it is matched against patterns for the editor APIs that delete or
overwrite things. That is a heuristic, not a sandbox: the runner documents it as such.
"""

from __future__ import annotations

import re
from typing import Any

MODES = ("off", "destructive", "strict", "all")

_PY_DESTRUCTIVE = re.compile(
    r"\b(delete_asset|delete_directory|delete_loaded_asset|delete_loaded_assets|delete_assets|"
    r"consolidate_assets|rename_asset|rename_directory|EditorAssetLibrary\.delete|"
    r"AssetToolsHelpers.*\.delete|save_current_level|save_map|save_package|"
    r"new_level|load_level|ObjectTools\.delete|"
    r"shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|Path\([^)]*\)\.unlink|\.unlink\(|rmtree)\b")
_PY_ACTOR_DELETE = re.compile(r"\b(destroy_actor|destroy_actors|delete_selected|K2_DestroyActor)\b")

# Tools whose every call is fine without asking (read-only or trivially reversible).
READ_ONLY = frozenset({
    "unreal_status", "project_context", "editor_log", "viewport_capture", "console_command_readonly",
    "unreal_api_search", "unreal_api_doc", "scene_query", "scene_brief", "actor_inspect", "level_inspect",
    "scene_assert", "search_assets", "asset_references", "material_inspect", "bp_export", "widget_export",
    "bt_export", "landscape_inspect", "foliage_inspect", "pcg_inspect", "spline_inspect", "anim_inspect",
    "animbp_export", "gas_inspect", "frame_stats", "performance_audit", "package_status", "blender_status",
    "wait_for_shaders", "object_describe", "property_get", "epic_list_toolsets", "epic_describe_toolset",
    "bp_explain", "bp_review",
})
EPIC_META = ("epic_list_toolsets", "epic_describe_toolset", "epic_call_tool")


def _ops(args: dict[str, Any]) -> list[dict[str, Any]]:
    ops = args.get("operations")
    return [o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else []


def classify(tool: str, args: dict[str, Any] | None, mode: str = "destructive") -> str | None:
    """Return why this call needs confirmation under ``mode``, or None to run it straight away."""
    if mode == "off":
        return None
    args = args or {}
    # The epic_* gateway (tools/epic.py) forwards to Epic's native tools: judge the Epic tool it names.
    if tool == "epic_call_tool":
        return classify_native(str(args.get("tool_name", "")), args.get("arguments"), mode)
    if tool.startswith("epic_") and tool not in EPIC_META:  # UNREAL_MCP_EPIC_MODE=expand: epic_<Toolset>_<tool>
        return classify_native(tool[len("epic_"):], args, mode)
    if mode == "all" and tool not in READ_ONLY:
        return f"{tool} changes the project (confirm-all mode)"
    reasons: list[str] = []
    ops = _ops(args)
    op_names = [str(o.get("op", "")).lower() for o in ops]

    if tool == "asset_manage":
        deletes = [o.get("path") for o in ops if str(o.get("op", "")).lower() == "delete"]
        if deletes:
            reasons.append(f"permanently deletes {len(deletes)} asset(s): {', '.join(map(str, deletes[:5]))}"
                           " (asset deletion is not undoable)")
    if args.get("discard_changes") is True:
        reasons.append("discards unsaved changes in the current level")
    if tool == "asset_import" and args.get("replace_existing") is True:
        reasons.append("overwrites existing assets on import")
    if tool == "cpp_source" and str(args.get("action", "")).lower() == "write":
        reasons.append(f"overwrites source file {args.get('path')!r} on disk")
    if tool in ("material_edit", "pcg_graph") and args.get("clear") is True:
        reasons.append(f"clear=true wipes the existing {tool.split('_')[0]} graph")
    if tool == "landscape_edit" and "delete" in op_names:
        reasons.append("deletes the landscape")
    if tool == "foliage_scatter" and "clear" in op_names:
        reasons.append("clears all foliage and HISM scatter instances")
    if tool == "package_project" and args.get("clean") is True:
        reasons.append("clean build deletes previous build output")
    if tool == "execute_python":
        code = str(args.get("code", ""))
        m = _PY_DESTRUCTIVE.search(code)
        if m:
            reasons.append(f"Python calls {m.group(0)!r} (deletes, renames, saves over or replaces content)")
        elif mode == "strict" and _PY_ACTOR_DELETE.search(code):
            reasons.append("Python destroys actors")
    if mode == "strict":
        if tool in ("scene_compose", "spline_edit") and "delete" in op_names:
            reasons.append(f"deletes {op_names.count('delete')} actor(s) (undoable)")
        if tool == "editor_action" and args.get("action") in ("load_level", "save_all"):
            reasons.append(f"editor_action {args.get('action')}")
        if tool == "level_open":
            reasons.append("switches the open map (clears the undo history)")
        if tool in ("bp_apply", "animbp_apply", "widget_apply") and any(n.startswith("remove") for n in op_names):
            reasons.append("removes Blueprint nodes/variables")
    return "; ".join(reasons) or None


_NATIVE_RISKY = re.compile(r"(delete|remove|destroy|clear|overwrite|save|rename|move|replace|reset|discard)", re.I)


def classify_native(tool: str, args: dict[str, Any] | None, mode: str = "destructive") -> str | None:
    """Epic's native MCP tools: names and semantics vary by toolset, so judge by the tool name."""
    if mode == "off":
        return None
    if mode in ("strict", "all"):
        return f"Epic native tool {tool} ({mode} mode confirms every native call)"
    m = _NATIVE_RISKY.search(tool)
    return f"Epic native tool {tool} may {m.group(1).lower()} content" if m else None


def claude_code_tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"
