"""Niagara, sequencer and DataTable tools end to end against the fake `unreal`."""

from test_editor_scripts import ue  # noqa: F401  (fixture)


def test_niagara_spawn_and_set(ue, tools):  # noqa: F811
    ue.register_asset(ue.NiagaraSystem("NS_Fire", "/Game/FX/NS_Fire.NS_Fire"))
    r = tools["niagara_edit"]([
        {"op": "spawn", "system": "/Game/FX/NS_Fire", "label": "Campfire", "location": [1, 2, 3],
         "auto_activate": False,
         "parameters": {"SpawnRate": 80, "User.Tint": [1, 0.4, 0.1, 1], "Wind": [50, 0, 0], "int:Count": 3,
                        "Enabled": True}},
        {"op": "set", "target": "Campfire", "parameters": {"SpawnRate": 10}},
        {"op": "spawn", "system": "/Game/FX/NS_Missing"},
        {"op": "set", "target": "Campfire", "parameters": {"Bad": None}},
    ])
    assert (r["succeeded"], r["failed"]) == (2, 2)
    comp = next(a for a in ue._state["actors"] if a.get_actor_label() == "Campfire").niagara_component
    assert comp.params["User.SpawnRate"] == ("float", 10.0)
    assert comp.params["User.Count"] == ("int", 3) and comp.params["User.Enabled"] == ("bool", True)
    assert comp.params["User.Tint"][0] == "linear_color" and comp.params["User.Wind"][0] == "vec3"
    assert comp.auto_activate is False
    assert "NiagaraSystem" in r["results"][2]["error"] and "TypeError" in r["results"][3]["error"]


def test_sequence_create_camera_keys_cut(ue, tools):  # noqa: F811
    hero = ue.get_editor_subsystem(ue.EditorActorSubsystem).spawn_actor_from_class(
        ue.Character, ue.Vector(), ue.Rotator())
    hero.set_actor_label("Hero")
    r = tools["sequence_edit"]("/Game/Cinematics/LS_Intro", create=True, duration_s=5, open=True, operations=[
        {"op": "spawn_camera", "id": "cam", "label": "IntroCam", "location": [-800, 0, 300], "focal_length": 50},
        {"op": "bind", "id": "hero", "actor": "Hero"},
        {"op": "transform_keys", "binding": "cam", "keys": [
            {"t": 0, "location": [-800, 0, 300]}, {"t": 2.5, "location": [-200, 400, 200], "rotation": [-10, -60, 0]}]},
        {"op": "camera_cut", "binding": "cam", "start_s": 0, "end_s": 5},
        {"op": "camera_cut", "binding": "ghost"},
        {"op": "bind", "id": "x", "actor": "Nobody"},
    ])
    assert r["created"] and r["succeeded"] == 4 and len(r["failed"]) == 2
    assert "unknown binding id" in r["failed"][0]["error"]
    seq = ue._state["assets"]["/Game/Cinematics/LS_Intro"]
    assert seq.rate.numerator == 30 and seq.end == 5.0
    cam_binding = seq.bindings[0]
    assert cam_binding.actor.get_cine_camera_component().current_focal_length == 50.0
    chans = cam_binding.tracks[0].sections[0].channels
    assert chans[0].keys == [(0, -800.0), (75, -200.0)]          # loc x at frames 0 and 75 (30 fps)
    assert chans[5].keys == [(75, -60.0)]                         # yaw
    assert chans[6].keys == []                                    # no scale keys
    assert seq.tracks[0].sections[0].camera == ("binding", 0)     # fallback binding-id path used
    assert ue.LevelSequenceEditorBlueprintLibrary.opened == [seq]


def test_data_table_write_read(ue, tools):  # noqa: F811
    ue.register_asset(ue.ScriptStruct("S_Item", "/Game/Data/S_Item.S_Item", ["Damage", "Label"]))
    assert tools["data_table"]("/Game/Data/DT_Items", action="write", rows={"A": {"Damage": 1}})["success"] is False
    w = tools["data_table"]("/Game/Data/DT_Items", action="write", struct="/Game/Data/S_Item.S_Item",
                            rows={"Sword": {"Damage": 10, "Label": "Sword"}, "Axe": {"Damage": 14, "Label": "Axe"}})
    assert w["created"] and w["row_count"] == 2
    r = tools["data_table"]("/Game/Data/DT_Items")
    assert r["rows"]["Axe"] == {"Damage": "14", "Label": "Axe"} and r["columns"] == ["Damage", "Label"]
    bad = tools["data_table"]("/Game/Data/DT_Items", action="write", rows={"X": {"Nope": 1}})
    assert bad["success"] is False and "row struct" in bad["traceback"]
    assert tools["data_table"]("/Game/Data/DT_None")["success"] is False
