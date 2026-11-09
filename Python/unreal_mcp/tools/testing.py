"""Runtime verification: play the level in PIE and check assertions.

The orchestration lives here (server side) on purpose: while PIE runs, the editor must keep
ticking, so the server sleeps between short commands instead of blocking the game thread.
"""

from __future__ import annotations

import time
from typing import Any

from . import call
from .python_exec import run_editor_script, run_python

LOG_TYPES = ("log_contains", "log_absent", "no_errors")
_POLL_S = 0.25


def _wait_for_pie(get_conn, running: bool, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ctx = call(get_conn, "project_context")
        if ctx.get("success") and bool(ctx.get("pie_running")) == running:
            return True
        time.sleep(_POLL_S)
    return False


# Per nav bounds volume: is there NavMesh inside it? Optionally kick a rebuild.
_NAV_PROBE = """
w = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
vols = [a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
        if a.get_class().get_name() == "NavMeshBoundsVolume"]
empty = 0
for v in vols:
    origin, extent = v.get_actor_bounds(False)
    if unreal.NavigationSystemV1.project_point_to_navigation(w, origin, None, None, extent) is None:
        empty += 1
if empty and ARGS_REBUILD:
    _ensure_navmesh()
result = {"volumes": len(vols), "empty": empty,
          "building": bool(unreal.NavigationSystemV1.is_navigation_being_built(w))}
"""


def _common_source() -> str:
    from ..editor_scripts import source
    return source("_common") + "\n"


def _wait_for_navigation(get_conn, timeout_s: float, sleep) -> tuple[bool | None, bool]:
    """A NavMesh missing or still building when PIE starts leaves the play world without one (AI
    MoveTo silently fails). Volumes placed from scripts may never have been built. Rebuild once if
    any volume has no NavMesh, then poll until every volume has NavMesh and nothing is building.
    Returns (ready, rebuilt); ready is None when the check is unavailable (no navigation API / no editor)."""
    deadline = time.monotonic() + timeout_s
    rebuilt = False
    while True:
        code = _common_source() + _NAV_PROBE.replace("ARGS_REBUILD", "True" if not rebuilt else "False")
        r = run_python(get_conn, code, timeout_s=30)
        if not r.get("success"):
            return None, rebuilt
        state = r.get("result") or {}
        if not state.get("volumes"):
            return True, rebuilt
        if state.get("empty") and not rebuilt:
            rebuilt = True
        elif not state.get("empty") and not state.get("building"):
            return True, rebuilt
        if time.monotonic() >= deadline:
            return False, rebuilt
        sleep(0.5)


# ---------------------------------------------------------------------------------------------
# Input injection and frame timings (plugin commands pie_input / frame_stats)
# ---------------------------------------------------------------------------------------------

INPUT_STEPS = ("press", "hold", "axis", "look")


def _input_target(name: str) -> dict[str, str]:
    """An asset path ("/Game/Input/IA_Jump") is an Enhanced Input action; anything else is an FKey name."""
    return {"action": name} if str(name).startswith("/") else {"key": str(name)}


def input_calls(step: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    """pie_input command params for one press/hold/axis/look step, and how long it lasts."""
    if "press" in step:
        dur = float(step.get("duration_s", 0.1))
        return [{**_input_target(step["press"]), "mode": "press", "duration_s": dur}], dur
    if "hold" in step:
        dur = float(step.get("duration_s", 1.0))
        return [{**_input_target(step["hold"]), "mode": "hold", "duration_s": dur}], dur
    if "axis" in step:
        dur = float(step.get("duration_s", 1.0))
        return [{**_input_target(step["axis"]), "mode": "axis", "value": step.get("value", 1.0),
                 "duration_s": dur}], dur
    # look: [yaw, pitch] per frame. Raw mouse deltas by default; "action" sends it to e.g. IA_Look instead.
    look = step["look"]
    xy = [float(look[0]), float(look[1]) if len(look) > 1 else 0.0] if isinstance(look, (list, tuple)) \
        else [float(look), 0.0]
    dur = float(step.get("duration_s", 0.5))
    if step.get("action"):
        return [{"action": step["action"], "mode": "axis", "value": xy, "duration_s": dur}], dur
    calls = [{"key": k, "mode": "axis", "value": v, "duration_s": dur}
             for k, v in (("MouseX", xy[0]), ("MouseY", xy[1])) if v]
    return calls, dur


def run_input_step(get_conn, step: dict[str, Any], sleep) -> dict[str, Any]:
    calls, dur = input_calls(step)
    sent = []
    for params in calls:
        if step.get("player") is not None:
            params["player"] = int(step["player"])
        r = call(get_conn, "pie_input", params)
        if not r.get("success"):
            return {"ok": False, "error": r.get("error"), "input": params}
        sent.append({k: r.get(k) for k in ("key", "action", "mode", "value", "pawn") if r.get(k) is not None})
    if not step.get("async"):
        sleep(dur + 0.05)  # let the injection finish (release lands in the frame after it ends)
    return {"ok": True, "input": sent, "duration_s": dur, "async": bool(step.get("async"))}


_FRAME_LIMITS = {"max_avg_ms": ("frame_ms", "avg"), "max_p95_ms": ("frame_ms", "p95"),
                 "max_gpu_ms": ("gpu_ms", "avg"), "max_game_ms": ("game_ms", "avg"),
                 "max_render_ms": ("render_ms", "avg")}


def capture_frame_stats(get_conn, frames: int = 120, timeout_s: float = 60.0, sleep=None) -> dict[str, Any]:
    """Record `frames` frames on the editor and return average / p95 frame, game, render, RHI and GPU ms."""
    sleep = sleep or time.sleep
    started = call(get_conn, "frame_stats", {"action": "start", "frames": int(frames)})
    if not started.get("success"):
        return started
    deadline = time.monotonic() + timeout_s
    while True:
        sleep(0.25)
        r = call(get_conn, "frame_stats", {"action": "read"})
        if not r.get("success") or r.get("done"):
            return r
        if time.monotonic() >= deadline:
            call(get_conn, "frame_stats", {"action": "stop"})
            return {**r, "success": False, "error": f"only {r.get('frames')} of {frames} frames in {timeout_s}s"}


def run_frame_stats_step(get_conn, step: dict[str, Any], sleep) -> dict[str, Any]:
    spec = step["frame_stats"]
    opts = dict(spec) if isinstance(spec, dict) else {"frames": spec}
    opts.update({k: v for k, v in step.items() if k != "frame_stats"})
    r = capture_frame_stats(get_conn, int(opts.get("frames") or 120), float(opts.get("timeout_s", 60.0)), sleep)
    if not r.get("success"):
        return {"ok": False, "error": r.get("error"), "frame_stats": r}
    stats = {k: v for k, v in r.items() if k not in ("success", "done", "wanted")}
    over = []
    for limit, (group, field) in _FRAME_LIMITS.items():
        if opts.get(limit) is not None:
            actual = (stats.get(group) or {}).get(field)
            if actual is None or actual > float(opts[limit]):
                over.append(f"{group}.{field} {actual} > {opts[limit]}")
    if opts.get("min_fps") is not None and (stats.get("fps_avg") or 0) < float(opts["min_fps"]):
        over.append(f"fps_avg {stats.get('fps_avg')} < {opts['min_fps']}")
    rec: dict[str, Any] = {"ok": not over, "frame_stats": stats}
    if over:
        rec["error"] = "; ".join(over)
    return rec


def _log_assertion(a: dict[str, Any], index: int, entries: list[dict[str, Any]]) -> dict[str, Any]:
    t = a["type"]
    rec: dict[str, Any] = {"index": index, "type": t}
    if a.get("name"):
        rec["name"] = a["name"]
    if t == "no_errors":
        errs = [e["message"] for e in entries if e.get("verbosity") in ("Error", "Fatal")]
        rec.update(passed=not errs, actual=errs[:10])
        return rec
    text = a.get("text", "")
    hits = [e["message"] for e in entries if text.lower() in e.get("message", "").lower()]
    rec.update(passed=bool(hits) if t == "log_contains" else not hits, actual=hits[:10])
    return rec


def _run_steps(get_conn, steps: list[dict[str, Any]], sleep) -> list[dict[str, Any]]:
    """Timeline inside a running PIE session: wait / do (optionally repeated) / check."""
    out = []
    for i, step in enumerate(steps):
        rec: dict[str, Any] = {"step": i}
        if "wait_s" in step:
            sleep(float(step["wait_s"]))
            rec.update(ok=True, waited_s=step["wait_s"])
        elif "until" in step:
            # Drive until a condition holds: robust where a fixed repeat count depends on frame rate.
            deadline = time.monotonic() + float(step.get("timeout_s", 10.0))
            tries = 0
            while True:
                r = run_editor_script(get_conn, "pie_assert", {
                    "exec": step.get("do") or "", "assertions": [{"type": "python", "expr": step["until"]}]})
                tries += 1
                if not r.get("success"):
                    rec.update(ok=False, error=r.get("traceback") or r.get("error"), tries=tries)
                    out.append(rec)
                    return out
                if r["assertions"][0].get("passed"):
                    rec.update(ok=True, until=step["until"][:80], tries=tries)
                    break
                if time.monotonic() >= deadline:
                    waited = step.get("timeout_s", 10.0)
                    rec.update(ok=False, error=f"'{step['until'][:80]}' still false after {waited}s",
                               tries=tries, last=r["assertions"][0].get("actual"))
                    break
                sleep(float(step.get("interval_s", 0.05)))
        elif "do" in step:
            repeat = max(1, int(step.get("repeat", 1)))
            for n in range(repeat):
                r = run_editor_script(get_conn, "pie_assert", {"exec": step["do"], "assertions": []})
                if not r.get("success"):
                    rec.update(ok=False, error=r.get("traceback") or r.get("error"), at_repeat=n)
                    out.append(rec)
                    return out
                if n + 1 < repeat:
                    sleep(float(step.get("interval_s", 0.05)))
            rec.update(ok=True, did=step["do"][:80], repeat=repeat)
        elif any(k in step for k in INPUT_STEPS):
            rec.update(run_input_step(get_conn, step, sleep))
            if not rec["ok"]:
                out.append(rec)
                return out
        elif "release_all" in step:
            r = call(get_conn, "pie_input", {"mode": "release_all"})
            rec.update(ok=bool(r.get("success")), released=r.get("released"))
            if not r.get("success"):
                rec["error"] = r.get("error")
        elif "frame_stats" in step:
            rec.update(run_frame_stats_step(get_conn, step, sleep))
        elif "check" in step:
            r = run_editor_script(get_conn, "pie_assert", {"assertions": step["check"]})
            if not r.get("success"):
                rec.update(ok=False, error=r.get("traceback") or r.get("error"))
                out.append(rec)
                return out
            rec.update(ok=all(a.get("passed") for a in r["assertions"]), assertions=r["assertions"])
        else:
            rec.update(ok=False, error="step needs one of wait_s, do, until, check, press, hold, axis, look, "
                       "release_all, frame_stats")
        out.append(rec)
    return out


def run_pie_test(get_conn, assertions: list[dict[str, Any]], duration_s: float = 3.0,
                 start_timeout_s: float = 30.0, stop: bool = True,
                 sleep=time.sleep, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    world_asserts = [(i, a) for i, a in enumerate(assertions) if a.get("type") not in LOG_TYPES]
    log_asserts = [(i, a) for i, a in enumerate(assertions) if a.get("type") in LOG_TYPES]

    ctx = call(get_conn, "project_context")
    if not ctx.get("success"):
        return ctx
    if ctx.get("pie_running"):
        return {"success": False, "error": "PIE is already running; stop it first (editor_action pie_stop)"}

    nav_ready, nav_rebuilt = _wait_for_navigation(get_conn, 60.0, sleep)
    since = call(get_conn, "editor_log", {"lines": 1}).get("last_seq", 0)
    started = call(get_conn, "editor_action", {"action": "pie_start"})
    if not started.get("success"):
        return started
    try:
        if not _wait_for_pie(get_conn, True, start_timeout_s):
            return {"success": False, "error": f"PIE did not start within {start_timeout_s}s "
                    "(compile errors? check editor_log)"}
        sleep(duration_s)
        timeline = _run_steps(get_conn, steps, sleep) if steps else []
        results: list[dict[str, Any]] = []
        if world_asserts:
            r = run_editor_script(get_conn, "pie_assert", {"assertions": [a for _, a in world_asserts]})
            if not r.get("success"):
                return {"success": False, "error": "assertion script failed", "detail": r}
            for (orig, _), rec in zip(world_asserts, r["assertions"], strict=True):
                rec["index"] = orig
                results.append(rec)
        log = call(get_conn, "editor_log", {"since_seq": since, "lines": 5000, "min_verbosity": "Log"})
        entries = log.get("entries", [])
        results += [_log_assertion(a, i, entries) for i, a in log_asserts]
    finally:
        if stop:
            call(get_conn, "editor_action", {"action": "pie_stop"})
            _wait_for_pie(get_conn, False, 15.0)

    results.sort(key=lambda r: r["index"])
    failed = [r for r in results if not r.get("passed")]
    failed_steps = [s for s in timeline if not s.get("ok")]
    out_steps = {"steps": timeline} if steps else {}
    nav = {} if nav_ready is None else {"navigation_ready": nav_ready, "navmesh_repaired": nav_rebuilt}
    return {"success": True, "passed": not failed and not failed_steps, "total": len(results), **nav,
            "failed_count": len(failed) + len(failed_steps), **out_steps,
            "results": results,
            "errors_in_log": [e["message"] for e in entries if e.get("verbosity") in ("Error", "Fatal")][:20]}


def register(mcp, get_conn):
    @mcp.tool()
    def pie_test(assertions: list[dict[str, Any]] | None = None, duration_s: float = 3.0, stop: bool = True,
                 steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Play the current level in PIE for duration_s, then check assertions. The runtime proof
        that gameplay work actually works. Requires the level to compile and run.

        assertions (each may carry a "name"):
          {"type":"actor_exists","label":"HealthPickup","min":1}   or by "class":"Enemy", "max":..
          {"type":"property","target":"@player","property":"health","op":">=","value":100}
          {"type":"distance","a":"@player","b":"SpawnPoint","op":"<=","value":500}
          {"type":"distance","a":"Door","point":[0,0,0],"op":">","value":10}
          {"type":"python","expr":"len([a for a in actors if 'Coin' in a.get_actor_label()]) == 10"}
          {"type":"no_errors"}                       (no Error lines logged during the run)
          {"type":"log_contains","text":"Game Started"}   {"type":"log_absent","text":"Accessed None"}
        ops: == != < <= > >= approx (with "tolerance"). "@player" = player 0's pawn.

        steps: optional timeline run after duration_s, to DRIVE the game and check mid-run:
          {"wait_s": 1.0}
          {"do": "player.jump()"}                                    (Python in the PIE world:
          {"do": "player.add_movement_input(vec([1,0,0]), 1.0)",     player, world, actors,
           "repeat": 40, "interval_s": 0.05}                          find(label), vec, unreal)
          {"check": [ ...assertions as above... ]}
          {"do": "player.add_movement_input(vec([1,0,0]), 1.0)", "until": "find('Coin') is None",
           "timeout_s": 8}                                  (repeat do until the expression holds)
        Real input on the player controller (goes through Enhanced Input / the input stack like a
        human's; a name starting with "/" is an InputAction asset, anything else an FKey name):
          {"press": "SpaceBar"}  {"press": "/Game/Input/IA_Jump"}          (tap, duration_s 0.1)
          {"hold": "W", "duration_s": 1.5}                                 (key down ... up)
          {"axis": "/Game/Input/IA_Move", "value": [0, 1], "duration_s": 2}  (action value every frame)
          {"axis": "Gamepad_LeftY", "value": 1.0, "duration_s": 1}
          {"look": [5, 0], "duration_s": 0.5}           (mouse deltas per frame: yaw, pitch)
          {"look": [1, 0], "action": "/Game/Input/IA_Look"}
          add "async": true to keep going while it runs (hold W + press SpaceBar), "player": n for
          another local player; {"release_all": true} lets go of everything.
        Frame timings:
          {"frame_stats": 120}  or  {"frame_stats": 300, "max_p95_ms": 33.3, "min_fps": 30}
            -> frame/game/render/rhi/gpu ms (avg, p95, p99, min, max) and fps_avg; limits fail the step.
        The final `assertions` run after the timeline. Returns passed (bool), per-step results,
        per-assertion results with actual values, and navmesh_repaired=true when NavMesh had to be
        rebuilt before play.
        """
        return run_pie_test(get_conn, assertions or [], duration_s=duration_s, stop=stop, steps=steps)

    @mcp.tool()
    def pie_input(key: str = "", action: str = "", mode: str = "press", value: Any = None,
                  duration_s: float | None = None, player: int = 0) -> dict[str, Any]:
        """Inject input into a RUNNING PIE session (start it with editor_action pie_start), outside
        pie_test. key: FKey name (SpaceBar, W, LeftMouseButton, MouseX, Gamepad_LeftX, ...) or action:
        InputAction asset path (Enhanced Input, injected every frame). mode: press (tap, default
        0.1 s), hold (down for duration_s, default 1 s), axis (value every frame; number or [x, y]),
        release_all. Returns immediately; the input runs on the editor for duration_s."""
        params: dict[str, Any] = {"mode": mode, "player": player}
        if key:
            params["key"] = key
        if action:
            params["action"] = action
        if value is not None:
            params["value"] = value
        if duration_s is not None:
            params["duration_s"] = duration_s
        return call(get_conn, "pie_input", params)

    @mcp.tool()
    def frame_stats(frames: int = 120, timeout_s: float = 60.0) -> dict[str, Any]:
        """Measure the next `frames` editor frames (PIE or not): average / p95 / p99 frame time,
        fps, and game-thread, render-thread, RHI-thread and GPU ms. For a gameplay benchmark run it
        inside pie_test steps ({"frame_stats": 300}) so it measures the game, not the idle editor."""
        return capture_frame_stats(get_conn, frames, timeout_s)
