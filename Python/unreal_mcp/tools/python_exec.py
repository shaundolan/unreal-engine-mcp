"""execute_python: run arbitrary Unreal Python inside the editor.

This is the universal escape hatch: anything the ``unreal`` module can do, the agent can
do. User code runs in a fresh namespace with ``unreal`` imported; assign to ``result``
to return a JSON-serializable value (UObjects are returned as their path names).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from . import call

_WRAPPER = r'''
import json as _mcp_json, sys as _mcp_sys, io as _mcp_io, traceback as _mcp_tb
def _mcp_default(o):
    for attr in ("get_path_name", "get_name"):
        fn = getattr(o, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    if hasattr(o, "to_tuple"):
        try:
            return list(o.to_tuple())
        except Exception:
            pass
    return repr(o)
_mcp_buf = _mcp_io.StringIO()
_mcp_old = _mcp_sys.stdout
_mcp_sys.stdout = _mcp_buf
_mcp_r = {"ok": True}
_mcp_g = {"__name__": "__mcp__"}
try:
    try:
        import unreal as _mcp_unreal
        _mcp_g["unreal"] = _mcp_unreal
    except ImportError:
        pass
    exec(compile(__MCP_SRC__, "<mcp>", "exec"), _mcp_g)
    _mcp_r["result"] = _mcp_g.get("result")
except BaseException:
    _mcp_r["ok"] = False
    _mcp_r["traceback"] = _mcp_tb.format_exc()
finally:
    _mcp_sys.stdout = _mcp_old
_mcp_r["stdout"] = _mcp_buf.getvalue()
try:
    _mcp_line = _mcp_json.dumps(_mcp_r, default=_mcp_default)
except Exception as _mcp_e:
    _mcp_line = _mcp_json.dumps({"ok": False, "traceback": "result not serializable: %r" % (_mcp_e,),
                                 "stdout": _mcp_r.get("stdout", "")})
print(__MCP_MARK__ + _mcp_line)
'''


def build_script(code: str, marker: str) -> str:
    """Wrap user code so its result/stdout/traceback come back as one marked JSON line."""
    return (_WRAPPER
            .replace("__MCP_SRC__", json.dumps(code))
            .replace("__MCP_MARK__", json.dumps(marker)))


def parse_output(log: list[dict[str, Any]], marker: str) -> dict[str, Any] | None:
    for entry in log:
        text = entry.get("output", "")
        idx = text.find(marker)
        if idx != -1:
            try:
                return json.loads(text[idx + len(marker):].strip())
            except json.JSONDecodeError:
                return None
    return None


def register(mcp, get_conn):
    @mcp.tool()
    def execute_python(code: str, timeout_s: float = 60.0) -> dict[str, Any]:
        """Execute Unreal Python in the live editor. The universal tool.

        Use when no dedicated tool covers the task (materials, Niagara, sequencer, asset
        import, editor utilities, anything in the `unreal` module). `unreal` is
        pre-imported. Assign to `result` to return data; print() output is captured.
        Wrap edits in `with unreal.ScopedEditorTransaction("desc"):` so they can be undone.
        Look up APIs first with unreal_api_search if unsure of names.

        Returns {success, result, stdout, traceback?, log_errors?}.
        """
        marker = f"<<MCP:{uuid.uuid4().hex}>>"
        reply = call(get_conn, "execute_python",
                     {"code": build_script(code, marker), "mode": "file", "timeout_s": timeout_s})
        if not reply.get("success"):
            return reply
        log = reply.get("log", [])
        parsed = parse_output(log, marker)
        errors = [e.get("output", "") for e in log
                  if e.get("type") == "Error" and marker not in e.get("output", "")]
        if parsed is None:
            return {"success": False,
                    "error": "script produced no result marker (editor Python crashed or timed out?)",
                    "log": log[-50:]}
        out: dict[str, Any] = {"success": bool(parsed.get("ok")), "result": parsed.get("result"),
                               "stdout": parsed.get("stdout", "")}
        if parsed.get("traceback"):
            out["traceback"] = parsed["traceback"]
        if errors:
            out["log_errors"] = errors[-20:]
        return out
