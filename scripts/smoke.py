#!/usr/bin/env python3
"""End-to-end smoke test against a LIVE editor running the UnrealMCP plugin.

    cd Python && uv run python ../scripts/smoke.py [--host 127.0.0.1] [--port 55557] [--keep]

Each check prints PASS/FAIL; exit code is the number of failures. Creates actors and a
Blueprint named MCP_Smoke* and deletes them at the end unless --keep is given.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Python"))

from unreal_mcp.config import Settings  # noqa: E402
from unreal_mcp.connection import UnrealConnection  # noqa: E402
from unreal_mcp.tools.python_exec import build_script, parse_output  # noqa: E402

failures = 0


def check(name: str, ok: bool, detail: object = "") -> bool:
    global failures
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not ok else ""))
    failures += 0 if ok else 1
    return ok


def result(reply: dict) -> dict:
    return reply.get("result", {}) if reply.get("status") == "success" else {}


def py(conn: UnrealConnection, code: str) -> dict:
    marker = "<<SMOKE>>"
    r = conn.send_command("execute_python", {"code": build_script(code, marker), "timeout_s": 60})
    return parse_output(result(r).get("log", []), marker) or {"ok": False, "raw": r}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("UNREAL_MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("UNREAL_MCP_PORT", 55557)))
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    conn = UnrealConnection(Settings(host=a.host, port=a.port, token=os.environ.get("UNREAL_MCP_TOKEN")))

    # 1. Transport
    if not check("ping", result(conn.send_command("ping")).get("message") == "pong", conn.send_command("ping")):
        return failures
    cmds = result(conn.send_command("list_commands"))
    check("list_commands has Phase 1 commands",
          {"execute_python", "property_set", "viewport_capture"} <= set(cmds.get("commands", [])), cmds)
    big = "x" * (2 * 1024 * 1024)
    t = time.monotonic()
    r = py(conn, f"result = len({big!r})")
    check("2 MB request round trip", r.get("result") == len(big), r)
    print(f"       ({time.monotonic() - t:.2f}s)")

    # 2. Python
    r = py(conn, "result = unreal.SystemLibrary.get_engine_version()")
    check("execute_python returns value", r.get("ok") and "5." in str(r.get("result")), r)
    r = py(conn, "raise RuntimeError('expected')")
    check("execute_python surfaces traceback", not r.get("ok") and "expected" in r.get("traceback", ""), r)

    # 3. Context + log
    ctx = result(conn.send_command("project_context"))
    check("project_context", bool(ctx.get("engine_version")) and "maps" in ctx, ctx)
    print(f"       engine {ctx.get('engine_version')}  level {ctx.get('current_level')}  "
          f"stub exists={ctx.get('python_stub_exists')}")
    seq = result(conn.send_command("editor_log", {"lines": 1})).get("last_seq", 0)
    py(conn, "unreal.log_warning('MCP_SMOKE_MARKER')")
    log = result(conn.send_command("editor_log", {"since_seq": seq, "filter": "MCP_SMOKE_MARKER"}))
    check("editor_log sees new warning", len(log.get("entries", [])) >= 1, log)

    # 4. Reflection + undo
    r = py(conn, """
sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
a = sub.spawn_actor_from_class(unreal.PointLight, unreal.Vector(0, 0, 300))
a.set_actor_label('MCP_SmokeLight')
result = a.get_actor_label()
""")
    check("spawn via python", r.get("result") == "MCP_SmokeLight", r)
    desc = result(conn.send_command("object_describe", {"path": "MCP_SmokeLight", "filter": "Light"}))
    check("object_describe actor", desc.get("label") == "MCP_SmokeLight", desc)
    got = result(conn.send_command("property_get", {"path": "MCP_SmokeLight", "property": "LightComponent.Intensity"}))
    check("property_get nested", isinstance(got.get("value"), (int, float)), got)
    s = conn.send_command("property_set", {"path": "MCP_SmokeLight", "property": "LightComponent.Intensity",
                                           "value": 12345})
    check("property_set nested", result(s).get("new_value") == 12345, s)
    conn.send_command("editor_action", {"action": "undo", "count": 1})
    got = result(conn.send_command("property_get", {"path": "MCP_SmokeLight", "property": "LightComponent.Intensity"}))
    check("undo reverts property_set", got.get("value") != 12345, got)
    fc = result(conn.send_command("function_call", {"path": "MCP_SmokeLight", "function": "K2_GetActorLocation"}))
    check("function_call return value", isinstance(fc.get("return_value"), dict), fc)

    # 5. Viewport
    cap = result(conn.send_command("viewport_capture", {"max_width": 640}))
    ok = cap.get("format") == "png" and base64.b64decode(cap.get("data_base64", ""))[:4] == b"\x89PNG"
    check("viewport_capture PNG", ok, {k: v for k, v in cap.items() if k != "data_base64"})

    # 6. Legacy Blueprint path still works through the registry
    bp = conn.send_command("create_blueprint", {"name": "MCP_SmokeBP", "parent_class": "Actor"})
    check("legacy create_blueprint", bp.get("status") == "success", bp)
    comp = conn.send_command("compile_blueprint", {"blueprint_name": "MCP_SmokeBP"})
    check("legacy compile_blueprint", comp.get("status") == "success", comp)

    if not a.keep:
        conn.send_command("delete_actor", {"name": "MCP_SmokeLight"})
        py(conn, "unreal.EditorAssetLibrary.delete_asset('/Game/Blueprints/MCP_SmokeBP')")

    print(f"\n{failures} failure(s)")
    return failures


if __name__ == "__main__":
    sys.exit(main())
