"""Long execute_python jobs: progress relay, cooperative cancel, timed_out vs hung, transactional rollback, GC guard."""

import asyncio
import json
import subprocess
import sys
import textwrap
import threading
import time
import types

import pytest

from unreal_mcp.tools import python_exec as pe

# A stand-in `unreal` module with the plugin's MCPPythonJobLibrary. State goes to stdout as one STATE line.
STUB = textwrap.dedent('''
    import sys, types, json, atexit
    _st = {"reports": [], "cancel_after": CANCEL_AFTER, "active": ACTIVE, "undoable": UNDOABLE, "txn": [], "undone": 0}
    class _Lib:
        @staticmethod
        def report_progress(job, done, total, msg):
            _st["reports"].append([job, done, total, msg])
            return _st["cancel_after"] is not None and len(_st["reports"]) >= _st["cancel_after"]
        @staticmethod
        def is_cancel_requested(job):
            return _st["cancel_after"] == 0
        @staticmethod
        def is_transaction_active():
            return _st["active"]
        @staticmethod
        def get_undoable_transaction_count():
            return _st["undoable"]
        @staticmethod
        def begin_transaction(desc):
            _st["txn"].append("begin:" + desc); return 0
        @staticmethod
        def end_transaction():
            _st["txn"].append("end"); return 0
        @staticmethod
        def undo_last_transaction():
            _st["undone"] += 1; _st["undoable"] -= 1; return True
    class _ScopedEditorTransaction:
        def __init__(self, d): _st["txn"].append("scoped:" + d)
        def __enter__(self): return self
        def __exit__(self, *a): _st["txn"].append("scoped_end")
    unreal = types.ModuleType("unreal")
    unreal.ScopedEditorTransaction = _ScopedEditorTransaction
    if WITH_LIB:
        unreal.MCPPythonJobLibrary = _Lib
    def _edit():
        _st["undoable"] += 1
    unreal.fake_edit = _edit
    sys.modules["unreal"] = unreal
    atexit.register(lambda: print("STATE" + json.dumps(_st)))
''')


def run(code, *, job_id="J1", transaction=None, cancel_after=None, active=False, undoable=3, with_lib=True,
        collect=False, gc_hint=None):
    stub = (STUB.replace("CANCEL_AFTER", repr(cancel_after)).replace("ACTIVE", repr(active))
            .replace("UNDOABLE", repr(undoable)).replace("WITH_LIB", repr(with_lib)))
    script = stub + pe.build_script(code, "<<M>>", collect, job_id=job_id, transaction=transaction, gc_hint=gc_hint)
    out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=30).stdout
    lines = out.splitlines()
    parsed = pe.parse_output([{"output": line} for line in lines], "<<M>>")
    state = next(json.loads(line[5:]) for line in lines if line.startswith("STATE"))
    return parsed, state


def test_progress_reaches_the_plugin_and_the_result():
    r, st = run("for i in range(3):\n    unreal_mcp.progress(i + 1, 3, f'step {i + 1}')\nresult = 'done'")
    assert r["ok"] and r["result"] == "done"
    assert st["reports"] == [["J1", 1.0, 3.0, "step 1"], ["J1", 2.0, 3.0, "step 2"], ["J1", 3.0, 3.0, "step 3"]]
    assert r["job"]["reports"] == 3
    assert r["job"]["last_progress"]["message"] == "step 3" and r["job"]["last_progress"]["total"] == 3.0


def test_progress_works_without_the_plugin_library():
    r, st = run("unreal_mcp.progress(0.5, message='half')\nresult = unreal_mcp.cancelled()", with_lib=False)
    assert r["ok"] and r["result"] is False and r["job"]["last_progress"]["progress"] == 0.5 and not st["reports"]


def test_cancel_raises_through_except_exception():
    code = textwrap.dedent('''
        n = 0
        for i in range(100):
            try:
                n += 1
                unreal_mcp.progress(i, 100)
            except Exception:
                pass  # a broad handler must not swallow the cancel
        result = n
    ''')
    r, st = run(code, cancel_after=2)
    assert r["ok"] is False and r["job"]["cancelled"] is True and "Cancelled" in r["traceback"]
    assert len(st["reports"]) == 2


def test_cancelled_poll():
    r, _ = run("result = unreal_mcp.cancelled()", cancel_after=0)
    assert r["ok"] and r["result"] is True
    r, _ = run("unreal_mcp.check_cancelled()", cancel_after=0)
    assert r["ok"] is False and r["job"]["cancelled"]


def test_transaction_rolls_back_on_exception():
    r, st = run("unreal.fake_edit()\nraise RuntimeError('boom')", transaction="MCP: t")
    assert r["ok"] is False and r["transaction"]["rolled_back"] is True and st["undone"] == 1
    assert st["txn"] == ["begin:MCP: t", "end"]


def test_transaction_kept_on_success_and_on_commit():
    r, st = run("unreal.fake_edit()\nresult = 1", transaction="t")
    assert r["ok"] and r["transaction"]["rolled_back"] is False and st["undone"] == 0
    r, st = run("unreal.fake_edit()\nunreal_mcp.commit()\nraise RuntimeError('late')", transaction="t")
    assert r["ok"] is False and r["transaction"]["committed"] is True and st["undone"] == 0


def test_transaction_noop_never_pops_someone_elses_entry():
    r, st = run("raise RuntimeError('before any edit')", transaction="t", undoable=5)
    assert st["undone"] == 0 and "no undoable change" in r["transaction"]["not_rolled_back"]


def test_transaction_nested_is_left_to_the_outer_owner():
    r, st = run("unreal.fake_edit()\nraise RuntimeError('x')", transaction="t", active=True)
    assert st["undone"] == 0 and "nested" in r["transaction"]["not_rolled_back"]


def test_transaction_rolls_back_a_cancelled_job():
    r, st = run("unreal.fake_edit()\nunreal_mcp.progress(1)", transaction="t", cancel_after=1)
    assert r["job"]["cancelled"] and r["transaction"]["rolled_back"] and st["undone"] == 1


def test_transaction_with_an_old_plugin_is_one_undo_step():
    r, st = run("unreal.fake_edit()\nraise RuntimeError('x')", transaction="t", with_lib=False)
    assert st["txn"] == ["scoped:t", "scoped_end"] and "rollback_unavailable" in r["transaction"]


def test_build_script_defaults_unchanged_for_the_native_provider():
    s = pe.build_script("result = 1", "<<M>>")
    assert not s.startswith("_MCP_OPTS") and "__MCP_" not in s
    assert pe.build_script("x", "<<M>>", job_id="abc").startswith("_MCP_OPTS = {'job_id': 'abc'}\n")


def test_gc_hint_for_destructive_scripts(fake, conn):
    sent = []
    fake.on("execute_python", lambda p: sent.append(p) or {"log": []})
    pe.run_python(lambda: conn, "unreal.EditorAssetLibrary.delete_directory('/Game/X')", gc_auto=True, job_id="j")
    pe.run_python(lambda: conn, "result = 1", gc_auto=True, job_id="j")
    assert "'gc_hint': True" in sent[0]["code"] and "'gc_hint'" not in sent[1]["code"]
    assert sent[0]["job_id"] == "j"


def test_gc_hint_actually_collects():
    code = textwrap.dedent('''
        import gc, builtins
        builtins._seen = []
        gc.callbacks.append(lambda phase, info, b=builtins: phase == "stop" and b._seen.append(info["generation"]))
        result = 1
    ''')
    for hint, expect in ((True, True), (None, False)):
        script = (pe.build_script(code, "<<M>>", job_id="j", gc_hint=hint)
                  + "\nimport builtins\nprint('GEN2', 2 in builtins._seen)\n")
        out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=30).stdout
        assert f"GEN2 {expect}" in out, out


# ------------------------------------------------------------------------------------------ timeout classification

@pytest.mark.parametrize("status,outcome", [
    (None, "timed_out"),
    ({"known": True, "reports": 0}, "timed_out"),
    ({"known": True, "reports": 4, "last_report_age_s": 10, "message": "tile 4"}, "timed_out"),
    ({"known": True, "reports": 4, "last_report_age_s": 500, "message": "tile 4"}, "hung"),
])
def test_classify_stall(status, outcome):
    assert pe.classify_stall(status, 120)[0] == outcome


def test_timeout_reports_hung_from_the_job_registry(fake, conn):
    fake.on("execute_python", lambda p: {"success": False, "error": "execute_python timed out after 65s waiting for "
                                                                    "the game thread"})
    fake.on("python_job_status", lambda p: {"job_id": p["job_id"], "known": True, "running": True, "reports": 7,
                                            "last_report_age_s": 300.0, "message": "tile 7/40"})
    r = pe.run_python(lambda: conn, "while True: pass", 60, job_id="abc", hang_after_s=120)
    assert r["success"] is False and r["outcome"] == "hung" and r["still_running"] is True
    assert r["job"]["message"] == "tile 7/40" and "python_job_status" in r["hint"]


def test_timeout_with_an_old_plugin_is_timed_out(fake, conn):
    fake.on("execute_python", lambda p: {"success": False, "error": "execute_python timed out after 65s"})
    r = pe.run_python(lambda: conn, "x", 60, job_id="abc")
    assert r["outcome"] == "timed_out" and "job" not in r


def test_result_outcome_and_progress(fake, conn):
    def handler(p):
        marker = json.loads(p["code"].split("print(")[-1].split(" + _mcp_line")[0])
        body = {"ok": False, "traceback": "Cancelled", "stdout": "",
                "job": {"elapsed_s": 12.5, "reports": 3, "cancelled": True,
                        "last_progress": {"progress": 3.0, "total": 9.0, "message": "m", "at_s": 12.0}}}
        return {"log": [{"type": "Info", "output": marker + json.dumps(body)}]}
    fake.on("execute_python", handler)
    r = pe.run_python(lambda: conn, "x", job_id="abc")
    assert r["outcome"] == "cancelled" and r["elapsed_s"] == 12.5
    assert r["progress"] == {"reports": 3, "progress": 3.0, "total": 9.0, "message": "m", "at_s": 12.0}


def test_job_status_tool(fake, tools):
    fake.on("python_job_cancel", lambda p: {"job_id": p["job_id"], "known": True, "cancel_requested": True})
    r = tools["python_job_status"]("abc", cancel=True)
    assert r["success"] and r["cancel_requested"]
    assert tools["python_job_status"]("abc")["success"] is False  # fake has no python_job_status handler


# ------------------------------------------------------------------------------------------ async relay

class _Session:
    def __init__(self):
        self.sent = []

    async def send_progress_notification(self, progress_token, progress, total=None):
        self.sent.append((progress_token, progress, total))


def _ctx(token):
    session = _Session()
    rc = types.SimpleNamespace(meta=types.SimpleNamespace(progressToken=token), session=session)
    return types.SimpleNamespace(request_context=rc), session


def _slow_editor(fake, release: threading.Event, reports):
    def execute(p):
        release.wait(10)
        marker = json.loads(p["code"].split("print(")[-1].split(" + _mcp_line")[0])
        return {"log": [{"type": "Info", "output": marker + json.dumps({"ok": True, "result": 1, "stdout": ""})}]}
    state = {"n": 0}

    def status(p):
        state["n"] = min(state["n"] + 1, len(reports))
        n = state["n"]
        if n >= len(reports):
            release.set()
        return {"job_id": p["job_id"], "known": True, "running": True, "reports": n, **reports[n - 1]}
    fake.on("execute_python", execute)
    fake.on("python_job_status", status)


def test_async_relays_progress_notifications(fake, conn, monkeypatch):
    monkeypatch.setattr(pe, "POLL_S", 0.05)
    release = threading.Event()
    _slow_editor(fake, release, [{"progress": 1, "total": 4, "message": "a"},
                                 {"progress": 2, "total": 4, "message": "b"}])
    ctx, session = _ctx("tok")
    r = asyncio.run(pe.run_python_async(lambda: conn, "x", 30, False, 120, ctx))
    assert r["success"] and r["outcome"] == "ok"
    assert ("tok", 1.0, 4.0) in session.sent and ("tok", 2.0, 4.0) in session.sent


def test_async_without_progress_token_does_not_poll(fake, conn):
    release = threading.Event()
    release.set()
    _slow_editor(fake, release, [{"progress": 1}])
    polled = []
    fake.on("python_job_status", lambda p: polled.append(p) or {"known": True})
    ctx, session = _ctx(None)
    r = asyncio.run(pe.run_python_async(lambda: conn, "x", 30, False, 120, ctx))
    assert r["success"] and not polled and not session.sent


def test_async_cancel_flags_the_job(fake, conn):
    release = threading.Event()
    _slow_editor(fake, release, [{"progress": 1}])
    cancels = []
    fake.on("python_job_cancel", lambda p: cancels.append(p) or release.set() or {"known": True})

    async def main():
        task = asyncio.create_task(pe.run_python_async(lambda: conn, "x", 30, False, 120, None))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    t0 = time.monotonic()
    asyncio.run(main())
    assert cancels and cancels[0]["reason"] == "cancelled by the MCP client" and time.monotonic() - t0 < 8
