"""package_job editor script: the UAT command line on Win64 and Linux, and status parsing.

Runs the real script text against a minimal fake `unreal` (only Paths is used) with Popen captured,
so the Windows branch is exercised on any OS."""

import json
import subprocess
import sys
import types

import pytest

from unreal_mcp.editor_scripts import render


class _Popen:
    calls: list = []

    def __init__(self, cmd, **kw):
        _Popen.calls.append((cmd, kw))
        self.pid = 4242


@pytest.fixture
def project(tmp_path, monkeypatch):
    proj = tmp_path / "My Game"
    proj.mkdir()
    engine = tmp_path / "Epic Games" / "UE_5.8" / "Engine"
    engine.mkdir(parents=True)
    paths = types.SimpleNamespace(
        get_project_file_path=lambda: str(proj / "MyGame.uproject"),
        convert_relative_path_to_full=lambda p: p,
        engine_dir=lambda: str(engine),
        project_dir=lambda: str(proj))
    monkeypatch.setitem(sys.modules, "unreal", types.SimpleNamespace(Paths=paths))
    _Popen.calls = []
    monkeypatch.setattr(subprocess, "Popen", _Popen)
    return proj, engine


def _run(args):
    scope = {"__name__": "__editor__"}
    exec(compile(render("package_job", args), "package_job", "exec"), scope)
    return scope["result"]


def test_win64_start_goes_through_cmd_with_whole_line_quoted(project, monkeypatch):
    proj, engine = project
    monkeypatch.setattr(sys, "platform", "win32")
    r = _run({"action": "start", "platform": "windows", "maps": ["/Game/Maps/Arena"]})
    assert r["platform"] == "Win64" and r["pid"] == 4242
    (cmd, kw), = _Popen.calls
    assert isinstance(cmd, str) and cmd.startswith('cmd.exe /d /s /c "') and cmd.endswith('"')
    assert "RunUAT.bat" in cmd and "-platform=Win64" in cmd and "-map=/Game/Maps/Arena" in cmd
    # paths with spaces stay quoted as single arguments inside the line
    assert subprocess.list2cmdline([f"-project={proj / 'MyGame.uproject'}"]) in cmd
    assert kw["creationflags"] & 0x08000000 and "start_new_session" not in kw
    meta = json.loads((proj / "Saved" / "MCPPackage" / f"{r['job']}.json").read_text(encoding="utf-8"))
    assert meta["command"].startswith(subprocess.list2cmdline([str(engine / "Build" / "BatchFiles" / "RunUAT.bat")]))


def test_linux_start_uses_runuat_sh_argv(project, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    r = _run({"action": "start"})
    (cmd, kw), = _Popen.calls
    assert r["platform"] == "Linux" and cmd[0].endswith("RunUAT.sh") and "-platform=Linux" in cmd
    assert kw["start_new_session"] is True


def test_status_reports_success_and_top_level_launcher_first(project, monkeypatch):
    proj, _ = project
    monkeypatch.setattr(sys, "platform", "linux")
    jobs = proj / "Saved" / "MCPPackage"
    jobs.mkdir(parents=True)
    out = proj / "Build"
    (out / "Windows" / "MyGame" / "Binaries" / "Win64").mkdir(parents=True)
    (out / "Windows" / "MyGame" / "Binaries" / "Win64" / "MyGame.exe").write_text("")
    (out / "Windows" / "Engine" / "Binaries" / "Win64").mkdir(parents=True)
    (out / "Windows" / "Engine" / "Binaries" / "Win64" / "CrashReportClient.exe").write_text("")
    (out / "Windows" / "MyGame.exe").write_text("")
    log = jobs / "j1.log"
    log.write_text("********** BUILD COMMAND STARTED **********\n********** ARCHIVE COMMAND COMPLETED **********\n"
                   "BUILD SUCCESSFUL\nAutomationTool exiting with ExitCode=0 (Success)\n", encoding="utf-8")
    (jobs / "j1.json").write_text(json.dumps({"job": "j1", "pid": 2 ** 22 + 12345, "output_dir": str(out),
                                              "log": str(log), "started": 0}), encoding="utf-8")
    r = _run({"action": "status"})
    assert r["state"] == "succeeded" and r["exit_code"] == 0 and r["stage"] == "archive completed"
    assert r["launchers"][0].endswith("MyGame.exe") and len(r["launchers"]) == 2
    assert not any("CrashReportClient" in f for f in r["launchers"])
