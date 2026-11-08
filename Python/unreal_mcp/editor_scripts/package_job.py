# Package the project with UAT BuildCookRun, detached, on the EDITOR machine (works for remote editors).
# ARGS: action: start | status, job (status), platform, configuration, output_dir, maps [..], extra_args, tail
# ruff: noqa: F821
import json as _json
import os as _os
import re as _re
import subprocess as _sp
import sys as _sys
import time as _time

_proj_file = unreal.Paths.convert_relative_path_to_full(unreal.Paths.get_project_file_path())
_proj_dir = _os.path.dirname(_proj_file)
_jobs_dir = _os.path.join(_proj_dir, "Saved", "MCPPackage")
_os.makedirs(_jobs_dir, exist_ok=True)
_engine = _os.path.realpath(unreal.Paths.convert_relative_path_to_full(unreal.Paths.engine_dir()))
_win = _sys.platform.startswith("win")
_uat = _os.path.join(_engine, "Build", "BatchFiles", "RunUAT.bat" if _win else "RunUAT.sh")
_host_platform = "Win64" if _win else ("Mac" if _sys.platform == "darwin" else "Linux")


def _alive(pid):
    if _win:
        out = _sp.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        _os.kill(pid, 0)
    except OSError:
        return False
    try:  # reap if it is our (finished) child
        return _os.waitpid(pid, _os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


_action = ARGS.get("action") or "start"
if _action == "start":
    _job = _time.strftime("%Y%m%d-%H%M%S")
    _platform = ARGS.get("platform") or _host_platform
    _config = ARGS.get("configuration") or "Development"
    _out = ARGS.get("output_dir") or _os.path.join(_proj_dir, "Saved", "MCPPackage", _job, "Build")
    _log = _os.path.join(_jobs_dir, f"{_job}.log")
    _cmd = [_uat, "BuildCookRun", f"-project={_proj_file}", "-noP4", f"-platform={_platform}",
            f"-clientconfig={_config}", "-build", "-cook", "-stage", "-pak", "-archive",
            f"-archivedirectory={_out}", "-utf8output", "-unattended", "-nocompileeditor"]
    if ARGS.get("maps"):
        _cmd.append("-map=" + "+".join(ARGS["maps"]))
    _cmd += [a for a in (ARGS.get("extra_args") or "").split() if a]
    _fh = open(_log, "w", encoding="utf-8")
    _kw = {"creationflags": 0x00000008 | 0x00000200} if _win else {"start_new_session": True}
    _p = _sp.Popen(_cmd, stdout=_fh, stderr=_sp.STDOUT, cwd=_engine, **_kw)
    _meta = {"job": _job, "pid": _p.pid, "platform": _platform, "configuration": _config, "output_dir": _out,
             "log": _log, "command": " ".join(_cmd), "started": _time.time()}
    with open(_os.path.join(_jobs_dir, f"{_job}.json"), "w") as _f:
        _json.dump(_meta, _f)
    result = _meta
else:
    _job = ARGS.get("job") or ""
    if not _job:
        _all = sorted(f[:-5] for f in _os.listdir(_jobs_dir) if f.endswith(".json"))
        if not _all:
            raise LookupError("no packaging jobs yet")
        _job = _all[-1]
    with open(_os.path.join(_jobs_dir, f"{_job}.json")) as _f:
        _meta = _json.load(_f)
    with open(_meta["log"], encoding="utf-8", errors="replace") as _f:
        _text = _f.read()
    _lines = _text.splitlines()
    _running = _alive(_meta["pid"])
    _m = _re.findall(r"AutomationTool exiting with ExitCode=(-?\d+)", _text)
    _exit = int(_m[-1]) if _m else None
    if _running:
        _state = "running"
    elif _exit == 0 or "BUILD SUCCESSFUL" in _text:
        _state = "succeeded"
    else:
        _state = "failed"
    _errors = [ln.strip() for ln in _lines if _re.search(r"\b(Error|error)\b[:\s]|ERROR:|BUILD FAILED", ln)][-40:]
    _st = _re.findall(r"\*{5,}\s*([A-Z]+) COMMAND (STARTED|COMPLETED)", _text)
    _stage = f"{_st[-1][0].lower()} {_st[-1][1].lower()}" if _st else None
    _files = []
    if _state == "succeeded" and _os.path.isdir(_meta["output_dir"]):
        for _root, _dirs, _fs in _os.walk(_meta["output_dir"]):
            for _n in _fs:
                if _n.endswith((".sh", ".exe", ".app")):
                    _files.append(_os.path.join(_root, _n))
    result = {**_meta, "state": _state, "exit_code": _exit, "stage": _stage,
              "elapsed_s": round(_time.time() - _meta["started"], 1), "errors": _errors,
              "log_tail": _lines[-int(ARGS.get("tail") or 40):], "launchers": _files[:10]}
