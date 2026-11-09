"""Editor control and introspection: status, project context, actions, console, world switching, shader queue."""

from __future__ import annotations

import time
from typing import Any

from . import call

EDITOR_ACTIONS = ("undo", "redo", "save_all", "open_asset", "load_level", "select_actors",
                  "focus_actor", "pie_start", "pie_stop")


def _save_result(r: dict[str, Any]) -> dict[str, Any]:
    """save_all: any package that failed to save makes the call fail (the plugin reports per package)."""
    if r.get("success") and r.get("failed"):
        r["success"] = False
        r["error"] = "could not save: " + "; ".join(f"{f.get('package')}: {f.get('error')}" for f in r["failed"][:8])
    return r


def poll_shaders(get_conn, timeout_s: float = 600.0, poll_s: float = 1.0, settle_polls: int = 2,
                     include_assets: bool = True) -> dict[str, Any]:
    """Poll shader_status until the shader (and asset build) queues are empty for settle_polls polls in a row."""
    t0 = time.monotonic()
    quiet, polls, peak = 0, 0, 0
    last: dict[str, Any] = {}
    while True:
        r = call(get_conn, "shader_status")
        if not r.get("success"):
            return r
        polls += 1
        last = r
        busy = r.get("shader_jobs", 0) + (r.get("asset_builds", 0) if include_assets else 0)
        peak = max(peak, busy)
        idle = busy == 0 and not r.get("shaders_compiling")
        quiet = quiet + 1 if idle else 0
        waited = round(time.monotonic() - t0, 2)
        if quiet >= max(1, settle_polls):
            return {"success": True, "idle": True, "waited_s": waited, "polls": polls, "peak_jobs": peak,
                    "shader_jobs": 0, "asset_builds": r.get("asset_builds", 0)}
        if waited >= timeout_s:
            return {"success": False, "idle": False, "timed_out": True, "waited_s": waited, "polls": polls,
                    "shader_jobs": last.get("shader_jobs"), "asset_builds": last.get("asset_builds"),
                    "error": f"shaders still compiling after {timeout_s:.0f}s ({last.get('shader_jobs')} jobs left)"}
        time.sleep(poll_s)


def register(mcp, get_conn):
    @mcp.tool()
    def unreal_status() -> dict[str, Any]:
        """Check the editor connection and list the commands the loaded plugin supports.
        Call this first in a session. Older plugins without list_commands still answer ping."""
        ping = call(get_conn, "ping")
        if not ping.get("success"):
            return ping
        cmds = call(get_conn, "list_commands")
        return {"success": True, "connected": True, "plugin_version": cmds.get("plugin_version", "legacy"),
                "python_available": cmds.get("python_available"),
                "commands": cmds.get("commands", []) if cmds.get("success") else []}

    @mcp.tool()
    def project_context() -> dict[str, Any]:
        """Engine version, project name/dir, current level, enabled plugins, maps,
        and the path of the generated Python API stub (for unreal_api_build_index)."""
        return call(get_conn, "project_context")

    @mcp.tool()
    def editor_action(action: str, asset_path: str = "", level_path: str = "",
                      actor_names: list[str] | None = None, actor_name: str = "",
                      count: int = 1, discard_changes: bool = False) -> dict[str, Any]:
        """Perform an editor action.

        action: one of undo, redo (count = number of steps), save_all, open_asset (asset_path),
                load_level (level_path, e.g. "/Game/Maps/Arena"), select_actors (actor_names),
                focus_actor (actor_name), pie_start, pie_stop.
        save_all never prompts (no checkout / make-writable dialogs), saves maps too (also a map
        made with new_level that was never written) and returns saved_packages, failed
        [{package, error}] and skipped (untitled /Temp maps); any failure makes success false.
        load_level is level_open without create: it refuses if the current level has unsaved
        changes unless discard_changes=true (save_all first to keep them).
        Every mutating MCP command is its own transaction, so `undo` reverts the last one.
        """
        if action not in EDITOR_ACTIONS:
            return {"success": False, "error": f"unknown action {action!r}; expected one of {EDITOR_ACTIONS}"}
        r = call(get_conn, "editor_action", {
            "action": action, "asset_path": asset_path, "level_path": level_path,
            "actor_names": actor_names or [], "actor_name": actor_name, "count": count,
            "discard_changes": discard_changes}, timeout=600 if action in ("save_all", "load_level") else None)
        return _save_result(r) if action == "save_all" else r

    @mcp.tool()
    def level_open(path: str, create: bool = False, save_current: bool = False,
                   discard_changes: bool = False) -> dict[str, Any]:
        """Switch the editor to another map safely (use this instead of LevelEditorSubsystem.load_level /
        new_level in execute_python).

        path: "/Game/Maps/Arena". create=true makes the map with new_level and saves it when it
        does not exist yet. The current level must be saved first: save_current=true saves every
        dirty package (no prompts), discard_changes=true drops the edits.
        Before switching it drops Python references and collects garbage, and if the target's
        world is already in memory as a standalone copy (made by duplicate_asset, load_asset or an
        earlier new_level) it unloads that copy first: loading over it fatals the editor with
        "World Memory Leaks". Returns current_level, created, unloaded_stale_copy.
        """
        return call(get_conn, "level_open", {"path": path, "create": create, "save_current": save_current,
                                             "discard_changes": discard_changes}, timeout=600)

    @mcp.tool()
    def wait_for_shaders(timeout_s: float = 600.0, include_assets: bool = True) -> dict[str, Any]:
        """Block until the shader compile queue (and, with include_assets, texture/mesh builds) is
        empty. Call it before viewport_capture or frame timings after building materials or
        importing assets: until shaders finish, the editor draws the old shader map or a grey
        placeholder, so captures are stale. Returns waited_s, peak_jobs; success=false with
        timed_out=true if the queue is still busy after timeout_s."""
        return poll_shaders(get_conn, timeout_s=timeout_s, include_assets=include_assets)

    @mcp.tool()
    def console_command(command: str) -> dict[str, Any]:
        """Run an editor console command (e.g. "stat unit", "r.ScreenPercentage 50",
        "obj list class=StaticMesh") and return its captured output."""
        return call(get_conn, "console_command", {"command": command})
