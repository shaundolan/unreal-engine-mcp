"""Live editor state as MCP resources, and the agent-pack slash commands as MCP prompts.

  unreal://project        engine version, project, current level, plugins, maps (project_context)
  unreal://level/summary  actor count, top classes, folders, bounds, lights, cameras (scene_brief)
  unreal://selection      selected actors, Content Browser selection and folder
Each read queries the running editor, so the content is always current. Errors come back as JSON
({"success": false, "error": ...}) rather than failing the read, so clients can show them.

The workflows in commands/*.md (the Claude Code plugin's slash commands) are also served as prompts
named after the file (unreal-status, unreal-playtest, ...), so other MCP clients get them too.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import call
from .python_exec import run_editor_script, run_python

_COMMANDS_DIR = Path(__file__).resolve().parents[3] / "commands"

SELECTION_CODE = r"""
sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = []
for a in sub.get_selected_level_actors()[:200]:
    loc = a.get_actor_location()
    actors.append({"label": a.get_actor_label(), "name": a.get_name(), "class": a.get_class().get_name(),
                   "location": [round(loc.x, 1), round(loc.y, 1), round(loc.z, 1)]})
assets = []
try:
    for d in unreal.EditorUtilityLibrary.get_selected_asset_data()[:200]:
        assets.append({"path": str(d.package_name), "class": str(d.asset_class_path.asset_name)})
except Exception as e:
    assets = [{"error": str(e)}]
folder = ""
try:
    folder = unreal.EditorUtilityLibrary.get_current_content_browser_path()
except Exception:
    pass
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
result = {"level": world.get_path_name().split(".")[0] if world else "", "selected_actors": actors,
          "selected_assets": assets, "content_browser_path": folder}
"""


def _json(d: Any) -> str:
    return json.dumps(d, indent=1, default=str)


def selection(get_conn) -> dict[str, Any]:
    r = run_python(get_conn, SELECTION_CODE, timeout_s=20)
    if not r.get("success"):
        return {"success": False, "error": r.get("error") or r.get("traceback")}
    return {"success": True, **(r.get("result") or {})}


def commands_dir() -> Path:
    return Path(os.environ.get("UNREAL_MCP_COMMANDS_DIR") or _COMMANDS_DIR)


def load_commands(root: Path | None = None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    root = root or commands_dir()
    if not root.is_dir():
        return out
    for f in sorted(root.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
        meta, body = (m.group(1), m.group(2)) if m else ("", text)
        d = re.search(r"^description:\s*(.*)$", meta, re.M)
        out[f.stem] = {"description": d.group(1).strip().strip('"') if d else f.stem, "body": body.strip()}
    return out


def register(mcp, get_conn):
    if not hasattr(mcp, "resource") or not hasattr(mcp, "prompt"):
        return  # tool-only hosts (tests, bench)

    @mcp.resource("unreal://project", name="unreal-project", mime_type="application/json",
                  description="Live: engine version, project, current level, enabled plugins, maps")
    def project_resource() -> str:
        return _json(call(get_conn, "project_context"))

    @mcp.resource("unreal://level/summary", name="unreal-level-summary", mime_type="application/json",
                  description="Live: the open level's actor count, top classes, folders, bounds, lights, cameras")
    def level_summary_resource() -> str:
        return _json(run_editor_script(get_conn, "scene_brief", {"top_classes": 25}))

    @mcp.resource("unreal://selection", name="unreal-selection", mime_type="application/json",
                  description="Live: selected actors, Content Browser selection and current folder")
    def selection_resource() -> str:
        return _json(selection(get_conn))

    def add_prompt(name: str, desc: str, body: str):
        def prompt(arguments: str = "") -> str:
            return body.replace("$ARGUMENTS", arguments or "(none)")
        prompt.__name__ = name.replace("-", "_")
        mcp.prompt(name=name, description=desc[:300])(prompt)

    for name, c in load_commands().items():
        add_prompt(name, c["description"], c["body"])
