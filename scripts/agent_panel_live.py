#!/usr/bin/env python3
"""Live check of the in-editor agent panel against a running editor. Uses the mock provider only (no model calls).

    cd Python && uv run python ../scripts/agent_panel_live.py

The editor's UNREAL_MCP_PORT (default 55557) selects it. It runs through the same client object the Slate tab renders,
via the agent_panel command. Steps: the tab is registered and opens; the panel starts the runner itself and connects;
a scripted turn spawns Agent_Cube; a destructive call raises a confirmation, which is denied; a turn is undone with
"Undo last agent turn"; then the runner stops. Prints PASS/FAIL per check, and the exit code is the number of failures.
The runner port is UnrealMCP Agent > Runner Port (default 55600) and must be free.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Python"))

from unreal_mcp.connection import UnrealConnection  # noqa: E402
from unreal_mcp.tools.python_exec import run_python  # noqa: E402

c = UnrealConnection()
c.MAX_CONNECT_ATTEMPTS = 1
failures = 0


def check(name: str, ok: bool, detail: object = "") -> bool:
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {json.dumps(detail, default=str)[:600]}" if detail and not ok else ""),
          flush=True)
    failures += 0 if ok else 1
    return ok


def panel(action: str, **p):
    r = c.send_command("agent_panel", {"action": action, **p}, timeout=60)
    return r.get("result", r) if r.get("status") != "error" else {"success": False, "error": r.get("error")}


def labels(prefix: str) -> list[str]:
    r = run_python(lambda: c, "result = sorted(a.get_actor_label() for a in unreal.get_editor_subsystem("
                              f"unreal.EditorActorSubsystem).get_all_level_actors() if a.get_actor_label().startswith({prefix!r}))")
    return r.get("result") or []


def wait(pred, timeout: float):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(1)
    return None


def idle() -> bool:
    s = panel("status")
    return not s.get("turn_running") and not (s.get("undo") or {}).get("open")


SCRIPT = [
    {"text": "Checking the editor.", "tool_calls": [{"name": "unreal_status", "args": {}}]},
    {"text": "Spawning a cube.", "tool_calls": [{"name": "scene_compose", "args": {"operations": [
        {"op": "spawn", "asset": "/Engine/BasicShapes/Cube.Cube", "label": "Agent_Cube", "location": [0, 0, 150]}]}}]},
    {"text": "Done: Agent_Cube is in the level."},
    {"text": "Deleting an asset.", "tool_calls": [{"name": "asset_manage", "args": {"operations": [
        {"op": "delete", "path": "/Game/DoesNotExist/SM_Nothing"}]}}]},
    {"text": "Turn two finished."},
]


def main() -> int:
    st = panel("status")
    if not check("editor reachable and plugin has agent_panel", st.get("success") is not False, st):
        return failures
    check("tab spawner 'UnrealMCPAgent' registered", st.get("tab_registered") is True, st)
    check("runner Python dir resolved", bool(st.get("python_dir")), st.get("python_dir_problem"))
    check("tab opens programmatically", panel("open").get("tab_open") is True)
    ctx = panel("context")
    check("editor context has the level", bool(ctx.get("level")), ctx)

    script = Path(tempfile.mkdtemp()) / "mock_script.json"
    script.write_text(json.dumps(SCRIPT))
    for a in labels("Agent_Cube"):
        run_python(lambda: c, f"[unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(a) for a in "
                              f"unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() "
                              f"if a.get_actor_label() == {a!r}]")
    r = panel("start_runner", extra_args=f'--provider mock --mock-script "{script}"')
    check("start_runner accepted", r.get("success") is not False, r)
    ok = wait(lambda: panel("status").get("connection") == "connected", 300)
    if not check("panel started the runner and connected (<=300 s)", bool(ok), panel("status").get("runner_output_tail")):
        return failures
    st = panel("status")
    check("hello lists 4 providers and the specialists", len(st.get("providers", [])) == 4
          and "unreal-blueprint" in st.get("specialists", []), st)

    check("send turn 1", panel("send", text="add a cube above the origin", provider="mock").get("success") is not False)
    wait(lambda: idle() and panel("status").get("turn", 0) >= 1, 120)
    items = panel("transcript")["items"]
    tools = [i for i in items if i["kind"] == "tool"]
    check("turn 1: two tool rows, both ok", [t["tool"] for t in tools] == ["unreal_status", "scene_compose"]
          and all(t["ok"] for t in tools), tools)
    check("turn 1: streamed assistant text", any("Agent_Cube" in (i.get("text") or "") for i in items if i["kind"] == "assistant"))
    check("turn 1: Agent_Cube exists", labels("Agent_Cube") == ["Agent_Cube"], labels("Agent_Cube"))
    undo = panel("status").get("undo") or {}
    check("turn 1: undo accounting counted >= 1 step", undo.get("undoable_steps", 0) >= 1, undo)

    nxt = len(items)
    panel("send", text="clean up old assets", provider="mock")
    req = wait(lambda: next((i for i in panel("transcript", since=nxt)["items"] if i["kind"] == "confirm"), None), 60)
    check("turn 2: destructive asset_manage delete raised a confirmation", bool(req), req)
    if req:
        panel("confirm", id=req["confirm_id"], allow=False)
    wait(idle, 60)
    rows = panel("transcript", since=nxt)["items"]
    denied = [i for i in rows if i["kind"] == "tool" and i.get("denied")]
    check("turn 2: the denied call did not run", len(denied) == 1, rows)
    check("turn 2 made no undoable change, so undo refuses", panel("undo_last_turn").get("success") is False)

    panel("reset")
    nxt = panel("transcript")["next"]
    script3 = [{"text": "Spawning a second cube.", "tool_calls": [{"name": "scene_compose", "args": {"operations": [
        {"op": "spawn", "asset": "/Engine/BasicShapes/Cube.Cube", "label": "Agent_Cube_B", "location": [300, 0, 150]}]}}]},
        {"text": "Done."}]
    panel("send", text="one more", provider="mock", mock_script=script3)
    wait(lambda: idle() and any(i["kind"] == "tool" and i.get("done") for i in panel("transcript", since=nxt)["items"]), 120)
    check("turn 3: Agent_Cube_B exists", "Agent_Cube_B" in labels("Agent_Cube"), labels("Agent_Cube"))
    u = panel("undo_last_turn")
    check("Undo last agent turn succeeds", u.get("success") is not False and u.get("undone", 0) >= 1, u)
    check("undo removed Agent_Cube_B and kept Agent_Cube", labels("Agent_Cube") == ["Agent_Cube"], labels("Agent_Cube"))
    check("second undo of the same turn refuses", panel("undo_last_turn").get("success") is False)

    st = panel("stop_runner")
    check("stop_runner stops the process", st.get("runner_process_running") is False, st)
    panel("close")
    check("tab closes", wait(lambda: panel("status").get("tab_open") is False, 10) is not None)
    print(f"\n{failures} failure(s)")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
