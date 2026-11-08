"""UE-Bench: scripted Unreal tasks with automatic checkers.

Each task (bench/tasks/*.json) has a prompt for the agent, optional setup tool calls, a checker
(scene_assert assertions on the editor level/assets and/or a pie_test run), and a reference
`solution` (tool calls). The `solution` agent replays it: CI uses that against the fake editor to
prove every checker passes on a correct solution and fails on an untouched level.

    python -m unreal_mcp.bench list
    python -m unreal_mcp.bench run --agent solution            # validate checkers on a live editor
    python -m unreal_mcp.bench run --agent none                # baseline: everything should fail
    python -m unreal_mcp.bench run --agent cmd --agent-cmd 'claude -p --mcp-config mcp.json {prompt}'

Placeholders {root} (the task's /Game/UEBench/<id> folder), {level} (fresh level path) and {id}
are substituted in prompts, setup, checks and solutions.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TASKS_DIR = Path(__file__).resolve().parents[2] / "bench" / "tasks"
BENCH_ROOT = "/Game/UEBench"

PROMPT_TEMPLATE = """You are working in an open Unreal Engine editor through the UnrealMCP tools.
The current level is {level} (freshly created for this task). Create any new assets under {root}.
Verify your work (scene_assert / pie_test) before you finish.

Task: {prompt}
"""


@dataclass
class Task:
    id: str
    domain: str
    title: str
    prompt: str
    check: dict[str, Any]
    solution: list[dict[str, Any]]
    setup: list[dict[str, Any]] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    timeout_s: float = 900.0

    @property
    def root(self) -> str:
        return f"{BENCH_ROOT}/{self.id}"


def load_tasks(tasks_dir: Path = DEFAULT_TASKS_DIR, pattern: str = "*") -> list[Task]:
    tasks = []
    for p in sorted(Path(tasks_dir).glob("*.json")):
        d = json.loads(p.read_text())
        if d["id"] != p.stem:
            raise ValueError(f"{p.name}: id {d['id']!r} must match the file name")
        if fnmatch.fnmatch(d["id"], pattern) or fnmatch.fnmatch(d["domain"], pattern):
            tasks.append(Task(**d))
    return tasks


def substitute(obj: Any, values: dict[str, str]) -> Any:
    if isinstance(obj, str):
        for k, v in values.items():
            obj = obj.replace("{" + k + "}", v)
        return obj
    if isinstance(obj, list):
        return [substitute(x, values) for x in obj]
    if isinstance(obj, dict):
        return {k: substitute(v, values) for k, v in obj.items()}
    return obj


class _Collector:
    def __init__(self):
        self.tools: dict[str, Any] = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def collect_tools(get_conn) -> dict[str, Any]:
    from .tools import register_all
    host = _Collector()
    register_all(host, get_conn)
    return host.tools


def call_tool(tools: dict[str, Any], name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    if name not in tools:
        return {"success": False, "error": f"unknown tool {name!r}"}
    try:
        r = tools[name](**(args or {}))
    except Exception as e:  # a tool raising is a harness-visible failure, not a crash
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
    if isinstance(r, list):  # tools that also return images: the dict comes first
        r = r[0] if r and isinstance(r[0], dict) else {"success": True}
    return r


def run_steps(tools, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, s in enumerate(steps):
        r = call_tool(tools, s["tool"], s.get("args"))
        ok = bool(r.get("success")) and not r.get("failed")
        out.append({"step": i, "tool": s["tool"], "ok": ok,
                    **({} if ok else {"error": r.get("error") or r.get("traceback") or r.get("failed")})})
    return out


def check_task(tools, check: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    passed = True
    if check.get("scene"):
        r = call_tool(tools, "scene_assert", {"assertions": check["scene"]})
        out["scene"] = r
        passed &= bool(r.get("passed"))
    if check.get("pie"):
        pie = check["pie"]
        r = call_tool(tools, "pie_test", {"assertions": pie.get("assertions", []),
                                          "duration_s": pie.get("duration_s", 2.0), "steps": pie.get("steps")})
        out["pie"] = r
        passed &= bool(r.get("passed"))
    out["passed"] = bool(passed)
    return out


def _failures(check_result: dict[str, Any]) -> list[str]:
    msgs = []
    for stage in ("scene", "pie"):
        r = check_result.get(stage)
        if not r:
            continue
        if not r.get("success"):
            msgs.append(f"{stage}: {r.get('error') or r.get('traceback')}")
        for a in r.get("results", []):
            if not a.get("passed"):
                msgs.append(f"{stage}: {a.get('name') or a.get('type')} actual={a.get('actual')!r}"
                            + (f" error={a['error']}" if a.get("error") else ""))
        for s in r.get("steps", []):
            if not s.get("ok"):
                msgs.append(f"{stage}: step {s['step']} {s.get('error') or s.get('assertions')}")
    return msgs


def requirement_missing(task: Task) -> str | None:
    for req in task.requires:
        if req == "blender":
            from .config import Settings
            from .tools.blender import resolve_blender
            if not resolve_blender(Settings.from_env()):
                return "blender not installed"
        elif req not in ("live_editor",):
            return f"unknown requirement {req!r}"
    return None


def run_agent_cmd(template: str, prompt: str, timeout_s: float, work_dir: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = work_dir / "prompt.txt"
    prompt_file.write_text(prompt)
    argv = [a.replace("{prompt_file}", str(prompt_file)).replace("{prompt}", prompt)
            for a in shlex.split(template)]
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"agent timed out after {timeout_s}s", "elapsed_s": timeout_s}
    except OSError as e:
        return {"ok": False, "error": f"could not start agent: {e}", "elapsed_s": 0}
    (work_dir / "agent_output.txt").write_text((p.stdout or "") + (p.stderr or ""))
    return {"ok": p.returncode == 0, "exit_code": p.returncode, "elapsed_s": round(time.monotonic() - t0, 1),
            "output_tail": ((p.stdout or "") + (p.stderr or ""))[-2000:]}


def run_task(tools, task: Task, agent: str = "solution", agent_cmd: str = "",
             work_dir: Path | None = None) -> dict[str, Any]:
    rec: dict[str, Any] = {"id": task.id, "domain": task.domain, "title": task.title}
    missing = requirement_missing(task)
    if missing:
        return {**rec, "status": "skipped", "reason": missing}
    t0 = time.monotonic()
    reset = call_tool(tools, "execute_python", {"code": reset_code(task)})
    if not reset.get("success"):
        return {**rec, "status": "error", "stage": "reset", "error": reset.get("traceback") or reset.get("error")}
    values = {"root": task.root, "level": reset["result"]["level"], "id": task.id}
    setup = run_steps(tools, substitute(task.setup, values))
    if not all(s["ok"] for s in setup):
        return {**rec, "status": "error", "stage": "setup", "setup": setup}
    if agent == "solution":
        rec["agent"] = {"steps": run_steps(tools, substitute(task.solution, values))}
    elif agent == "cmd":
        prompt = PROMPT_TEMPLATE.format(prompt=substitute(task.prompt, values), **values)
        rec["agent"] = run_agent_cmd(agent_cmd, prompt, task.timeout_s,
                                     (work_dir or Path(".")) / task.id)
    elif agent != "none":
        raise ValueError(f"unknown agent {agent!r}")
    result = check_task(tools, substitute(task.check, values))
    rec.update(status="passed" if result["passed"] else "failed", failures=_failures(result),
               elapsed_s=round(time.monotonic() - t0, 1), check=result)
    return rec


def reset_code(task: Task, root: str | None = None) -> str:
    # Goes through execute_python (not a dedicated tool) so the bench needs nothing beyond the
    # standard toolset; the script itself is the bench_reset editor script.
    from .editor_scripts import render
    return render("bench_reset", {"root": root or task.root, "level_name": f"L_{task.id.replace('-', '_')}"})




def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    domains: dict[str, dict[str, int]] = {}
    for r in results:
        d = domains.setdefault(r["domain"], {"passed": 0, "failed": 0, "error": 0, "skipped": 0})
        d[r["status"]] += 1
    scored = [r for r in results if r["status"] != "skipped"]
    passed = sum(r["status"] == "passed" for r in scored)
    return {"passed": passed, "scored": len(scored), "skipped": len(results) - len(scored),
            "pass_rate": round(passed / len(scored), 3) if scored else 0.0, "domains": domains}


def scoreboard_markdown(summary: dict[str, Any], results: list[dict[str, Any]], label: str) -> str:
    lines = [f"# UE-Bench: {label}", "",
             f"**{summary['passed']}/{summary['scored']} passed** ({summary['pass_rate']:.0%}), "
             f"{summary['skipped']} skipped", "", "| Domain | Passed | Scored |", "|---|---|---|"]
    for dom, c in sorted(summary["domains"].items()):
        scored = c["passed"] + c["failed"] + c["error"]
        lines.append(f"| {dom} | {c['passed']} | {scored} |")
    lines += ["", "| Task | Status | Notes |", "|---|---|---|"]
    for r in results:
        note = r.get("reason") or "; ".join(r.get("failures", [])[:2]) or r.get("stage", "")
        lines.append(f"| {r['id']} | {r['status']} | {note.replace('|', '/')[:160]} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m unreal_mcp.bench", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--tasks-dir", type=Path, default=DEFAULT_TASKS_DIR)
    run = sub.add_parser("run")
    run.add_argument("--tasks-dir", type=Path, default=DEFAULT_TASKS_DIR)
    run.add_argument("--filter", default="*", help="glob on task id or domain")
    run.add_argument("--agent", choices=["solution", "none", "cmd"], default="solution")
    run.add_argument("--agent-cmd", default="", help="command template; {prompt} or {prompt_file}")
    run.add_argument("--label", default="")
    run.add_argument("--out", type=Path, default=Path("bench-results"))
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for t in load_tasks(a.tasks_dir):
            req = f"  [requires {', '.join(t.requires)}]" if t.requires else ""
            print(f"{t.id:32} {t.domain:12} {t.title}{req}")
        return 0

    if a.agent == "cmd" and not a.agent_cmd:
        ap.error("--agent cmd needs --agent-cmd")
    from .connection import get_unreal_connection
    tools = collect_tools(get_unreal_connection)
    tasks = load_tasks(a.tasks_dir, a.filter)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = a.out / stamp
    results = []
    for t in tasks:
        r = run_task(tools, t, agent=a.agent, agent_cmd=a.agent_cmd, work_dir=out_dir / "agents")
        results.append(r)
        print(f"{r['status']:8} {t.id}" + (f"  {r['failures'][0]}" if r.get("failures") else ""), flush=True)
    summary = summarize(results)
    label = a.label or f"{a.agent} {stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps({"label": label, "summary": summary, "results": results},
                                                     indent=1, default=str))
    (out_dir / "scoreboard.md").write_text(scoreboard_markdown(summary, results, label))
    print(f"\n{summary['passed']}/{summary['scored']} passed -> {out_dir}")
    return 0 if summary["passed"] == summary["scored"] else 1


if __name__ == "__main__":
    sys.exit(main())
