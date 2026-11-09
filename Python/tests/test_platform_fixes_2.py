"""Second round of fixes from the city build: exact PIE render size + pie captures, ClearCoat and the other hidden
material outputs, re-import at a force-deleted path, spawn / possess in PIE, level_open after a streaming wrapper
map (plugin side) and the editor_unresponsive watchdog."""

import base64
import struct
import time
import zlib

import pytest
from test_editor_scripts import ue  # noqa: F401  (fixture)
from test_pie import pie  # noqa: F401  (fixture)

from unreal_mcp.tools import call
from unreal_mcp.tools.testing import run_pie_test

M = "/Game/M/M_Paint"


def _png(w, h):
    raw = b"".join(b"\x00" + b"\x10\x20\x30" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------------------------------- PIE size + capture

def test_pie_test_starts_pie_at_a_fixed_size_and_captures_frames(pie, ue, fake, conn, tmp_path):  # noqa: F811
    size = {}

    def capture(p):
        assert p["source"] == "pie"
        if p.get("width"):
            size["wh"] = (p["width"], p["height"])
        w, h = size.get("wh", (1280, 720))
        return {"width": w, "height": h, "format": "png", "data_base64": base64.b64encode(_png(w, h)).decode()}
    fake.on("viewport_capture", capture)
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None, width=1600, height=900,
                     capture_dir=str(tmp_path), steps=[{"capture": "start"}, {"capture": "small", "width": 320,
                                                                             "height": 180}])
    start = next(q["params"] for q in fake.requests if q["type"] == "editor_action"
                 and q["params"]["action"] == "pie_start")
    assert start == {"action": "pie_start", "mode": "new_window", "width": 1600, "height": 900}
    assert r["passed"], r
    s0, s1 = r["steps"]
    assert s0["capture"] == str(tmp_path / "start.png") and (tmp_path / "start.png").read_bytes()[:4] == b"\x89PNG"
    assert (s1["width"], s1["height"]) == (320, 180)
    caps = [q["params"] for q in fake.requests if q["type"] == "viewport_capture"]
    assert caps[1]["width"] == 320 and caps[1]["max_width"] >= 320  # never downsized below the asked size


def test_pie_test_capture_without_pie_fails_the_step(pie, ue, fake, conn, tmp_path):  # noqa: F811
    fake.on("viewport_capture", lambda p: {"success": False, "error": "source=pie needs a running PIE session"})
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None, capture_dir=str(tmp_path),
                     steps=[{"capture": "x"}])
    assert r["passed"] is False and "running PIE" in r["steps"][0]["error"]


def test_viewport_capture_and_editor_action_pass_the_size(fake, tools):
    fake.on("viewport_capture", lambda p: {"width": p.get("width", 8), "height": p.get("height", 8),
                                           "data_base64": base64.b64encode(_png(4, 4)).decode()})
    fake.on("editor_action", lambda p: dict(p))
    tools["viewport_capture"](source="pie", width=1920, height=1080)
    cap = [q["params"] for q in fake.requests if q["type"] == "viewport_capture"][-1]
    assert cap == {"max_width": 1920, "source": "pie", "width": 1920, "height": 1080}
    tools["viewport_capture"]()
    assert [q["params"] for q in fake.requests if q["type"] == "viewport_capture"][-1] == {"max_width": 1280,
                                                                                            "source": "auto"}
    r = tools["editor_action"]("pie_start", width=1600, height=900, mode="viewport")
    assert (r["width"], r["height"], r["mode"]) == (1600, 900, "viewport")
    r = tools["editor_action"]("undo")
    assert "width" not in r and "mode" not in r


# ------------------------------------------------------------------------------------------- spawn / possess

def test_pie_test_spawn_and_possess_steps(pie, ue, fake, conn):  # noqa: F811
    spawned = []

    def spawn(p):
        spawned.append(p)
        return {"name": "BP_Car_C_0", "label": p.get("label", ""), "has_begun_play": True,
                "properties_set": list(p.get("properties", {}))}
    fake.on("spawn_in_pie", spawn)
    fake.on("pie_possess", lambda p: {"possessed": True, "pawn": p["target"], "previous_pawn": "Walker_0"}
            if p["target"] == "Car" else {"success": False, "error": f"no actor labelled or named '{p['target']}'"})
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None, steps=[
        {"spawn": "/Game/BP_Car", "location": [1, 2, 3], "label": "Car", "properties": {"Speed": 900}},
        {"possess": "Car"},
        {"possess": "Ghost"},
        {"wait_s": 1}])
    assert spawned == [{"class": "/Game/BP_Car", "location": [1, 2, 3], "label": "Car", "properties": {"Speed": 900}}]
    s0, s1, s2 = r["steps"]  # a failing step stops the timeline
    assert s0["ok"] and s0["properties_set"] == ["Speed"] and s0["has_begun_play"]
    assert s1["ok"] and s1["previous_pawn"] == "Walker_0"
    assert not s2["ok"] and "Ghost" in s2["error"] and r["passed"] is False


def test_spawn_in_pie_and_pie_possess_tools(fake, tools):
    fake.on("spawn_in_pie", lambda p: dict(p))
    fake.on("pie_possess", lambda p: dict(p))
    r = tools["spawn_in_pie"]("Character", location=[0, 0, 90], properties={"CharacterMovement.MaxWalkSpeed": 300},
                              possess=True)
    assert r["class"] == "Character" and r["possess"] is True and r["collision"] == "always" and "label" not in r
    assert r["properties"] == {"CharacterMovement.MaxWalkSpeed": 300} and "rotation" not in r
    r = tools["pie_possess"]("Drone", player=1, destroy_previous=True)
    assert (r["target"], r["player"], r["destroy_previous"]) == ("Drone", 1, True)


# ------------------------------------------------------------------------------------------- material outputs

def _with_lib(u):
    """The plugin's unreal.MCPScriptLibrary, faked: hidden outputs land in the material's outputs by name."""
    def connect(e, out, name):
        key = {"clearcoat": "CustomData0", "clearcoatroughness": "CustomData1"}.get(name.lower(), name)
        if key not in ("CustomData0", "CustomData1", "PixelDepthOffset", "BaseColor", "FrontMaterial"):
            return f"unknown material output '{name}'; outputs: BaseColor, ..."
        mat = next(m for m in u._state["assets"].values() if isinstance(m, u.Material) and e in m.expressions)
        mat.outputs[key] = e
        return ""

    class MCPScriptLibrary:
        connect_material_output = staticmethod(connect)
        is_substrate_enabled = staticmethod(lambda: False)

        @staticmethod
        def get_connected_material_outputs(mat):
            return [f"{k}\tMaterialExpressionMultiply_{i}\t" for i, k in enumerate(mat.outputs)]

        @staticmethod
        def release_stale_asset_path(path):
            stale = u._state.setdefault("stale", set())
            if path in stale:
                stale.discard(path)
                return "released:1"
            return "absent"
    u.MCPScriptLibrary = MCPScriptLibrary
    return MCPScriptLibrary


CLEARCOAT_OPS = [{"op": "add", "id": "cc", "class": "Multiply"}, {"op": "add", "id": "ccr", "class": "Multiply"},
                 {"op": "output", "from": "cc", "property": "ClearCoat"},
                 {"op": "output", "from": "ccr", "property": "ClearCoatRoughness"}]


def test_material_edit_clear_coat_outputs_go_through_the_plugin_library(ue, tools):  # noqa: F811
    _with_lib(ue)
    r = tools["material_edit"](M, create=True, operations=[
        *CLEARCOAT_OPS, {"op": "add", "id": "c", "class": "Multiply"},
        {"op": "output", "from": "c", "property": "BaseColor"},  # visible to Python: the plain path
        {"op": "output", "from": "c", "property": "Glitter"}])
    assert r["succeeded"] == 6 and [f["index"] for f in r["failed"]] == [6], r
    assert "unknown material output 'Glitter'" in r["failed"][0]["error"]
    outs = ue._state["assets"][M].outputs
    assert set(outs) == {"CustomData0", "CustomData1", "MP_BASE_COLOR"}
    insp = tools["material_inspect"](M)
    assert {"MP_CUSTOM_DATA0", "MP_CUSTOM_DATA1"} <= set(insp["connected_outputs"]) and insp["substrate"] is False
    assert insp["connected_outputs"]["MP_CUSTOM_DATA0"] == "MaterialExpressionMultiply"


def test_material_edit_hidden_outputs_without_the_library_say_why(ue, tools):  # noqa: F811
    r = tools["material_edit"](M, create=True, operations=[
        *CLEARCOAT_OPS, {"op": "output", "from": "cc", "property": "Sparkle"}])
    errs = {f["index"]: f["error"] for f in r["failed"]}
    assert "hidden from Python" in errs[2] and "MCPScriptLibrary" in errs[2] and "MakeMaterialAttributes" in errs[2]
    assert "hidden from Python" in errs[3]
    assert "unknown material output 'Sparkle'" in errs[4] and "ClearCoat" in errs[4]


# ------------------------------------------------------------------------------------------- force-deleted paths

def _refuse_stale(u):
    """Interchange refuses a path still held by a force-deleted object (StaticFindObject finds it)."""
    tools = u.AssetToolsHelpers.get_asset_tools()
    real = tools.import_asset_tasks

    def import_asset_tasks(tasks):
        stale = u._state.get("stale", set())

        def target(t):
            return f"{t.destination_path}/{t.destination_name or t.filename.rsplit('/', 1)[-1].rsplit('.', 1)[0]}"
        blocked = [t for t in tasks if target(t) in stale]
        for t in blocked:
            t.imported_object_paths = []
        real([t for t in tasks if t not in blocked])
    tools.import_asset_tasks = import_asset_tasks


def test_asset_import_releases_a_force_deleted_path_first(ue, tools, tmp_path):  # noqa: F811
    _with_lib(ue)
    _refuse_stale(ue)
    f = tmp_path / "T_Crack.png"
    f.write_bytes(b"png")
    ue._state["stale"] = {"/Game/Kit/T_Crack"}
    r = tools["asset_import"](files=[str(f)], destination="/Game/Kit")
    assert r["success"], r
    assert r["imports"][0]["released_stale"] == "released:1" and r["imports"][0]["imported"]


def test_asset_import_without_the_library_still_reports_the_refusal(ue, tools, tmp_path):  # noqa: F811
    _refuse_stale(ue)
    f = tmp_path / "T_Crack.png"
    f.write_bytes(b"png")
    ue._state["stale"] = {"/Game/Kit/T_Crack"}
    r = tools["asset_import"](files=[str(f)], destination="/Game/Kit")
    assert r["success"] is False and "released_stale" not in r["imports"][0]


def test_asset_import_sanitizes_the_predicted_name(ue, tools, tmp_path):  # noqa: F811
    _with_lib(ue)
    seen = []
    ue.MCPScriptLibrary.release_stale_asset_path = staticmethod(lambda p: seen.append(p) or "absent")
    f = tmp_path / "road crack (1).png"
    f.write_bytes(b"png")
    tools["asset_import"](files=[str(f)], destination="/Game/Kit")
    assert seen == ["/Game/Kit/road_crack__1_"]


def test_asset_manage_delete_releases_the_path(ue, tools):  # noqa: F811
    _with_lib(ue)
    tools["material_edit"](M, create=True)
    ue._state["stale"] = {M}  # the deleted object lingers in memory
    r = tools["asset_manage"]([{"op": "delete", "path": M}])
    assert r["results"][0]["released_stale"] == "released:1" and not ue._state["stale"]


def test_material_edit_create_releases_a_stale_path(ue, tools):  # noqa: F811
    _with_lib(ue)
    ue._state["stale"] = {M}
    assert tools["material_edit"](M, create=True)["success"]
    assert not ue._state["stale"]


# ------------------------------------------------------------------------------------------- watchdog

def test_unresponsive_error_keeps_hint_and_health(fake, conn):
    fake.on("save_all", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    fake._reply_for_orig = fake._reply_for

    def reply_for(req):
        if req.get("type") == "material_edit_x":
            fake.requests.append(req)
            return {"status": "error", "error": "editor_unresponsive: material_edit_x was not run: stuck",
                    "editor_unresponsive": True, "hint": "kill it with scripts/ue_linux.sh kill",
                    "health": {"responsive": False, "pid": 42}}
        return fake._reply_for_orig(req)
    fake._reply_for = reply_for
    r = call(lambda: conn, "material_edit_x")
    assert r["success"] is False and r["editor_unresponsive"] is True
    assert "ue_linux.sh kill" in r["hint"] and r["health"]["pid"] == 42


@pytest.mark.parametrize("health, unresponsive", [
    ({"responsive": False, "pid": 4242, "reason": "the game thread has not ticked for 75s"}, True),
    ({"responsive": True, "pid": 4242}, False),
    (None, False),  # a plugin without editor_health: the probe gets "Unknown command" through the queue
])
def test_timeout_probes_editor_health(fake, conn, health, unresponsive):
    fake.on("slow", lambda p: {"ok": True})
    fake.delay["slow"] = 1.0
    if health is not None:
        fake.on("editor_health", lambda p: health)
    t0 = time.monotonic()
    r = conn.send_command("slow", timeout=0.2)
    assert time.monotonic() - t0 < 1.0
    assert r["status"] == "error" and "timed out" in r["error"]
    assert bool(r.get("editor_unresponsive")) is unresponsive
    if unresponsive:
        assert r["error"].startswith("editor_unresponsive:") and "not ticked for 75s" in r["error"]
        assert f"UNREAL_MCP_PORT={fake.port} scripts/ue_linux.sh kill" in r["hint"] and "kill -9 4242" in r["hint"]
    time.sleep(1.0)  # let the slow reply drain; the connection recovers
    assert conn.send_command("ping")["status"] == "success"


def test_editor_health_tool(fake, tools):
    fake.on("editor_health", lambda p: {"responsive": True, "pid": 7, "game_thread_tick_age_s": 0.1})
    r = tools["editor_health"]()
    assert r["success"] and r["pid"] == 7 and r["responsive"]
