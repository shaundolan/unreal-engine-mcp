"""UnrealMCPToolsets manifest: in sync with the server, and each native backend sends what the Python tool sends."""

import json

import pytest

from unreal_mcp import native_toolsets as nt

SAMPLE = {"string": "Sample", "array": [{"op": "noop"}], "object": {"k": 1}, "integer": 3, "number": 1.5,
          "boolean": True}


def _manifest():
    return json.loads(nt.MANIFEST_PATH.read_text(encoding="utf-8"))


def _all_tools():
    return [(ts["name"], t) for ts in _manifest()["toolsets"] for t in ts["tools"]]


def _sample_args(schema):
    out = {}
    for name in schema.get("required", []):
        prop = schema["properties"][name]
        typ = prop.get("type") or next((a["type"] for a in prop.get("anyOf", []) if a.get("type") != "null"), "string")
        out[name] = SAMPLE.get(typ, "Sample")
    return out


def test_committed_manifest_is_current():
    """UnrealMCPToolsets/Resources/UnrealMCPToolsets.json must match `python -m unreal_mcp.native_toolsets`."""
    assert nt.MANIFEST_PATH.read_text(encoding="utf-8") == nt.dumps(nt.build_manifest())


def test_names_are_valid_toolset_and_mcp_names():
    for ts_name, tool in _all_tools():
        assert ts_name.startswith("UnrealMCP.") and "." not in tool["name"]
        full = f"{ts_name}.{tool['name']}"
        assert len(full) <= 128 and all(c.isalnum() or c in "_-." for c in full)
        assert tool["description"]


def _non_empty(d):
    return {k: v for k, v in d.items() if v not in ([], "", {}, None)}


class _Recorder:
    def __init__(self):
        self.sent = []

    def send_command(self, command, params, timeout=None):
        self.sent.append((command, params))
        return {"status": "success", "result": {}}


@pytest.mark.parametrize("ts_name,tool", _all_tools(), ids=lambda v: v["name"] if isinstance(v, dict) else v)
def test_native_backend_matches_python_tool(ts_name, tool, tools, monkeypatch):
    """Calling the Python tool and calling the native toolset must reach the same command/script with the same
    parameters: that is what makes the two front doors one code path."""
    from unreal_mcp import editor_scripts
    from unreal_mcp.tools import python_exec

    rec = _Recorder()
    scripts = []
    real_render = editor_scripts.render
    monkeypatch.setattr(editor_scripts, "render", lambda name, args: scripts.append((name, args)) or real_render(
        name, args))
    monkeypatch.setattr(python_exec, "run_python", lambda get_conn, code, timeout_s=60.0, collect=False:
                        {"success": True, "result": {}})
    fn = tools[tool["name"]]
    # Rebind the tool's connection getter to the recorder (the fixture's tools close over a real fake connection).
    args = _sample_args(tool["inputSchema"])
    backend = tool["backend"]
    native = nt.native_params(tool, args)
    if backend["kind"] == "frame_stats":
        fn.__globals__["capture_frame_stats"](lambda: rec, native["frames"], 0.01, sleep=lambda s: None)
        assert rec.sent[0] == ("frame_stats", {"action": "start", "frames": native["frames"]})
        return
    cells = dict(zip(fn.__code__.co_freevars, fn.__closure__ or (), strict=True))
    cells["get_conn"].cell_contents = lambda: rec
    fn(**args)
    if backend["kind"] == "command":
        assert rec.sent, "the Python tool sent nothing"
        command, params = rec.sent[0]
        assert command == backend["command"]
        # The Python wrappers spell "missing" as [] / "" / {} where the plugin drops nulls: same handler result.
        assert _non_empty(native) == _non_empty(params)
    else:
        name, sent_args = scripts[0]
        assert backend["script"] == nt.editor_script_body(name)
        assert _non_empty(native) == _non_empty(sent_args)
