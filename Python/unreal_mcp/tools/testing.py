"""Runtime verification: play the level in PIE and check assertions.

The orchestration lives here (server side) on purpose: while PIE runs, the editor must keep
ticking, so the server sleeps between short commands instead of blocking the game thread.
"""

from __future__ import annotations

import time
from typing import Any

from . import call
from .python_exec import run_editor_script

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
        elif "check" in step:
            r = run_editor_script(get_conn, "pie_assert", {"assertions": step["check"]})
            if not r.get("success"):
                rec.update(ok=False, error=r.get("traceback") or r.get("error"))
                out.append(rec)
                return out
            rec.update(ok=all(a.get("passed") for a in r["assertions"]), assertions=r["assertions"])
        else:
            rec.update(ok=False, error="step needs one of wait_s, do, check")
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
    return {"success": True, "passed": not failed and not failed_steps, "total": len(results),
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
        The final `assertions` run after the timeline. Returns passed (bool), per-step results,
        and per-assertion results with actual values.
        """
        return run_pie_test(get_conn, assertions or [], duration_s=duration_s, stop=stop, steps=steps)
