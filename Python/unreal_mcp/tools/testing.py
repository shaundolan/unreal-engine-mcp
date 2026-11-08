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


def run_pie_test(get_conn, assertions: list[dict[str, Any]], duration_s: float = 3.0,
                 start_timeout_s: float = 30.0, stop: bool = True,
                 sleep=time.sleep) -> dict[str, Any]:
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
    return {"success": True, "passed": not failed, "total": len(results), "failed_count": len(failed),
            "results": results,
            "errors_in_log": [e["message"] for e in entries if e.get("verbosity") in ("Error", "Fatal")][:20]}


def register(mcp, get_conn):
    @mcp.tool()
    def pie_test(assertions: list[dict[str, Any]], duration_s: float = 3.0, stop: bool = True) -> dict[str, Any]:
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
        Returns passed (bool) and per-assertion results with actual values.
        """
        return run_pie_test(get_conn, assertions, duration_s=duration_s, stop=stop)
