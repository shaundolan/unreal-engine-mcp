"""Gameplay framework tools: Enhanced Input, game mode/maps, C++ source + Live Coding, perf audit."""

from __future__ import annotations

import re
import time
from typing import Any

from . import call
from .python_exec import run_editor_script

_LC_OK = re.compile(r"live ?coding.*(succeeded|successful|complete|no changes)", re.I)
_LC_FAIL = re.compile(r"(live ?coding.*fail)|(error [A-Z]+\d{3,5})|(: error\b)|(\bfatal error\b)", re.I)


def wait_for_live_coding(get_conn, since: int, timeout_s: float, sleep=time.sleep) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    seen: list[dict[str, Any]] = []
    cursor = since
    while time.monotonic() < deadline:
        log = call(get_conn, "editor_log", {"since_seq": cursor, "lines": 2000})
        for e in log.get("entries", []):
            cursor = max(cursor, int(e.get("seq", cursor)))
            msg = e.get("message", "")
            if "LiveCoding" in e.get("category", "") or _LC_FAIL.search(msg) or "live coding" in msg.lower():
                seen.append(e)
        errors = [e["message"] for e in seen if _LC_FAIL.search(e.get("message", ""))]
        if errors:
            return {"success": True, "compiled": False, "errors": errors[:50],
                    "log": [e["message"] for e in seen][-80:]}
        if any(_LC_OK.search(e.get("message", "")) for e in seen):
            return {"success": True, "compiled": True, "log": [e["message"] for e in seen][-40:]}
        sleep(1.0)
    return {"success": False, "error": f"no Live Coding result within {timeout_s}s",
            "log": [e["message"] for e in seen][-40:]}


def register(mcp, get_conn):
    register_packaging(mcp, get_conn)

    @mcp.tool()
    def enhanced_input(actions: list[dict[str, Any]] | None = None, contexts: list[dict[str, Any]] | None = None,
                       folder: str = "/Game/Input") -> dict[str, Any]:
        """Create/update Enhanced Input assets in one call.
        actions: [{"name":"IA_Move","value_type":"Axis2D"}, {"name":"IA_Jump","value_type":"Boolean"}]
        contexts: [{"name":"IMC_Default","mappings":[
            {"action":"IA_Jump","key":"SpaceBar"},
            {"action":"IA_Move","key":"W","modifiers":["SwizzleAxis"]},
            {"action":"IA_Move","key":"S","modifiers":["SwizzleAxis","Negate"]},
            {"action":"IA_Move","key":"D"}, {"action":"IA_Move","key":"A","modifiers":["Negate"]},
            {"action":"IA_Move","key":"Gamepad_Left2D"}]}]
        Contexts are rebuilt from the given mappings (replace=false to append). The character
        Blueprint still needs AddMappingContext + input action events (Blueprint tools)."""
        return run_editor_script(get_conn, "enhanced_input", {
            "actions": actions or [], "contexts": contexts or [], "folder": folder})

    @mcp.tool()
    def game_setup(level_game_mode: str = "", default_game_mode: str = "", editor_startup_map: str = "",
                   game_default_map: str = "") -> dict[str, Any]:
        """Read (no args) or set the game framework wiring.
        level_game_mode: GameMode override for the current level (World Settings), e.g.
        "/Game/Blueprints/BP_ArenaGameMode.BP_ArenaGameMode_C". default_game_mode, editor_startup_map,
        game_default_map: project Maps & Modes settings (saved to DefaultEngine.ini).
        Save the level afterwards (editor_action save_all) to keep the level override."""
        args = {"level_game_mode": level_game_mode, "default_game_mode": default_game_mode,
                "editor_startup_map": editor_startup_map, "game_default_map": game_default_map}
        args["read_only"] = not any(args.values())
        return run_editor_script(get_conn, "game_setup", args)

    @mcp.tool()
    def cpp_source(action: str = "list", path: str = "", content: str = "", pattern: str = "*") -> dict[str, Any]:
        """Read/write the project's C++ on the editor machine (works for remote editors).
        action="list" (pattern e.g. "Source/*/Public/*.h"), "read" (path relative to the project
        dir, e.g. "Source/MyGame/MyActor.cpp"), "write" (full file content). Restricted to
        Source/, Plugins/ and Config/. After editing .cpp bodies run live_coding_compile; new
        UCLASS/UPROPERTY/UFUNCTION or new files need an editor restart and full build."""
        return run_editor_script(get_conn, "cpp_source", {
            "action": action, "path": path, "content": content, "pattern": pattern})

    @mcp.tool()
    def live_coding_compile(timeout_s: float = 300.0) -> dict[str, Any]:
        """Trigger Live Coding (Windows editors) and wait for the result. Returns compiled
        true/false plus compiler errors parsed from the log, so you can fix and retry."""
        since = call(get_conn, "editor_log", {"lines": 1}).get("last_seq", 0)
        started = call(get_conn, "console_command", {"command": "LiveCoding.Compile"})
        if not started.get("success"):
            return started
        return wait_for_live_coding(get_conn, since, timeout_s)

    @mcp.tool()
    def performance_audit(triangle_budget: int = 5_000_000, top: int = 15) -> dict[str, Any]:
        """Static performance audit of the current level: LOD0 triangle total and heaviest meshes,
        mesh component and material slot counts, light mobility/shadow counts, instancing
        candidates, and a list of concrete issues. For frame timings, use pie_test + stat unit."""
        return run_editor_script(get_conn, "performance_audit", {"triangle_budget": triangle_budget, "top": top})


def register_packaging(mcp, get_conn):
    from .python_exec import run_editor_script as _run

    @mcp.tool()
    def package_project(platform: str = "", configuration: str = "Development", output_dir: str = "",
                        maps: list[str] | None = None, extra_args: str = "") -> dict[str, Any]:
        """Build, cook, stage, pak and archive a standalone build with UAT BuildCookRun, in the
        background on the editor machine. platform defaults to the editor's host (Win64/Linux/Mac);
        configuration Development or Shipping; maps limits cooking (e.g. ["/Game/Maps/Arena"]).
        Returns a job id immediately. Poll package_status(job) until state is succeeded/failed
        (first cooks take many minutes). The editor stays usable meanwhile."""
        return _run(get_conn, "package_job", {"action": "start", "platform": platform, "configuration": configuration,
                                              "output_dir": output_dir, "maps": maps or [], "extra_args": extra_args})

    @mcp.tool()
    def package_status(job: str = "", tail: int = 40) -> dict[str, Any]:
        """Progress of a package_project job (latest if job is empty): state running/succeeded/failed,
        current UAT stage, exit code, error lines, the log tail, and launcher paths when done."""
        return _run(get_conn, "package_job", {"action": "status", "job": job, "tail": tail})
