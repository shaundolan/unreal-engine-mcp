"""execute_python: run arbitrary Unreal Python inside the editor.

This is the universal escape hatch: anything the ``unreal`` module can do, the agent can
do. User code runs in a fresh namespace with ``unreal`` imported; assign to ``result``
to return a JSON-serializable value (UObjects are returned as their path names).

Long jobs (RingFold's build_city.py runs scripts for 30-60 minutes) get three extras, all opt-in from the script's
side and invisible to scripts that do not use them:

* ``unreal_mcp.progress(done, total=None, message="")`` inside the script records progress in a thread-safe registry in
  the plugin (``unreal.MCPPythonJobLibrary``). The game thread is busy for as long as the script runs, so the MCP
  server reads that registry over a side connection (``python_job_status``, answered on the plugin's socket thread like
  ``editor_health``) and relays it as MCP progress notifications when the client sent a progressToken.
* Cooperative cancel: an MCP cancellation, or the plugin giving up at timeout_s, flags the job.
  ``unreal_mcp.cancelled()`` polls the flag and the next ``progress()`` raises ``unreal_mcp.Cancelled``. The result
  says ``outcome``: ok, error, cancelled, timed_out, or hung (progress was reported, then nothing for ``hang_after_s``).
* ``transactional=True`` wraps the script in one editor transaction and undoes it when the script raises (unless it
  called ``unreal_mcp.commit()``), Epic's ``_TransactionScope`` model: snapshot the undoable-entry count first so a
  no-op never pops someone else's entry, and only undo as the outermost transaction.

This module has no ``from __future__ import annotations``: FastMCP 1.5 finds the Context parameter by identity of its
annotation, which a string annotation defeats.
"""

import asyncio
import functools
import inspect
import json
import logging
import re
import socket
import time
import uuid
from typing import Any

from . import call

logger = logging.getLogger("UnrealMCP")

# Knobs the client passes per call. build_script prepends them as _MCP_OPTS; the native toolset provider
# (UnrealMCPToolsets) uses the wrapper without them, which means: no job id, no transaction.
_WRAPPER = r'''
import json as _mcp_json, sys as _mcp_sys, io as _mcp_io, traceback as _mcp_tb, time as _mcp_time
_mcp_o = dict(globals().get("_MCP_OPTS") or {})
_mcp_t0 = _mcp_time.monotonic()
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
try:
    import unreal as _mcp_unreal
except ImportError:
    _mcp_unreal = None
_mcp_lib = getattr(_mcp_unreal, "MCPPythonJobLibrary", None)
class _McpCancelled(BaseException):
    """The execute_python job was cancelled (MCP cancel or timeout). A BaseException, so a script's
    `except Exception:` does not swallow it."""
class _McpJob:
    """`unreal_mcp` inside execute_python scripts: progress(), cancelled(), check_cancelled(), commit(), elapsed()."""
    Cancelled = _McpCancelled
    def __init__(self, job_id):
        self.job_id = job_id
        self.reports = 0
        self.last = None
        self.committed = False
        self._cancel = False
    def elapsed(self):
        return _mcp_time.monotonic() - _mcp_t0
    def progress(self, done=None, total=None, message=""):
        """Report progress: progress(0.25), progress(12, 40, "tile 12/40") or progress(message="importing").
        Raises unreal_mcp.Cancelled once the job has been cancelled."""
        if done is None:
            done = self.last["progress"] if self.last else 0.0
        self.reports += 1
        self.last = {"progress": float(done), "total": float(total) if total else None,
                     "message": str(message), "at_s": round(self.elapsed(), 1)}
        if _mcp_lib is not None and self.job_id:
            try:
                if _mcp_lib.report_progress(self.job_id, float(done), float(total or 0.0), str(message)):
                    self._cancel = True
            except _McpCancelled:
                raise
            except Exception:
                pass
        if self._cancel:
            raise _McpCancelled("execute_python job cancelled")
    def cancelled(self):
        if not self._cancel and _mcp_lib is not None and self.job_id:
            try:
                self._cancel = bool(_mcp_lib.is_cancel_requested(self.job_id))
            except Exception:
                pass
        return self._cancel
    def check_cancelled(self):
        if self.cancelled():
            raise _McpCancelled("execute_python job cancelled")
    def commit(self):
        """transactional=True: keep the edits made so far even if the script raises afterwards."""
        self.committed = True
_mcp_job = _McpJob(_mcp_o.get("job_id"))
_mcp_buf = _mcp_io.StringIO()
_mcp_old = _mcp_sys.stdout
_mcp_sys.stdout = _mcp_buf
_mcp_r = {"ok": True}
_mcp_g = {"__name__": "__mcp__", "unreal_mcp": _mcp_job}
_mcp_exc = None
_mcp_txn = None
_mcp_scoped = None
try:
    if _mcp_unreal is not None:
        _mcp_g["unreal"] = _mcp_unreal
    if _mcp_o.get("transaction") is not None:
        _mcp_txn = {"description": _mcp_o["transaction"], "rolled_back": False}
        if _mcp_lib is not None:
            _mcp_txn["outermost"] = not _mcp_lib.is_transaction_active()
            _mcp_txn["undoable_before"] = _mcp_lib.get_undoable_transaction_count()
            _mcp_lib.begin_transaction(_mcp_o["transaction"])
            _mcp_txn["open"] = True
        elif _mcp_unreal is not None:
            _mcp_scoped = _mcp_unreal.ScopedEditorTransaction(_mcp_o["transaction"])
            _mcp_scoped.__enter__()
            _mcp_txn["rollback_unavailable"] = "old plugin: one undo step, no rollback"
        else:
            _mcp_txn["rollback_unavailable"] = "no unreal module"
    exec(compile(__MCP_SRC__, "<mcp>", "exec"), _mcp_g)
    _mcp_r["result"] = _mcp_g.get("result")
except BaseException as _mcp_e:
    _mcp_exc = _mcp_e
    _mcp_r["ok"] = False
    _mcp_r["traceback"] = _mcp_tb.format_exc()
finally:
    _mcp_sys.stdout = _mcp_old
    if _mcp_txn is not None:
        try:
            if _mcp_txn.pop("open", False):
                _mcp_lib.end_transaction()
            elif _mcp_scoped is not None:
                _mcp_scoped.__exit__(None, None, None)
            _mcp_txn["committed"] = _mcp_job.committed
            if _mcp_exc is not None and not _mcp_job.committed and _mcp_lib is not None:
                if not _mcp_txn.get("outermost"):
                    _mcp_txn["not_rolled_back"] = "nested inside an outer transaction; the outer owner decides"
                elif _mcp_lib.get_undoable_transaction_count() <= _mcp_txn["undoable_before"]:
                    _mcp_txn["not_rolled_back"] = "the script recorded no undoable change"
                else:
                    _mcp_txn["rolled_back"] = bool(_mcp_lib.undo_last_transaction())
        except BaseException:
            _mcp_txn["error"] = _mcp_tb.format_exc()
_mcp_r["stdout"] = _mcp_buf.getvalue()
_mcp_el = _mcp_job.elapsed()
_mcp_r["job"] = {"elapsed_s": round(_mcp_el, 2), "reports": _mcp_job.reports}
if _mcp_job.last is not None:
    _mcp_r["job"]["last_progress"] = _mcp_job.last
if isinstance(_mcp_exc, _McpCancelled):
    _mcp_r["job"]["cancelled"] = True
if _mcp_txn is not None:
    _mcp_r["transaction"] = _mcp_txn
try:
    _mcp_line = _mcp_json.dumps(_mcp_r, default=_mcp_default)
except Exception as _mcp_e:
    _mcp_line = _mcp_json.dumps({"ok": False, "traceback": "result not serializable: %r" % (_mcp_e,),
                                 "stdout": _mcp_r.get("stdout", ""), "job": _mcp_r.get("job")})
print(__MCP_MARK__ + _mcp_line)
# Drop every reference the script made. Functions defined by the script hold its globals dict in a reference cycle,
# so without this, wrappers of editor objects linger until CPython's cyclic GC runs at some random later point,
# possibly after the engine has destroyed those objects (re-imports, level switches): a crash in _PyWeakref_ClearRef.
# Collecting right here, while those objects are still alive, is the safe point: always when the caller asked
# (__MCP_GC__), and for any script that ran a while or that the client saw delete/import/rename assets (gc_hint).
_mcp_g.clear()
_mcp_exc = None
_mcp_gc_now = bool(_mcp_o.get("gc_hint") or _mcp_el > float(_mcp_o.get("gc_after_s", 1e9)))
del _mcp_g, _mcp_r, _mcp_line, _mcp_default, _mcp_job, _mcp_txn, _mcp_scoped
if __MCP_GC__:
    import gc as _mcp_gc
    _mcp_gc.collect()
    del _mcp_gc
elif _mcp_gc_now:
    import gc as _mcp_gc
    _mcp_gc.collect()
    del _mcp_gc
'''

# Scripts that make engine objects go away under live Python wrappers: collect right after them (see the wrapper).
_GC_HINT = re.compile(
    r"\b(delete_asset|delete_directory|delete_loaded_asset|delete_loaded_assets|delete_assets|force_delete|"
    r"consolidate_assets|rename_asset|rename_assets|rename_directory|ObjectTools|AssetImportTask|import_asset_tasks|"
    r"reimport|load_level|new_level|load_map|collect_garbage)\b")
GC_AFTER_S = 5.0          # ...and after any script that ran this long (its cost is noise next to the run)
DEFAULT_HANG_AFTER_S = 120.0
POLL_S = 2.0              # progress relay interval while a client wants progress
SIDE_TIMEOUT_S = 3.0


def build_script(code: str, marker: str, collect: bool = False, *, job_id: str | None = None,
                 transaction: str | None = None, gc_hint: bool | None = None,
                 gc_after_s: float | None = None) -> str:
    """Wrap user code so its result/stdout/traceback come back as one marked JSON line.
    collect=True also runs CPython's cyclic GC afterwards, while the objects the script touched are still alive.
    With no keyword options the script is the same as before job support (plus the always-present unreal_mcp helper)."""
    body = (_WRAPPER
            .replace("__MCP_SRC__", json.dumps(code))
            .replace("__MCP_MARK__", json.dumps(marker))
            .replace("__MCP_GC__", "True" if collect else "False"))
    opts: dict[str, Any] = {}
    if job_id:
        opts["job_id"] = job_id
    if transaction is not None:
        opts["transaction"] = transaction
    if gc_hint:
        opts["gc_hint"] = True
    if gc_after_s is not None:
        opts["gc_after_s"] = float(gc_after_s)
    return (f"_MCP_OPTS = {opts!r}\n" + body) if opts else body


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


# ------------------------------------------------------------------------------------------ job side channel

def job_command(get_conn, command: str, params: dict[str, Any],
                timeout: float = SIDE_TIMEOUT_S) -> dict[str, Any] | None:
    """python_job_status / python_job_cancel on a short-lived side connection (the main one is blocked by the running
    script). None when the editor does not answer or its plugin predates the job registry."""
    try:
        conn = get_conn()
        side = getattr(conn, "side_command", None)
        if side is None:
            return None
        reply = side(command, params, timeout=timeout)
    except Exception as e:  # never let observability break the call itself
        logger.debug("%s failed: %s", command, e)
        return None
    if isinstance(reply, dict) and reply.get("status") == "success" and isinstance(reply.get("result"), dict):
        return reply["result"]
    return None


def classify_stall(status: dict[str, Any] | None, hang_after_s: float) -> tuple[str, str]:
    """(outcome, note) for a call that ran out of time: hung = it reported progress, then went quiet."""
    if not status or not status.get("known"):
        return "timed_out", ("no job status from the editor (plugin predates python_job_status?): "
                             "cannot tell slow from hung")
    if not status.get("reports"):
        return "timed_out", ("the script reported no progress; call unreal_mcp.progress() in long loops so a hang can "
                             "be told from slow work")
    age = float(status.get("last_report_age_s") or 0.0)
    if age >= hang_after_s:
        return "hung", (f"last progress {age:.0f}s ago ({status.get('message') or 'no message'}), "
                        f"past hang_after_s={hang_after_s:g}")
    return "timed_out", f"still progressing (last report {age:.0f}s ago: {status.get('message') or 'no message'})"


def _is_timeout(err: str) -> bool:
    return "timed out" in err


def run_python(get_conn, code: str, timeout_s: float = 60.0, collect: bool = False, *, job_id: str | None = None,
               transactional: bool = False, hang_after_s: float = DEFAULT_HANG_AFTER_S,
               gc_auto: bool = False) -> dict[str, Any]:
    """Run code in the editor; returns {success, result, stdout, traceback?, log_errors?}.

    With job_id (execute_python always sets one) the result also carries outcome, elapsed_s, progress and job_id, and a
    timed-out call is classified as timed_out vs hung from the plugin's job registry. gc_auto collects after scripts
    that delete/import/rename assets or ran longer than GC_AFTER_S."""
    marker = f"<<MCP:{uuid.uuid4().hex}>>"
    script = build_script(code, marker, collect, job_id=job_id,
                          transaction=("MCP: execute_python" if transactional else None),
                          gc_hint=bool(gc_auto and _GC_HINT.search(code)),
                          gc_after_s=(GC_AFTER_S if gc_auto else None))
    params: dict[str, Any] = {"code": script, "mode": "file", "timeout_s": timeout_s}
    if job_id:
        params["job_id"] = job_id
    t0 = time.monotonic()
    reply = call(get_conn, "execute_python", params)
    if not reply.get("success"):
        if job_id:
            reply["job_id"] = job_id
            reply["elapsed_s"] = round(time.monotonic() - t0, 1)
            err = str(reply.get("error", ""))
            if _is_timeout(err) or reply.get("editor_unresponsive"):
                status = job_command(get_conn, "python_job_status", {"job_id": job_id})
                outcome, note = classify_stall(status, hang_after_s)
                reply["outcome"], reply["outcome_detail"] = outcome, note
                if status and status.get("known"):
                    reply["job"] = status
                    reply["still_running"] = bool(status.get("running"))
                    if status.get("running"):
                        reply["hint"] = (reply.get("hint") or "") + (
                            "The script is still running in the editor; the plugin asked it to cancel (it stops at its "
                            "next unreal_mcp.progress()/cancelled() check). "
                            f"Poll python_job_status(job_id={job_id!r}).")
        return reply
    log = reply.get("log", [])
    parsed = parse_output(log, marker)
    errors = [e.get("output", "") for e in log
              if e.get("type") == "Error" and marker not in e.get("output", "")]
    if parsed is None:
        out_err: dict[str, Any] = {"success": False,
                                   "error": "script produced no result marker (editor Python crashed or timed out?)",
                                   "log": log[-50:]}
        if job_id:
            out_err.update(job_id=job_id, outcome="error")
        return out_err
    out: dict[str, Any] = {"success": bool(parsed.get("ok")), "result": parsed.get("result"),
                           "stdout": parsed.get("stdout", "")}
    if parsed.get("traceback"):
        out["traceback"] = parsed["traceback"]
    if errors:
        out["log_errors"] = errors[-20:]
    if job_id:
        job = parsed.get("job") or {}
        out["job_id"] = job_id
        out["outcome"] = "ok" if parsed.get("ok") else ("cancelled" if job.get("cancelled") else "error")
        if "elapsed_s" in job:
            out["elapsed_s"] = job["elapsed_s"]
        if job.get("reports"):
            out["progress"] = {"reports": job["reports"], **(job.get("last_progress") or {})}
    if parsed.get("transaction") is not None:
        out["transaction"] = parsed["transaction"]
    return out


def run_editor_script(get_conn, name: str, args: dict[str, Any], timeout_s: float = 120.0,
                      collect: bool = False) -> dict[str, Any]:
    """Run one of unreal_mcp/editor_scripts/<name>.py with ARGS=args; flatten its `result` dict.
    A script reports a failure it detected itself with result["success"] = False and result["error"]."""
    from ..editor_scripts import render
    out = run_python(get_conn, render(name, args), timeout_s, collect)
    if not out.get("success"):
        return out
    res = out.get("result")
    flat: dict[str, Any] = {"success": True}
    if isinstance(res, dict):
        flat.update(res)
    else:
        flat["result"] = res
    if out.get("log_errors"):
        flat["log_errors"] = out["log_errors"]
    return flat


# ------------------------------------------------------------------------------------------ MCP relay

def _progress_token(ctx: Any) -> Any:
    try:
        meta = ctx.request_context.meta if ctx is not None else None
    except Exception:  # outside a request (direct FastMCP.call_tool): no client to notify
        return None
    return getattr(meta, "progressToken", None) if meta is not None else None


async def _notify(ctx: Any, token: Any, status: dict[str, Any]) -> None:
    session = ctx.request_context.session
    send = session.send_progress_notification
    total = status.get("total")
    kwargs: dict[str, Any] = {"progress_token": token, "progress": float(status.get("progress") or 0.0),
                              "total": float(total) if total else None}
    if "message" in inspect.signature(send).parameters:  # newer mcp SDKs carry a message; 1.5.0 does not
        msg = status.get("message") or ""
        kwargs["message"] = msg
    await send(**kwargs)


def _drop_main_socket(get_conn) -> None:
    """Unblock the worker thread waiting on the main socket, so later tools are not stuck behind its lock.
    The plugin drops the reply it would have sent."""
    try:
        sock = getattr(get_conn(), "socket", None)
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass


async def run_python_async(get_conn, code: str, timeout_s: float, transactional: bool, hang_after_s: float,
                           ctx: Any = None) -> dict[str, Any]:
    """run_python in a worker thread (the event loop stays free for cancellation), relaying job progress to the
    client while it waits. On MCP cancellation: flag the job in the editor, release the socket, re-raise."""
    job_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, functools.partial(
        run_python, get_conn, code, timeout_s, False, job_id=job_id, transactional=transactional,
        hang_after_s=hang_after_s, gc_auto=True))
    token = _progress_token(ctx)
    try:
        if token is None:
            return await fut
        seen = 0
        relay = True
        while True:
            done, _ = await asyncio.wait({fut}, timeout=POLL_S)
            if done:
                return fut.result()
            if not relay:
                continue
            status = await loop.run_in_executor(None, job_command, get_conn, "python_job_status", {"job_id": job_id})
            if status is None:
                relay = False  # old plugin / no side channel: just wait
                continue
            if int(status.get("reports") or 0) > seen:
                seen = int(status["reports"])
                try:
                    await _notify(ctx, token, status)
                except Exception as e:
                    logger.debug("progress notification failed: %s", e)
    except asyncio.CancelledError:
        # Synchronous on purpose: under anyio a cancelled scope re-cancels every further await.
        job_command(get_conn, "python_job_cancel", {"job_id": job_id, "reason": "cancelled by the MCP client"})
        _drop_main_socket(get_conn)
        raise


def register(mcp, get_conn):
    doc = """Execute Unreal Python in the live editor. The universal tool.

        Use when no dedicated tool covers the task (materials, Niagara, sequencer, asset
        import, editor utilities, anything in the `unreal` module). `unreal` is
        pre-imported. Assign to `result` to return data; print() output is captured.
        Wrap edits in `with unreal.ScopedEditorTransaction("desc"):` so they can be undone,
        or pass transactional=true: one undo step for the whole script, rolled back if it
        raises (call unreal_mcp.commit() to keep partial work).
        Look up APIs first with unreal_api_search if unsure of names.

        Long scripts: call `unreal_mcp.progress(done, total, "message")` in loops. It is
        relayed as MCP progress and lets a hang be told from slow work; once the call is
        cancelled or times out it raises unreal_mcp.Cancelled (or poll unreal_mcp.cancelled()).
        hang_after_s: seconds without progress after which a timed-out run counts as hung.

        Returns {success, result, stdout, traceback?, log_errors?, outcome, elapsed_s,
        progress?, job_id, transaction?}. outcome: ok | error | cancelled | timed_out | hung.
        """

    if hasattr(mcp, "get_context"):  # a real FastMCP server: async, so progress and cancellation work
        from mcp.server.fastmcp import Context

        async def execute_python(code: str, timeout_s: float = 60.0, transactional: bool = False,
                                 hang_after_s: float = DEFAULT_HANG_AFTER_S,
                                 ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
            return await run_python_async(get_conn, code, timeout_s, transactional, hang_after_s, ctx)
    else:  # plain collectors (bench, build scripts, tests) call tools synchronously
        def execute_python(code: str, timeout_s: float = 60.0, transactional: bool = False,
                           hang_after_s: float = DEFAULT_HANG_AFTER_S) -> dict[str, Any]:
            return run_python(get_conn, code, timeout_s, job_id=uuid.uuid4().hex, transactional=transactional,
                              hang_after_s=hang_after_s, gc_auto=True)
    execute_python.__doc__ = doc
    mcp.tool()(execute_python)

    @mcp.tool()
    def python_job_status(job_id: str, cancel: bool = False) -> dict[str, Any]:
        """Progress of an execute_python job (job_id from its result), answered even while the script still runs.
        cancel=true asks the script to stop at its next unreal_mcp.progress()/cancelled() check.
        Returns {success, known, running, elapsed_s, reports, progress, total, message, last_report_age_s,
        cancel_requested}."""
        status = job_command(get_conn, "python_job_cancel" if cancel else "python_job_status",
                             {"job_id": job_id, **({"reason": "python_job_status(cancel=true)"} if cancel else {})})
        if status is None:
            return {"success": False, "error": "the editor did not answer python_job_status (not running, or a plugin "
                                               "older than the job registry)"}
        return {"success": True, **status}
