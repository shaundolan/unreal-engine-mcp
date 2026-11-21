"""headless_check: open a map, run a pie_test suite that works under -nullrhi, return a compact pass/fail.

Also a shell entry point (ripper01 / CI), against an already running editor:
    python -m unreal_mcp.tools.headless suite.json [--map /Game/Maps/X] [--port 55558] [--json]
exit 0 = passed, 1 = failed, 2 = could not run (no editor, bad suite, PIE did not start).
Suite format: docs/HEADLESS_CHECK.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import call
from .testing import run_pie_test

COUNTER_PREFIX = "counter:"
# Steps that need a renderer: dropped (and reported) under -nullrhi instead of failing the run.
RENDER_STEPS = ("capture",)
SUITE_KEYS = {"name", "map", "discard_changes", "duration_s", "start_timeout_s", "width", "height", "mode", "steps",
              "assertions", "counters", "fail_on_log_errors", "ignore_log_errors", "allow_render_steps", "notes"}


def load_suite(suite: dict[str, Any] | str) -> dict[str, Any]:
    """A suite dict, a JSON string, or a path to a JSON file."""
    if isinstance(suite, dict):
        data = suite
    else:
        text = str(suite).strip()
        data = json.loads(text) if text.startswith("{") else json.loads(Path(text).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("a suite is a JSON object")
    unknown = set(data) - SUITE_KEYS
    if unknown:
        raise ValueError(f"unknown suite keys {sorted(unknown)}; allowed: {sorted(SUITE_KEYS)}")
    for c in data.get("counters", []):
        if not c.get("name") or not c.get("expr"):
            raise ValueError(f"counter needs name and expr: {c}")
    return data


def _counter_asserts(counters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "python", "name": COUNTER_PREFIX + c["name"], "expr": c["expr"]} for c in counters]


def _is_counter(rec: dict[str, Any]) -> bool:
    return str(rec.get("name", "")).startswith(COUNTER_PREFIX)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    return float(v) if isinstance(v, (int, float)) else None


def _counter_report(counters: list[dict[str, Any]], start: dict[str, dict], end: dict[str, dict]
                    ) -> tuple[dict[str, Any], list[str]]:
    report, failures = {}, []
    for c in counters:
        key = COUNTER_PREFIX + c["name"]
        s, e = start.get(key, {}), end.get(key, {})
        rec: dict[str, Any] = {"start": s.get("actual"), "end": e.get("actual")}
        err = e.get("error") or s.get("error")
        sv, ev = _num(rec["start"]), _num(rec["end"])
        if sv is not None and ev is not None:
            rec["delta"] = round(ev - sv, 3)
        if err:
            rec["error"] = err
            failures.append(f"counter {c['name']}: {err}")
        for bound, val, op in (("min", ev, "<"), ("max", ev, ">"),
                               ("min_delta", rec.get("delta"), "<"), ("max_delta", rec.get("delta"), ">")):
            if bound not in c or err:
                continue
            if val is None:
                failures.append(f"counter {c['name']}: {bound} {c[bound]} needs a numeric value, got {rec['end']!r}")
            elif (val < c[bound]) if op == "<" else (val > c[bound]):
                failures.append(f"counter {c['name']}: {bound.replace('_', ' ')} {c[bound]} not met "
                                f"({'delta' if 'delta' in bound else 'end'} = {val})")
        report[c["name"]] = rec
    return report, failures


def _short(v: Any, n: int = 120) -> str:
    s = json.dumps(v, default=str) if not isinstance(v, str) else v
    return s if len(s) <= n else s[: n - 3] + "..."


def summarize(suite: dict[str, Any], r: dict[str, Any], skipped: list[dict[str, Any]],
              opened: dict[str, Any] | None, elapsed: float) -> dict[str, Any]:
    counters = suite.get("counters", [])
    out: dict[str, Any] = {"success": bool(r.get("success")), "suite": suite.get("name", ""),
                           "map": (opened or {}).get("current_level") or suite.get("map") or "(current level)"}
    if not r.get("success"):
        out.update(passed=False, error=r.get("error") or "pie_test failed",
                   **({"detail": r["detail"]} if "detail" in r else {}))
        return out
    steps = r.get("steps", [])
    start_sample: dict[str, dict] = {}
    failures: list[str] = []
    for s in steps:
        asserts = s.get("assertions") or []
        if asserts and all(_is_counter(a) for a in asserts):
            start_sample = {a["name"]: a for a in asserts}
            continue
        if not s.get("ok"):
            what = next((k for k in ("did", "until", "input", "assertions") if k in s), "step")
            bad = [a for a in asserts if not a.get("passed")]
            detail = "; ".join(f"{a.get('name') or a.get('type')} actual={_short(a.get('actual'), 60)}"
                               + (f" error={a['error']}" if a.get("error") else "") for a in bad)
            failures.append(f"step {s.get('step')} ({what}): {s.get('error') or detail or 'failed'}")
    end_sample = {}
    for a in r.get("results", []):
        if _is_counter(a):
            end_sample[a["name"]] = a
        elif not a.get("passed"):
            failures.append(f"assertion {a.get('index')} {a.get('name') or ''} ({a.get('type')}): "
                            f"actual={_short(a.get('actual'))}" + (f" error={a['error']}" if a.get("error") else ""))
    report, counter_fail = _counter_report(counters, start_sample, end_sample)
    failures += counter_fail
    ignore = [t.lower() for t in suite.get("ignore_log_errors", [])]
    log_errors = [e for e in r.get("errors_in_log", []) if not any(t in e.lower() for t in ignore)]
    if suite.get("fail_on_log_errors") and log_errors:
        failures.append(f"{len(log_errors)} error line(s) in the log")
    out.update(passed=not failures, failures=failures, counters=report, log_errors=log_errors[:10],
               steps_run=len(steps), assertions=len([a for a in r.get("results", []) if not _is_counter(a)]),
               elapsed_s=round(elapsed, 1))
    if skipped:
        out["skipped_steps"] = skipped
    for k in ("navigation_ready", "navmesh_repaired"):
        if k in r:
            out[k] = r[k]
    return out


def run_headless_check(get_conn, suite: dict[str, Any] | str, map_path: str = "", sleep=None,
                       pie=run_pie_test) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        s = load_suite(suite)
    except (OSError, ValueError) as e:
        return {"success": False, "passed": False, "error": f"bad suite: {e}"}
    target = map_path or s.get("map", "")
    opened = None
    if target:
        opened = call(get_conn, "level_open", {"path": target, "create": False, "save_current": False,
                                              "discard_changes": bool(s.get("discard_changes", True))}, timeout=600)
        if not opened.get("success"):
            return {"success": False, "passed": False, "suite": s.get("name", ""), "map": target,
                    "error": f"level_open failed: {opened.get('error')}"}
    steps, skipped = [], []
    for i, st in enumerate(s.get("steps", [])):
        if not s.get("allow_render_steps") and any(k in st for k in RENDER_STEPS):
            skipped.append({"step": i, "reason": "needs a renderer (-nullrhi); set allow_render_steps to keep it"})
        else:
            steps.append(st)
    counters = s.get("counters", [])
    if counters:
        steps = [{"check": _counter_asserts(counters)}, *steps]
    r = pie(get_conn, list(s.get("assertions", [])) + _counter_asserts(counters),
            duration_s=float(s.get("duration_s", 2.0)), start_timeout_s=float(s.get("start_timeout_s", 60.0)),
            stop=True, sleep=sleep, steps=steps or None, width=int(s.get("width", 640)),
            height=int(s.get("height", 360)), mode=s.get("mode", "new_window"))
    return summarize(s, r, skipped, opened, time.monotonic() - t0)


def register(mcp, get_conn):
    @mcp.tool()
    def headless_check(suite: dict[str, Any] | str, map_path: str = "") -> dict[str, Any]:
        """One-call gameplay check that also works in a -nullrhi / offscreen editor: open the suite's map
        (or map_path; unsaved edits are discarded unless the suite sets "discard_changes": false), play it
        with pie_test's steps + assertions, and return a COMPACT verdict: passed, failures (one line
        each: failing steps, assertions with actual values, counter bounds, log errors when
        fail_on_log_errors), counters {name: start, end, delta}, the first log errors, skipped steps.

        suite: a JSON object, a JSON string, or a path to a .json file on the server machine:
          {"name": "drive", "map": "/Game/Maps/City", "duration_s": 2,
           "steps": [ ...pie_test steps (hold/press/axis/do/until/check/wait_s/possess/spawn)... ],
           "assertions": [ ...pie_test assertions... ],
           "counters": [{"name": "cars", "expr": "len([a for a in actors if 'Car' in a.get_name()])",
                         "min": 5, "min_delta": 0}],          sampled at start and end of the timeline
           "fail_on_log_errors": false, "ignore_log_errors": ["LogStreaming"],
           "width": 640, "height": 360, "mode": "new_window"}
        capture steps need a renderer and are skipped unless "allow_render_steps": true.
        Example suite and the CLI for shells: docs/HEADLESS_CHECK.md."""
        return run_headless_check(get_conn, suite, map_path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m unreal_mcp.tools.headless",
                                 description="Run a headless_check suite against a running editor.")
    ap.add_argument("suite", help="suite JSON file")
    ap.add_argument("--map", default="", help="override the suite's map")
    ap.add_argument("--port", type=int, default=0, help="editor MCP port (default UNREAL_MCP_PORT or 55557)")
    ap.add_argument("--json", action="store_true", help="print the full JSON result")
    a = ap.parse_args(argv)
    if a.port:
        os.environ["UNREAL_MCP_PORT"] = str(a.port)
    from ..connection import UnrealConnection
    conn = UnrealConnection()
    try:
        r = run_headless_check(lambda: conn, a.suite, a.map)
    finally:
        conn.disconnect()
    if a.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        verdict = "PASS" if r.get("passed") else ("ERROR" if not r.get("success") else "FAIL")
        print(f"{verdict} {r.get('suite') or a.suite} on {r.get('map')} ({r.get('elapsed_s', 0)}s, "
              f"{r.get('steps_run', 0)} steps, {r.get('assertions', 0)} assertions)")
        if r.get("error"):
            print(f"  error: {r['error']}")
        for name, c in (r.get("counters") or {}).items():
            print(f"  counter {name}: {c.get('start')} -> {c.get('end')}"
                  + (f" (delta {c['delta']})" if "delta" in c else ""))
        for f in r.get("failures", []):
            print(f"  FAIL {f}")
        for e in r.get("log_errors", []):
            print(f"  log error: {_short(e, 200)}")
        for s in r.get("skipped_steps", []):
            print(f"  skipped step {s['step']}: {s['reason']}")
    return 0 if r.get("passed") else (2 if not r.get("success") else 1)


if __name__ == "__main__":
    sys.exit(main())
