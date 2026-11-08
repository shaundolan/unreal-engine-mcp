"""Editor control and introspection: status, project context, actions, console."""

from __future__ import annotations

from typing import Any

from . import call

EDITOR_ACTIONS = ("undo", "redo", "save_all", "open_asset", "load_level", "select_actors",
                  "focus_actor", "pie_start", "pie_stop")


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
                      count: int = 1) -> dict[str, Any]:
        """Perform an editor action.

        action: one of undo, redo (count = number of steps), save_all, open_asset (asset_path),
                load_level (level_path, e.g. "/Game/Maps/Arena"), select_actors (actor_names),
                focus_actor (actor_name), pie_start, pie_stop.
        Every mutating MCP command is its own transaction, so `undo` reverts the last one.
        """
        if action not in EDITOR_ACTIONS:
            return {"success": False, "error": f"unknown action {action!r}; expected one of {EDITOR_ACTIONS}"}
        return call(get_conn, "editor_action", {
            "action": action, "asset_path": asset_path, "level_path": level_path,
            "actor_names": actor_names or [], "actor_name": actor_name, "count": count})

    @mcp.tool()
    def console_command(command: str) -> dict[str, Any]:
        """Run an editor console command (e.g. "stat unit", "r.ScreenPercentage 50",
        "obj list class=StaticMesh") and return its captured output."""
        return call(get_conn, "console_command", {"command": command})
