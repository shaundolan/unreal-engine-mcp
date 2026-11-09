#!/usr/bin/env python3
"""Package the open project through the MCP tools (package_project + package_status) and wait.

    cd Python && uv run python ../scripts/package.py [--platform Win64] [--config Development] [--map /Game/Maps/X]

Talks to the editor on UNREAL_MCP_HOST/PORT/TOKEN. Prints the UAT stage while it runs and the
launchers at the end; exit code 0 only when the build succeeded.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Python"))

from unreal_mcp.bench import call_tool, collect_tools  # noqa: E402
from unreal_mcp.connection import get_unreal_connection  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="", help="Win64, Linux, Mac (default: the editor's host)")
    ap.add_argument("--config", default="Development", choices=["Development", "Shipping", "DebugGame"])
    ap.add_argument("--map", action="append", default=[], help="limit cooking to these maps (repeatable)")
    ap.add_argument("--timeout-min", type=float, default=120)
    a = ap.parse_args()

    tools = collect_tools(get_unreal_connection)
    job = call_tool(tools, "package_project", {"platform": a.platform, "configuration": a.config, "maps": a.map})
    if not job.get("success"):
        print("package_project failed:", job.get("error") or job.get("traceback"))
        return 1
    print(f"job {job['job']} ({job['platform']} {job['configuration']}) -> {job['output_dir']}\n  log: {job['log']}")
    deadline, last = time.monotonic() + a.timeout_min * 60, None
    while time.monotonic() < deadline:
        time.sleep(15)
        st = call_tool(tools, "package_status", {"job": job["job"], "tail": 20})
        if not st.get("success"):
            print("package_status failed:", st.get("error") or st.get("traceback"))
            return 1
        if st.get("stage") != last:
            last = st.get("stage")
            print(f"  {st['elapsed_s']:>7.0f}s  {last}")
        if st["state"] != "running":
            print(f"{st['state'].upper()} (exit {st.get('exit_code')}) after {st['elapsed_s']:.0f}s")
            for f in st.get("launchers", []):
                print("  launcher:", f)
            if st["state"] != "succeeded":
                print("\n".join(st.get("errors", [])[-20:]) or "\n".join(st.get("log_tail", [])))
            return 0 if st["state"] == "succeeded" else 1
    print(f"still running after {a.timeout_min} min; check package_status / the log")
    return 1


if __name__ == "__main__":
    sys.exit(main())
