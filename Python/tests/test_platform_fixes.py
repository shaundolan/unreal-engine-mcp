"""Fixes found by building a whole city on the platform: saving, material_edit/material_instance, asset import,
instanced meshes, collision, world switching, shader waits. Editor scripts run against the fake `unreal` module."""

import subprocess
import sys
import textwrap

from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp.tools import assets as assets_mod
from unreal_mcp.tools.python_exec import build_script

M = "/Game/M/M_Test"


def _mat(u, path=M):
    return u._state["assets"][path]


# ------------------------------------------------------------------------------------------- material_edit

def test_material_edit_clear_removes_every_expression(ue, tools):  # noqa: F811
    tools["material_edit"](M, create=True, operations=[{"op": "add", "class": "Multiply"} for _ in range(8)])
    assert len(_mat(ue).expressions) == 8
    # UE's delete_all_material_expressions alone leaves every other node; rebuilding must not stack duplicates
    ue.MaterialEditingLibrary.delete_all_material_expressions(_mat(ue))
    assert len(_mat(ue).expressions) == 4
    r = tools["material_edit"](M, clear=True, operations=[
        {"op": "add", "class": "ScalarParameter", "properties": {"parameter_name": "Rough"}}])
    assert r["success"] and r["expression_count"] == 1 and "clear_left" not in r


def test_material_edit_pins_outputs_custom_node_and_texture_defaults(ue, tools):  # noqa: F811
    r = tools["material_edit"](M, create=True, operations=[
        {"op": "add", "id": "n", "class": "Noise"},
        {"op": "add", "id": "p", "class": "WorldPosition"},
        {"op": "connect", "from": "p", "to": "n", "to_input": "Position"},  # the pin is "World Position"
        {"op": "add", "id": "c", "class": "Custom", "properties": {
            "code": "Win = 1; return UV0.xyy;", "output_type": "CMOT_FLOAT3",
            "inputs": ["UV0", {"input_name": "Night"}],
            "additional_outputs": [{"output_name": "Win", "output_type": "float1"}]}},
        {"op": "add", "id": "uv", "class": "TextureCoordinate"},
        {"op": "connect", "from": "uv", "to": "c", "to_input": "UV0"},
        {"op": "connect", "from": "n", "to": "c", "to_input": "night"},
        {"op": "add", "id": "m", "class": "Multiply"},
        {"op": "connect", "from": "c", "from_output": "Win", "to": "m", "to_input": "A"},
        {"op": "add", "id": "nm", "class": "TextureSampleParameter2D",
         "properties": {"parameter_name": "NormalTex", "sampler_type": "SAMPLERTYPE_NORMAL"}},
        {"op": "add", "id": "lin", "class": "TextureSampleParameter2D",
         "properties": {"parameter_name": "RoughTex", "sampler_type": "SAMPLERTYPE_LINEAR_COLOR"}},
        {"op": "add", "id": "col", "class": "TextureSampleParameter2D", "properties": {"parameter_name": "Base"}},
        {"op": "output", "from": "nm", "from_output": "RGB", "property": "Normal"},
        {"op": "output", "from": "m", "property": "SubsurfaceColor"},
        {"op": "output", "from": "m", "property": "Refraction"},
    ])
    assert r["success"], r
    custom = next(e for e in _mat(ue).expressions if isinstance(e, ue.MaterialExpressionCustom))
    assert [str(i.input_name) for i in custom.inputs] == ["UV0", "Night"]
    assert [str(o.output_name) for o in custom.additional_outputs] == ["Win"]
    assert str(custom.additional_outputs[0].output_type).endswith("CMOT_FLOAT1")
    assert set(_mat(ue).outputs) == {"MP_NORMAL", "MP_SUBSURFACE_COLOR", "MP_REFRACTION"}
    assert r["default_textures"] == {"9": "/Engine/EngineMaterials/DefaultNormal.DefaultNormal",
                                     "10": "/Engine/EngineMaterials/BaseFlattenLinearColor.BaseFlattenLinearColor"}
    nm = next(e for e in _mat(ue).expressions if getattr(e, "parameter_name", "") == "NormalTex")
    assert "NORMAL" in str(nm.sampler_type)  # the asked sampler type survives the texture assignment


def test_material_edit_bad_pin_and_output_errors_list_choices(ue, tools):  # noqa: F811
    r = tools["material_edit"](M, create=True, operations=[
        {"op": "add", "id": "n", "class": "Noise"}, {"op": "add", "id": "m", "class": "Multiply"},
        {"op": "connect", "from": "n", "to": "m", "to_input": "C"},
        {"op": "output", "from": "m", "property": "Sparkle"}])
    assert r["success"] is False
    errs = {f["index"]: f["error"] for f in r["failed"]}
    assert "no input pin 'C'" in errs[2] and "['A', 'B']" in errs[2]
    assert "unknown material output 'Sparkle'" in errs[3] and "SubsurfaceColor" in errs[3]


def _parent_with(tools, *params):
    return tools["material_edit"](M, create=True, clear=True, operations=[
        {"op": "add", "class": "ScalarParameter", "properties": {"parameter_name": p}} for p in params])


# ------------------------------------------------------------------------------------------- material_instance

def test_material_instance_validates_names_and_reads_back(ue, tools):  # noqa: F811
    _parent_with(tools, "Rough")
    ok = tools["material_instance"]("/Game/M/MI_A", parent=M, parameters={"Rough": 0.25})
    # the UE 5.8 setter returned False here; success comes from the read-back
    assert ok["success"] is True and ok["set"] == ["Rough"] and ok["errors"] == {}
    bad = tools["material_instance"]("/Game/M/MI_A", parameters={"Rough": 0.5, "Shine": 1.0, "Tint": [1, 0, 0, 1]})
    assert bad["success"] is False and bad["set"] == ["Rough"]
    assert "not found on parent" in bad["errors"]["Shine"] and "scalar parameters: ['Rough']" in bad["errors"]["Shine"]
    assert "Tint" in bad["error"]


def test_material_instance_prunes_overrides_the_parent_lost(ue, tools):  # noqa: F811
    _parent_with(tools, "Rough", "Old")
    tools["material_instance"]("/Game/M/MI_A", parent=M, parameters={"Rough": 0.2, "Old": 1.0})
    _parent_with(tools, "Rough")  # rebuilt parent without "Old"
    r = tools["material_instance"]("/Game/M/MI_A", parameters={"Rough": 0.3})
    assert r["success"] and r["pruned"] == ["Old"] and "Old" not in ue._state["assets"]["/Game/M/MI_A"].params


def test_material_instance_recovers_a_deleted_and_recreated_parent(ue, tools):  # noqa: F811
    _parent_with(tools, "Rough")
    tools["material_instance"]("/Game/M/MI_A", parent=M, parameters={"Rough": 0.2})
    mi = ue._state["assets"]["/Game/M/MI_A"]
    ue._state["deps"]["/Game/M/MI_A"] = [M]  # what the asset registry recorded on disk
    # force delete nulls the parent; the material is recreated at the same path
    ue.EditorAssetLibrary.delete_asset(M)
    mi.parent = None
    gone = tools["material_instance"]("/Game/M/MI_A", parameters={"Rough": 0.4})
    assert gone["success"] is False and "no parent" in gone["errors"]["parent"]
    new = ue.register_asset(ue.Material("M_Test", M + ".M_Test"))  # recreated by something other than material_edit
    p = ue.MaterialExpressionScalarParameter()
    p.parameter_name = "Rough"
    new.expressions.append(p)
    r = tools["material_instance"]("/Game/M/MI_A", parameters={"Rough": 0.4})
    assert r["success"] and r["reparented"] and mi.parent is new and mi.params["Rough"] == 0.4


def test_material_instance_replaces_a_dead_parent_object(ue, tools):  # noqa: F811
    _parent_with(tools, "Rough")
    tools["material_instance"]("/Game/M/MI_A", parent=M)
    mi = ue._state["assets"]["/Game/M/MI_A"]
    dead = mi.parent
    dead._dead = True  # deleted, not yet garbage collected, while a new material took its path
    _parent_with(tools, "Rough")
    del ue._state["assets"][M]
    new = ue.Material("M_Test", M + ".M_Test")
    new.expressions = list(dead.expressions)
    ue.register_asset(new)
    r = tools["material_instance"]("/Game/M/MI_A", parameters={"Rough": 0.7})
    assert r["success"] and r["reparented"] and mi.parent is new
    assert "previous parent was deleted" in r["notes"][0]


def test_material_edit_reparents_stranded_instances(ue, tools):  # noqa: F811
    _parent_with(tools, "Rough")
    tools["material_instance"]("/Game/M/MI_A", parent=M)
    tools["material_instance"]("/Game/M/MI_B", parent=M)
    a, b = ue._state["assets"]["/Game/M/MI_A"], ue._state["assets"]["/Game/M/MI_B"]
    ue._state["deps"].update({"/Game/M/MI_A": [M], "/Game/M/MI_B": [M]})
    ue.EditorAssetLibrary.delete_asset(M)
    a.parent = None
    b.parent._dead = True
    r = _parent_with(tools, "Rough")
    assert r["success"] and sorted(r["reparented_instances"]) == ["/Game/M/MI_A.MI_A", "/Game/M/MI_B.MI_B"]
    assert a.parent is _mat(ue) and b.parent is _mat(ue)


# ------------------------------------------------------------------------------------------- asset_import

def _python_calls(fake):
    return [r["params"]["code"] for r in fake.requests if r.get("type") == "execute_python"]


def test_asset_import_passes_local_paths_when_editor_is_local(ue, tools, fake, tmp_path):  # noqa: F811
    f = tmp_path / "crate.fbx"
    f.write_bytes(b"x" * 5000)
    r = tools["asset_import"](local_files=[str(f)], destination="/Game/Props")
    assert r["success"] and r["transfer"] == "path" and r["imports"][0]["file"] == str(f.resolve())
    codes = _python_calls(fake)
    assert len(codes) == 1 and "data_b64" not in codes[0] and "eHh4eHh4" not in codes[0]  # no base64 payload


def test_asset_import_chunks_uploads_to_a_remote_editor(ue, tools, fake, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(assets_mod, "CHUNK_BYTES", 1000)
    monkeypatch.setattr(assets_mod, "editor_is_local", lambda get_conn: False)
    f = tmp_path / "barrel.glb"
    payload = bytes(range(256)) * 10  # 2560 bytes -> 3 chunks
    f.write_bytes(payload)
    r = tools["asset_import"](local_files=[str(f)], destination="/Game/Props")
    assert r["success"] and r["transfer"] == "upload", r
    uploads = [c for c in _python_calls(fake) if "Append one base64 chunk" in c]
    assert len(uploads) == 3
    editor_file = r["imports"][0]["file"]
    assert editor_file.endswith("barrel.glb") and "unreal_mcp_upload_" in editor_file
    assert ue._state["imported"][0][0] == editor_file
    import os
    assert not os.path.exists(editor_file)  # temp copy removed after the import


def test_asset_import_replace_collects_garbage_and_existing_needs_replace(ue, tools, fake, tmp_path):  # noqa: F811
    f = tmp_path / "SM_Rock.fbx"
    f.write_bytes(b"fbx")
    assert tools["asset_import"](files=[str(f)], destination="/Game/P")["success"]
    again = tools["asset_import"](files=[str(f)], destination="/Game/P")
    assert again["success"] is False and "replace_existing" in again["imports"][0]["error"]
    before = ue.SystemLibrary.gc_runs
    r = tools["asset_import"](files=[str(f)], destination="/Game/P", replace_existing=True)
    assert r["success"] and r["imports"][0]["replaced"] is True
    assert ue.SystemLibrary.gc_runs - before == 2  # before and after, each in a call of its own
    codes = _python_calls(fake)[-3:]
    assert "collect_garbage" in codes[0] and "AssetImportTask" in codes[1] and "collect_garbage" in codes[2]
    assert "if True:\n    import gc as _mcp_gc" in codes[1]  # the import call itself runs CPython GC too


def test_editor_is_local():
    class C:
        def __init__(self, host):
            self.settings = type("S", (), {"host": host})()
    assert assets_mod.editor_is_local(lambda: C("127.0.0.1")) and assets_mod.editor_is_local(lambda: C("localhost"))
    assert not assets_mod.editor_is_local(lambda: C("192.0.2.10"))


def test_wrapper_drops_script_globals_in_cycles():
    """A function defined by the script keeps its globals alive in a cycle; the wrapper clears them, so editor
    objects can't outlive the call (stale wrappers crashed CPython's GC after re-imports)."""
    user = textwrap.dedent("""
        import builtins, weakref
        class Thing: pass
        t = Thing()
        builtins._probe = weakref.ref(t)
        def f():
            return t
        result = 1
    """)
    code = build_script(user, "<<M>>") + "\nimport builtins\nprint('ALIVE', builtins._probe() is not None)\n"
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30).stdout
    assert "ALIVE False" in out, out


# ------------------------------------------------------------------------------------------- instanced meshes

def test_instanced_mesh_place_create_append_clear(ue, tools):  # noqa: F811
    mesh = ue.register_asset(ue.StaticMesh("SM_Tree", "/Game/P/SM_Tree.SM_Tree"))
    r = tools["instanced_mesh_place"]("Trees", mesh="/Game/P/SM_Tree", folder="City/Props", cull_distance=30000,
                                      transforms=[[0, 0, 0], [100, 0, 0, 45], [200, 0, 0, 90, 2],
                                                  [300, 0, 0, 0, [1, 2, 3]],
                                                  {"location": [400, 0, 0], "rotation": [0, 30, 0], "scale": 1.5}])
    assert r["success"] and r["created_actor"] and r["created_component"] and r["instances"] == 5
    assert r["class"] == "HierarchicalInstancedStaticMeshComponent"
    actor = next(a for a in ue._state["actors"] if a.get_actor_label() == "Trees")
    comp = actor._components[0]
    assert comp.static_mesh is mesh and comp.instance_end_cull_distance == 30000 and comp.world_space is True
    assert str(actor.get_folder_path()) == "City/Props"
    assert comp.instances[1].rotation.yaw == 45 and comp.instances[3].scale3d.to_tuple() == (1, 2, 3)
    r2 = tools["instanced_mesh_place"]("Trees", mesh="/Game/P/SM_Tree", transforms=[[0, 500, 0]])
    assert r2["instances"] == 6 and not r2["created_component"]
    r3 = tools["instanced_mesh_place"]("Trees", clear=True, transforms=[[1, 1, 1]], cast_shadow=False,
                                       collision=False, min_lod=1)
    assert r3["cleared"] == 6 and r3["instances"] == 1 and comp.cast_shadow is False and comp.min_lod == 1
    assert "NO_COLLISION" in str(comp.collision)
    ism = tools["instanced_mesh_place"]("Rocks", mesh="/Game/P/SM_Tree", hism=False, transforms=[[0, 0, 0]])
    assert ism["class"] == "InstancedStaticMeshComponent"
    bad = tools["instanced_mesh_place"]("Empty", transforms=[[0, 0, 0]])
    assert bad["success"] is False and "mesh is required" in bad["traceback"]
    assert tools["instanced_mesh_place"]("T2", mesh="/Game/Nope")["success"] is False


# ------------------------------------------------------------------------------------------- collision

def test_mesh_collision_shapes_trace_flags_profiles(ue, tools):  # noqa: F811
    a = ue.register_asset(ue.StaticMesh("SM_A", "/Game/P/SM_A.SM_A"))
    b = ue.register_asset(ue.StaticMesh("SM_B", "/Game/P/SM_B.SM_B"))
    r = tools["mesh_collision"](meshes=["/Game/P/SM_A"], collision="box", profile="BlockAll")
    assert r["success"] and r["meshes"][0]["simple_shapes"] == 1 and r["meshes"][0]["profile"] == "BlockAll"
    tools["mesh_collision"](meshes=["/Game/P/SM_A"], collision="sphere", replace=False)
    assert len(a.body_setup.shapes) == 2
    r = tools["mesh_collision"](folder="/Game/P", collision="complex_as_simple")
    assert r["success"] and len(r["meshes"]) == 2
    assert all(m["trace"] == "CTF_USE_COMPLEX_AS_SIMPLE" for m in r["meshes"])
    r = tools["mesh_collision"](meshes=["/Game/P/SM_B"], collision="convex", convex={"hull_count": 6})
    assert r["meshes"][0]["convex_hulls"] == 6 and r["meshes"][0]["trace"] == "CTF_USE_DEFAULT"
    assert b.body_setup.collision_trace_flag is ue.CollisionTraceFlag.CTF_USE_DEFAULT
    tools["scene_compose"]([{"op": "spawn", "class": "StaticMeshActor", "label": "Wall"}])
    r = tools["mesh_collision"](actors=["Wall"], profile="Pawn")
    assert r["success"] and r["actors"] == [{"actor": "Wall", "components": 1}]
    bad = tools["mesh_collision"](meshes=["/Game/P/SM_A", "/Game/Nope"], collision="wobbly")
    assert bad["success"] is False and "unknown collision 'wobbly'" in bad["errors"]["/Game/P/SM_A"]
    assert "not found" in bad["errors"]["/Game/Nope"]
    assert tools["mesh_collision"]()["success"] is False


# ------------------------------------------------------------------------------------------- editor commands

def test_save_all_failure_is_failure(fake, tools):
    fake.on("editor_action", lambda p: {"action": p["action"], "saved": False, "all_saved": False,
                                        "saved_packages": ["/Game/A"],
                                        "failed": [{"package": "/Game/Maps/L", "error": "file is read-only: x"}],
                                        "skipped": []})
    r = tools["editor_action"]("save_all")
    assert r["success"] is False and "/Game/Maps/L: file is read-only" in r["error"]
    fake.on("editor_action", lambda p: {"action": p["action"], "saved": True, "saved_packages": [], "failed": []})
    assert tools["editor_action"]("save_all")["success"] is True


def test_level_open_forwards(fake, tools):
    seen = {}
    fake.on("level_open", lambda p: seen.update(p) or {"current_level": p["path"], "created": p["create"],
                                                       "unloaded_stale_copy": False})
    r = tools["level_open"]("/Game/Maps/Menu", create=True, save_current=True)
    assert r["success"] and r["created"] and seen == {"path": "/Game/Maps/Menu", "create": True,
                                                      "save_current": True, "discard_changes": False}


def test_wait_for_shaders_polls_until_idle(fake, tools, monkeypatch):
    import unreal_mcp.tools.editor as ed
    monkeypatch.setattr(ed.time, "sleep", lambda s: None)
    seq = iter([{"shader_jobs": 40, "asset_builds": 2, "shaders_compiling": True},
                {"shader_jobs": 5, "asset_builds": 0, "shaders_compiling": True},
                {"shader_jobs": 0, "asset_builds": 0, "shaders_compiling": False},
                {"shader_jobs": 0, "asset_builds": 1, "shaders_compiling": False},  # not settled yet
                {"shader_jobs": 0, "asset_builds": 0, "shaders_compiling": False},
                {"shader_jobs": 0, "asset_builds": 0, "shaders_compiling": False}])
    fake.on("shader_status", lambda p: next(seq))
    r = tools["wait_for_shaders"](timeout_s=60)
    assert r["success"] and r["idle"] and r["polls"] == 6 and r["peak_jobs"] == 42


def test_wait_for_shaders_times_out_and_needs_plugin(fake, tools, monkeypatch):
    import unreal_mcp.tools.editor as ed
    monkeypatch.setattr(ed.time, "sleep", lambda s: None)
    clock = iter(range(0, 10000, 30))
    monkeypatch.setattr(ed.time, "monotonic", lambda: next(clock))
    fake.on("shader_status", lambda p: {"shader_jobs": 7, "asset_builds": 0, "shaders_compiling": True})
    r = tools["wait_for_shaders"](timeout_s=60)
    assert r["success"] is False and r["timed_out"] and "7 jobs left" in r["error"]
    fake.handlers.pop("shader_status")
    assert tools["wait_for_shaders"]()["success"] is False
