import base64
import json
import subprocess
import sys
import textwrap

from unreal_mcp.tools.python_exec import build_script, parse_output

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def run_wrapper(code: str, marker="<<M>>"):
    """Execute the generated wrapper in a real interpreter (with no `unreal` module)."""
    out = subprocess.run([sys.executable, "-I", "-c", build_script(code, marker)],
                         capture_output=True, text=True, timeout=30)
    return parse_output([{"type": "Info", "output": line} for line in out.stdout.splitlines()], marker)


def test_wrapper_returns_result_and_stdout():
    r = run_wrapper("print('hi')\nresult = {'n': 1 + 2}")
    assert r == {"ok": True, "result": {"n": 3}, "stdout": "hi\n"}


def test_wrapper_captures_traceback():
    r = run_wrapper("x = 1\nraise ValueError('boom')")
    assert r["ok"] is False
    assert "ValueError: boom" in r["traceback"]


def test_wrapper_handles_quotes_and_unicode_in_source():
    r = run_wrapper(textwrap.dedent('''
        s = """triple "quoted" 'x' \\n Dubhlán"""
        result = s
    '''))
    assert r["result"] == "triple \"quoted\" 'x' \n Dubhlán"


def test_wrapper_serializes_unreal_like_objects():
    r = run_wrapper(textwrap.dedent('''
        class Obj:
            def get_path_name(self): return "/Game/Thing.Thing"
        class Vec:
            def to_tuple(self): return (1.0, 2.0, 3.0)
        result = [Obj(), Vec(), object]
    '''))
    assert r["result"][0] == "/Game/Thing.Thing"
    assert r["result"][1] == [1.0, 2.0, 3.0]
    assert "object" in r["result"][2]


def test_execute_python_tool_end_to_end(fake, tools):
    def handler(p):
        assert p["mode"] == "file" and p["timeout_s"] == 5
        marker = json.loads(p["code"].split("print(")[-1].split(" + _mcp_line")[0])
        line = marker + json.dumps({"ok": True, "result": 42, "stdout": "x\n"})
        return {"command_result": "", "log": [{"type": "Info", "output": line},
                                               {"type": "Error", "output": "LogPython: Warning-ish"}]}

    fake.on("execute_python", handler)
    r = tools["execute_python"]("result = 42", timeout_s=5)
    assert r == {"success": True, "result": 42, "stdout": "x\n", "log_errors": ["LogPython: Warning-ish"]}


def test_execute_python_missing_marker(fake, tools):
    fake.on("execute_python", lambda p: {"log": [{"type": "Error", "output": "SyntaxError"}]})
    r = tools["execute_python"]("result = (")
    assert r["success"] is False and "no result marker" in r["error"]


def test_execute_python_plugin_without_command(fake, tools):
    r = tools["execute_python"]("result = 1")
    assert r["success"] is False and "Unknown command" in r["error"]


def test_reflection_tools_send_expected_params(fake, tools):
    seen = {}
    for cmd in ("object_describe", "property_get", "property_set", "function_call"):
        fake.on(cmd, lambda p, cmd=cmd: seen.setdefault(cmd, p) and {"ok": cmd})
    assert tools["object_describe"](path="PointLight_1", filter="intens")["success"]
    tools["property_get"]("PointLight_1", "LightComponent.Intensity")
    tools["property_set"]("PointLight_1", "LightComponent.Intensity", 5000)
    tools["function_call"]("/Script/Engine.Default__KismetSystemLibrary", "GetEngineVersion")
    assert seen["object_describe"]["filter"] == "intens"
    assert seen["property_set"] == {"path": "PointLight_1", "property": "LightComponent.Intensity", "value": 5000}
    assert seen["function_call"]["args"] == {}


def test_editor_action_validation(fake, tools):
    assert tools["editor_action"]("explode")["success"] is False
    fake.on("editor_action", lambda p: {"action": p["action"], "count": p["count"]})
    assert tools["editor_action"]("undo", count=3) == {"success": True, "action": "undo", "count": 3}


def test_unreal_status_against_legacy_plugin(fake, tools):
    r = tools["unreal_status"]()
    assert r["success"] and r["plugin_version"] == "legacy" and r["commands"] == []


def test_unreal_status_against_new_plugin(fake, tools):
    fake.on("list_commands", lambda p: {"commands": ["ping", "execute_python"], "plugin_version": "0.2.0",
                                        "python_available": True})
    r = tools["unreal_status"]()
    assert r["plugin_version"] == "0.2.0" and "execute_python" in r["commands"]


def test_viewport_capture_returns_image(fake, tools):
    fake.on("viewport_capture", lambda p: {"width": 1, "height": 1, "format": "png",
                                           "data_base64": base64.b64encode(PNG_1PX).decode()})
    img = tools["viewport_capture"]()
    content = img.to_image_content()
    assert content.mimeType == "image/png"
    assert base64.b64decode(content.data) == PNG_1PX


def test_editor_log_passthrough(fake, tools):
    fake.on("editor_log", lambda p: {"entries": [{"seq": 7, "message": "hi"}], "last_seq": 7, "echo": p})
    r = tools["editor_log"](lines=10, min_verbosity="Warning", since_seq=3)
    assert r["last_seq"] == 7 and r["echo"]["min_verbosity"] == "Warning" and r["echo"]["since_seq"] == 3
