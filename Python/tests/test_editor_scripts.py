"""Domain tools end to end: MCP tool -> framed TCP -> fake plugin -> wrapper -> editor script
executing against a fake `unreal` module."""

import contextlib
import io
import sys

import pytest
from fake_unreal_api import make_module

from unreal_mcp.editor_scripts import names, render

_exec_lock = __import__("threading").Lock()


@pytest.fixture
def ue(fake, monkeypatch):
    module = make_module()

    def execute_python(params):
        buf = io.StringIO()
        with _exec_lock:
            monkeypatch.setitem(sys.modules, "unreal", module)
            with contextlib.redirect_stdout(buf):
                exec(compile(params["code"], "<editor>", "exec"), {"__name__": "__editor__"})
        return {"python_ok": True, "command_result": "",
                "log": [{"type": "Info", "output": line} for line in buf.getvalue().splitlines()]}

    fake.on("execute_python", execute_python)
    return module


@pytest.mark.parametrize("name", names())
def test_every_script_compiles(name):
    compile(render(name, {"x": 1}), name, "exec")


def test_scene_compose_spawn_pattern_modify_delete(ue, tools):
    r = tools["scene_compose"]([
        {"op": "spawn", "class": "PointLight", "label": "Lamp", "location": [0, 0, 300],
         "pattern": {"type": "grid", "count": [2, 2], "spacing": [100, 200]},
         "properties": {"light_component.intensity": 1234}, "tags": ["lamp"], "folder": "Lighting"},
        {"op": "spawn", "asset": "/Engine/BasicShapes/Cube.Cube"},  # asset missing in fake
        {"op": "modify", "target": "Lamp_3", "location": {"x": 5, "y": 6, "z": 7}, "scale": 2},
        {"op": "delete", "target": "Lamp_0"},
        {"op": "teleport"},
    ])
    assert r["success"] and r["succeeded"] == 3 and r["failed"] == 2
    assert r["results"][0]["spawned"] == ["Lamp_0", "Lamp_1", "Lamp_2", "Lamp_3"]
    assert "not found" in r["results"][1]["error"]
    assert "unknown op" in r["results"][4]["error"]
    actors = {a.get_actor_label(): a for a in ue._state["actors"]}
    assert set(actors) == {"Lamp_1", "Lamp_2", "Lamp_3"}
    assert actors["Lamp_2"].get_actor_location().to_tuple() == (0, 200, 300)
    assert actors["Lamp_3"].get_actor_location().to_tuple() == (5, 6, 7)
    assert actors["Lamp_3"].get_actor_scale3d().to_tuple() == (2, 2, 2)
    assert actors["Lamp_1"].light_component.intensity == 1234.0
    assert str(actors["Lamp_1"].get_folder_path()) == "Lighting"
    assert ue._state["transactions"] == 1  # whole batch = one undo step


def test_scene_compose_stop_on_error(ue, tools):
    r = tools["scene_compose"]([{"op": "delete", "target": "nope"}, {"op": "spawn", "class": "PointLight"}],
                               stop_on_error=True)
    assert r["failed"] == 1 and len(r["results"]) == 1 and ue._state["actors"] == []


def test_scene_compose_circle_faces_center(ue, tools):
    tools["scene_compose"]([{"op": "spawn", "class": "CameraActor", "label": "Cam",
                             "pattern": {"type": "circle", "count": 4, "radius": 100, "face_center": True}}])
    yaws = sorted(round(a.get_actor_rotation().yaw) for a in ue._state["actors"])
    assert yaws == [180, 270, 360, 450]


def _populate(tools):
    tools["scene_compose"]([
        {"op": "spawn", "class": "PointLight", "label": "Near", "location": [10, 0, 0]},
        {"op": "spawn", "class": "PointLight", "label": "Far", "location": [5000, 0, 0]},
        {"op": "spawn", "class": "StaticMeshActor", "label": "Wall", "location": [100, 0, 0], "tags": ["solid"]},
        {"op": "spawn", "class": "CameraActor", "label": "MainCam", "location": [0, 0, 500]},
    ])


def test_scene_query_filters(ue, tools):
    _populate(tools)
    lights = tools["scene_query"](class_name="Light")  # subclass match
    assert {a["label"] for a in lights["actors"]} == {"Near", "Far"}
    near = tools["scene_query"](near=[0, 0, 0], radius=200, fields=["class", "tags"])
    assert [a["label"] for a in near["actors"]] == ["Near", "Wall"]
    assert near["actors"][1]["tags"] == ["solid"]
    box = tools["scene_query"](box_min=[-1, -1, 400], box_max=[1, 1, 600])
    assert [a["label"] for a in box["actors"]] == ["MainCam"]
    assert tools["scene_query"](tag="solid")["total_matches"] == 1
    limited = tools["scene_query"](limit=2)
    assert limited["truncated"] and limited["total_matches"] == 4 and len(limited["actors"]) == 2


def test_scene_brief(ue, tools):
    _populate(tools)
    b = tools["scene_brief"]()
    assert b["actor_count"] == 4 and b["classes"]["PointLight"] == 2
    assert set(b["lights"]) == {"Near", "Far"} and b["cameras"] == ["MainCam"]
    assert b["bounds"]["max"][0] == 5000


def test_actor_inspect(ue, tools):
    _populate(tools)
    r = tools["actor_inspect"]("near", properties=["light_component.intensity", "nonexistent"])
    assert r["class"] == "PointLight" and r["components"][0]["class"] == "LightComponent"
    assert r["properties"]["light_component.intensity"] == 5000.0
    assert r["properties"]["nonexistent"].startswith("<error")
    missing = tools["actor_inspect"]("ghost")
    assert missing["success"] is False and "LookupError" in missing["traceback"]


def test_assets_search_refs_manage(ue, tools):
    for p in ("/Game/Props/SM_Rock", "/Game/Props/SM_Tree", "/Game/Mats/M_Rock"):
        cls = ue.Material if "/M_" in p else ue.StaticMesh
        ue.register_asset(cls(p.rsplit("/", 1)[1], f"{p}.{p.rsplit('/', 1)[1]}"))
    ue._state["deps"] = {"/Game/Props/SM_Rock": ["/Game/Mats/M_Rock"], "/Game/Mats/M_Rock": ["/Game/T_Noise"]}

    r = tools["search_assets"](query="rock")
    assert {a["path"] for a in r["assets"]} == {"/Game/Props/SM_Rock.SM_Rock", "/Game/Mats/M_Rock.M_Rock"}
    assert tools["search_assets"](class_name="StaticMesh", path="/Game/Props")["total_matches"] == 2

    deps = tools["asset_references"]("/Game/Props/SM_Rock.SM_Rock", depth=2)
    assert {(p["package"], p["depth"]) for p in deps["packages"]} == {("/Game/Mats/M_Rock", 1), ("/Game/T_Noise", 2)}
    refs = tools["asset_references"]("/Game/Mats/M_Rock", direction="referencers")
    assert [p["package"] for p in refs["packages"]] == ["/Game/Props/SM_Rock"]
    assert tools["asset_references"]("/Game/X", direction="sideways")["success"] is False

    m = tools["asset_manage"]([
        {"op": "duplicate", "source": "/Game/Props/SM_Rock", "destination": "/Game/Props/SM_Rock2"},
        {"op": "rename", "source": "/Game/Props/SM_Tree", "destination": "/Game/Props/SM_Oak"},
        {"op": "delete", "path": "/Game/Nope"},
        {"op": "exists", "path": "/Game/Props/SM_Oak"},
    ])
    assert (m["succeeded"], m["failed"]) == (3, 1)
    assert m["results"][3]["exists"] is True
    assert "/Game/Props/SM_Rock2" in ue._state["assets"]


def test_asset_import_editor_and_uploaded_files(ue, tools, tmp_path):
    on_editor = tmp_path / "crate.fbx"
    on_editor.write_bytes(b"fbx")
    local = tmp_path / "barrel.glb"
    local.write_bytes(b"glb" * 1000)
    r = tools["asset_import"](files=[str(on_editor)], local_files=[str(local)], destination="/Game/Props")
    assert r["success"] and r["failed"] == []
    imported = sorted(p for i in r["imports"] for p in i["imported"])
    assert imported == ["/Game/Props/barrel.barrel", "/Game/Props/crate.crate"]
    assert tools["asset_import"]()["success"] is False
    assert tools["asset_import"](local_files=[str(tmp_path / "missing.fbx")])["success"] is False
    bad = tools["asset_import"](files=[str(tmp_path / "not_there.fbx")])
    assert bad["success"] is False and "does not exist" in bad["traceback"]


def test_material_edit_inspect_instance(ue, tools):
    r = tools["material_edit"]("/Game/Materials/M_Glow", create=True,
                               settings={"blend_mode": "BLEND_TRANSLUCENT", "two_sided": True},
                               operations=[
        {"op": "add", "id": "tint", "class": "VectorParameter",
         "properties": {"parameter_name": "Tint", "default_value": [1, 0.5, 0, 1]}},
        {"op": "add", "id": "power", "class": "ScalarParameter", "properties": {"parameter_name": "Power"}},
        {"op": "add", "id": "mul", "class": "MaterialExpressionMultiply"},
        {"op": "connect", "from": "tint", "to": "mul", "to_input": "A"},
        {"op": "connect", "from": "power", "to": "mul", "to_input": "B"},
        {"op": "connect", "from": "power", "to": "mul", "to_input": "Bogus"},
        {"op": "connect", "from": "ghost", "to": "mul", "to_input": "A"},
        {"op": "output", "from": "mul", "property": "EmissiveColor"},
        {"op": "output", "from": "tint", "property": "base color"},
        {"op": "add", "class": "NotARealNode"},
    ])
    assert r["success"] and r["expression_count"] == 3 and r["succeeded"] == 7
    errors = {f["index"]: f["error"] for f in r["failed"]}
    assert "pin names" in errors[5] and "unknown expression id" in errors[6] and "LookupError" in errors[9]
    mat = ue._state["assets"]["/Game/Materials/M_Glow"]
    assert mat.blend_mode is ue.BlendMode.BLEND_TRANSLUCENT and mat.two_sided is True
    assert mat.expressions[0].default_value.to_tuple() == (1, 0.5, 0, 1)

    info = tools["material_inspect"]("/Game/Materials/M_Glow")
    assert info["parameters"] == {"Tint": {"type": "vector"}, "Power": {"type": "scalar"}}
    assert info["connected_outputs"] == {"MP_BASE_COLOR": "MaterialExpressionVectorParameter",
                                         "MP_EMISSIVE_COLOR": "MaterialExpressionMultiply"}

    mi = tools["material_instance"]("/Game/Materials/MI_Glow_Red", parent="/Game/Materials/M_Glow",
                                    parameters={"Power": 8, "Tint": [1, 0, 0, 1], "Missing": 1.0})
    assert mi["created"] and sorted(mi["set"]) == ["Power", "Tint"] and "Missing" in mi["errors"]
    info = tools["material_inspect"]("/Game/Materials/MI_Glow_Red")
    assert info["parent"] == "/Game/Materials/M_Glow.M_Glow"
    assert info["parameters"]["Power"]["value"] == 8.0

    assert tools["material_edit"]("/Game/Materials/M_None")["success"] is False
    assert tools["material_instance"]("/Game/Materials/MI_None")["success"] is False


def test_level_inspect(ue, tools):
    _populate(tools)
    r = tools["level_inspect"]()
    assert r["world"] == "/Temp/Untitled.Untitled" and r["actor_count"] == 4
    assert r["world_settings"]["kill_z"] == -100000.0 and r["world_partition"] is False
