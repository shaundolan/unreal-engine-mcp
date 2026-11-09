"""Animation + GAS tools: editor scripts against the fake `unreal` module, request shapes and result
normalization for the C++ commands (animbp_apply/export, anim_asset_apply/export, gameplay_tags)."""

from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp.tools.animation import montage_ops
from unreal_mcp.tools.gas import add_build_deps, attribute_set_source, class_path

M = "/Game/Characters/Mannequins"
SEQ = f"{M}/Anims/Unarmed"


# ---------------------------------------------------------------------------------------------- anim_edit


def test_blendspace_create_and_inspect(ue, tools):  # noqa: F811
    ue.add_mannequin()
    r = tools["anim_edit"](
        [
            {
                "op": "create_blendspace",
                "asset": "/Game/A/BS_Loco",
                "skeleton": f"{M}/Meshes/SK_Mannequin",
                "axes": [{"name": "Speed", "min": 0, "max": 600, "grid": 4}],
                "samples": [
                    {"animation": f"{SEQ}/MM_Idle", "x": 0},
                    {"animation": f"{SEQ}/Jog/MF_Unarmed_Jog_Fwd", "x": 500},
                ],
            }
        ]
    )
    assert r["success"], r
    res = r["results"][0]
    assert res["class"] == "BlendSpace1D" and res["dimensions"] == 1
    assert res["axes"][0] == {"axis": "x", "name": "Speed", "min": 0.0, "max": 600.0, "grid": 4}
    assert [s["x"] for s in res["samples"]] == [0.0, 500.0]
    i = tools["anim_inspect"](asset="/Game/A/BS_Loco")
    assert i["success"] and i["samples"][1]["animation"].endswith("MF_Unarmed_Jog_Fwd")
    listing = tools["anim_inspect"](folder="/Game/A")
    assert listing["count"] == 1 and listing["assets"][0]["class"] == "BlendSpace1D"


def test_blendspace_mesh_as_skeleton_2d_and_bad_sample(ue, tools):  # noqa: F811
    ue.add_mannequin()
    r = tools["anim_edit"](
        [
            {
                "op": "create_blendspace",
                "asset": "/Game/A/BS_2D",
                "skeleton": f"{M}/Meshes/SKM_Manny_Simple",
                "dimensions": 2,
                "axes": [{"name": "Direction", "min": -180, "max": 180}, {"name": "Speed", "max": 600}],
                "samples": [{"animation": f"{SEQ}/MM_Idle", "x": 0, "y": 0}],
            },
            {
                "op": "create_blendspace",
                "asset": "/Game/A/BS_Bad",
                "skeleton": f"{M}/Meshes/SK_Mannequin",
                "samples": [{"animation": "/Game/Nope/MM_X"}],
            },
        ]
    )
    assert r["success"] is False
    assert (
        r["results"][0]["ok"] and r["results"][0]["dimensions"] == 2 and r["results"][0]["axes"][1]["name"] == "Speed"
    )
    assert "not found" in r["results"][1]["error"] and "op 1" in r["error"]


def test_sequence_settings_curves_and_inspect(ue, tools):  # noqa: F811
    ue.add_mannequin()
    r = tools["anim_edit"](
        [
            {
                "op": "set_sequence",
                "asset": f"{SEQ}/Jog/MF_Unarmed_Jog_Fwd",
                "rate_scale": 1.25,
                "root_motion": True,
                "root_lock": "anim_first_frame",
            },
            {
                "op": "add_curve",
                "asset": f"{SEQ}/Jog/MF_Unarmed_Jog_Fwd",
                "name": "FootPlant",
                "keys": [[0, 0], [0.4, 1]],
            },
            {"op": "set_sequence", "asset": f"{SEQ}/MM_Idle", "root_lock": "sideways"},
        ]
    )
    assert [x["ok"] for x in r["results"]] == [True, True, False]
    assert r["results"][0]["set"] == {"rate_scale": 1.25, "root_motion": True, "root_lock": "anim_first_frame"}
    assert "sideways" in r["results"][2]["error"]
    i = tools["anim_inspect"](asset=f"{SEQ}/Jog/MF_Unarmed_Jog_Fwd")
    assert i["rate_scale"] == 1.25 and i["root_motion"] and i["root_lock"] == "ANIM_FIRST_FRAME"
    assert i["curves"] == ["FootPlant"] and i["skeleton"].endswith("SK_Mannequin")


def test_anim_edit_routes_montage_and_notify_ops_to_cpp(fake, tools):
    seen = []

    def apply(p):
        seen.append(p)
        bad = [i for i, o in enumerate(p["operations"]) if o["op"] == "add_notify" and o.get("time", 0) > 5]
        return {
            "ok": not bad,
            "applied": len(p["operations"]) - len(bad),
            "path": p["asset"],
            "failed": [{"index": i, "op": "add_notify", "error": "time outside"} for i in bad],
        }

    fake.on("anim_asset_apply", apply)
    r = tools["anim_edit"](
        [
            {
                "op": "create_montage",
                "asset": "/Game/A/AM_Attack",
                "sequences": [f"{SEQ}/Attack/MM_Attack_01"],
                "sections": [{"name": "Windup", "time": 0}, {"name": "Strike", "time": 0.4}],
                "notifies": [{"name": "Hit", "time": 0.45}],
                "blend_in": 0.1,
            },
            {"op": "add_notify", "asset": f"{SEQ}/MM_Idle", "name": "Breath", "time": 9},
        ]
    )
    assert r["success"] is False and r["results"][0]["ok"] and not r["results"][1]["ok"]
    assert "time outside" in r["results"][1]["error"]
    create = seen[0]
    assert create["create"] == "montage" and create["atomic"] is True
    assert [o["op"] for o in create["operations"]] == [
        "add_segment",
        "add_section",
        "add_section",
        "remove_section",
        "add_notify",
        "set_blend",
    ]
    assert seen[1]["operations"] == [{"op": "add_notify", "name": "Breath", "time": 9}]


def test_montage_ops_translation():
    ops = montage_ops(
        {
            "segments": [{"sequence": "/Game/A", "slot": "G.Upper", "play_rate": 2}],
            "sequences": ["/Game/B"],
            "sections": [{"name": "Intro", "time": 0.5}],
        }
    )
    assert ops[0] == {"op": "add_segment", "slot": "G.Upper", "sequence": "/Game/A", "play_rate": 2}
    assert ops[1] == {"op": "add_segment", "slot": "DefaultGroup.DefaultSlot", "sequence": "/Game/B"}
    assert {"op": "remove_section", "name": "Default"} not in ops  # nothing at t=0 replaces "Default"


def test_animbp_apply_forwards_and_reports(fake, tools):
    seen = {}

    def apply(p):
        seen.update(p)
        return {
            "ok": False,
            "failed": [{"index": 2, "op": "add_transition", "error": "state 'walk' not found"}],
            "compile": {"ok": True, "errors": []},
            "rolled_back": True,
            "created": False,
        }

    fake.on("animbp_apply", apply)
    fake.on("animbp_export", lambda p: {"path": p["blueprint"], "state_machines": [], "pins": p["include_pins"]})
    r = tools["animbp_apply"](
        "/Game/A/ABP_Hero", [{"op": "add_state_machine", "id": "loco"}], skeleton=f"{M}/Meshes/SK_Mannequin"
    )
    assert seen["skeleton"].endswith("SK_Mannequin") and seen["atomic"] is True and seen["compile"] is True
    assert r["success"] is False and "op 2 (add_transition): state 'walk' not found" in r["error"]
    e = tools["animbp_export"]("/Game/A/ABP_Hero")
    assert e["success"] and e["pins"] is False


def test_anim_inspect_merges_montage_sections(ue, fake, tools):  # noqa: F811
    ue.add_mannequin()
    m = ue.register_asset(ue.AnimMontage("AM_X", "/Game/A/AM_X.AM_X"))
    m.slot_anim_tracks = []
    m.get_section_name = lambda i: "Default"
    m.get_num_sections = lambda: 1
    fake.on("anim_asset_export", lambda p: {"sections": [{"name": "Default", "time": 0, "next": ""}], "blend_in": 0.25})
    r = tools["anim_inspect"](asset="/Game/A/AM_X")
    assert r["success"] and r["sections"][0]["time"] == 0 and r["blend_in"] == 0.25


# ---------------------------------------------------------------------------------------------- GAS


def _tags_handler(ue):  # noqa: F811
    def gameplay_tags(p):
        known = ue._state["tags"]
        added = [t for t in p.get("tags", []) if t not in known]
        known.update(added)
        return {"ok": True, "tags": sorted(known), "added": added, "already_existed": [], "errors": []}

    return gameplay_tags


def test_heal_effect_create_and_inspect(ue, fake, tools):  # noqa: F811
    fake.on("gameplay_tags", _tags_handler(ue))
    r = tools["gas_edit"](
        [
            {
                "op": "create_effect",
                "asset": "/Game/GAS/GE_Heal",
                "duration": "instant",
                "modifiers": [{"attribute": "AbilitySystemTestAttributeSet.Health", "op": "add", "magnitude": 25}],
                "asset_tags": ["Effect.Heal"],
            }
        ]
    )
    assert r["success"], r
    assert r["tags_registered"] == ["Effect.Heal"]
    res = r["results"][0]
    assert res["created"] and res["duration_policy"] == "INSTANT"
    assert res["modifiers"] == [
        {"attribute": "AbilitySystemTestAttributeSet.Health", "op": "AddBase", "magnitude": 25.0}
    ]
    assert res["asset_tags"] == ["Effect.Heal"]
    i = tools["gas_inspect"]("/Game/GAS/GE_Heal")
    assert i["success"] and i["modifiers"][0]["magnitude"] == 25.0


def test_effect_duration_period_and_bad_input(ue, fake, tools):  # noqa: F811
    fake.on("gameplay_tags", _tags_handler(ue))
    r = tools["gas_edit"](
        [
            {
                "op": "create_effect",
                "asset": "/Game/GAS/GE_Regen",
                "duration": 5,
                "period": 1,
                "modifiers": [{"attribute": "AbilitySystemTestAttributeSet.Health", "op": "add", "magnitude": 2}],
            },
            {"op": "create_effect", "asset": "/Game/GAS/GE_Bad", "modifiers": [{"attribute": "Health"}]},
            {"op": "create_effect", "asset": "/Game/GAS/GE_Bad2", "modifiers": [{"attribute": "NoSuchSet.Health"}]},
            {
                "op": "create_effect",
                "asset": "/Game/GAS/GE_Bad3",
                "modifiers": [{"attribute": "AbilitySystemTestAttributeSet.Health", "op": "pow"}],
            },
        ]
    )
    ok = r["results"][0]
    assert ok["ok"] and ok["duration_policy"] == "HAS_DURATION" and ok["duration"] == 5.0 and ok["period"] == 1.0
    errs = [x.get("error", "") for x in r["results"][1:]]
    assert "must be" in errs[0] and "not found" in errs[1] and "pow" in errs[2]


def test_ability_tags_and_graph(ue, fake, tools):  # noqa: F811
    fake.on("gameplay_tags", _tags_handler(ue))
    applied = {}
    fake.on("bp_apply", lambda p: applied.update(p) or {"ok": True, "applied": len(p["operations"]), "failed": []})
    r = tools["gas_edit"](
        [
            {
                "op": "create_ability",
                "asset": "/Game/GAS/GA_Heal",
                "ability_tags": ["Ability.Heal"],
                "activation_owned_tags": ["State.Healing"],
                "graph": [{"op": "add_node", "id": "act", "kind": "event", "event": "ActivateAbility"}],
            }
        ]
    )
    assert r["success"], r
    res = r["results"][0]
    assert res["ability_tags"] == ["Ability.Heal"] and res["activation_owned_tags"] == ["State.Healing"]
    assert res["graph"]["success"] and applied["blueprint"] == "/Game/GAS/GA_Heal"


def test_tags_must_register(fake, tools):
    fake.on("gameplay_tags", lambda p: {"ok": False, "errors": ["'Bad Tag': spaces not allowed"], "added": []})
    r = tools["gas_edit"]([{"op": "create_effect", "asset": "/Game/GAS/GE_X", "asset_tags": ["Bad Tag"]}])
    assert r["success"] is False and "spaces" in r["error"]
    t = tools["gameplay_tags"]("add", ["Bad Tag"])
    assert t["success"] is False and "spaces" in t["error"]


def test_grant_abilities_inserts_before_existing_beginplay(fake, tools):
    fake.on(
        "bp_export",
        lambda p: {
            "components": [],
            "graphs": [
                {
                    "name": "EventGraph",
                    "nodes": [
                        {
                            "id": "E1",
                            "kind": "event",
                            "event": "ReceiveBeginPlay",
                            "pins": [{"name": "then", "dir": "out", "links": ["P1.execute"]}],
                        }
                    ],
                }
            ],
        },
    )
    seen = {}
    fake.on("bp_apply", lambda p: seen.update(p) or {"ok": True, "failed": []})
    r = tools["gas_edit"](
        [
            {
                "op": "grant_abilities",
                "character": "/Game/BP_Hero",
                "abilities": ["/Game/GAS/GA_Heal"],
                "attribute_sets": ["AbilitySystemTestAttributeSet"],
            }
        ]
    )
    assert r["success"], r
    ops = seen["operations"]
    assert ops[0]["op"] == "add_component" and ops[0]["class"] == "AbilitySystemComponent"
    assert ops[0]["properties"]["DefaultStartingData"][0]["Attributes"].endswith("AbilitySystemTestAttributeSet")
    give = next(o for o in ops if o.get("function") == "GiveAbility")
    assert give["pins"]["AbilityClass"] == "/Game/GAS/GA_Heal.GA_Heal_C"
    assert ops[-1] == {"op": "connect", "from": "mcp_give0.then", "to": "P1.execute"}


def test_attribute_set_source_and_build_deps(fake, tools):
    g = attribute_set_source("HealthSet", "MyGame", ["Health", "MaxHealth"], {"Health": 100})
    assert g["class"] == "UHealthSet" and g["attribute_path"] == "/Script/MyGame.HealthSet"
    assert "class MYGAME_API UHealthSet : public UAttributeSet" in g["header"]
    assert "ATTRIBUTE_ACCESSORS(UHealthSet, Health)" in g["header"] and "InitHealth(100.0f)" in g["source"]
    assert "FMath::Clamp(GetHealth(), 0.f, GetMaxHealth())" in g["source"]
    assert g["header"].count("{") == g["header"].count("}") and g["source"].count("{") == g["source"].count("}")
    cs = 'PublicDependencyModuleNames.AddRange(new string[] { "Core", "GameplayTags" });'
    out = add_build_deps(cs)
    assert out.count('"GameplayTags"') == 1 and '"GameplayAbilities"' in out and add_build_deps(out) == out
    assert class_path("/Game/GAS/GA_Heal") == "/Game/GAS/GA_Heal.GA_Heal_C"
    r = tools["gas_edit"]([{"op": "attribute_set", "module": "My Game", "name": "X", "attributes": ["A"]}])
    assert r["success"] is False and "identifiers" in r["error"]


def test_attribute_set_write_uses_cpp_source(ue, tools, tmp_path, monkeypatch):  # noqa: F811
    import os

    root = str(tmp_path / "Proj") + "/"
    monkeypatch.setattr(ue.Paths, "project", root)
    os.makedirs(os.path.join(root, "Source", "MyGame", "Public"), exist_ok=True)
    with open(os.path.join(root, "Source", "MyGame", "MyGame.Build.cs"), "w") as f:
        f.write('PublicDependencyModuleNames.AddRange(new string[] { "Core" });')
    with open(os.path.join(root, "Source", "MyGame", "Public", "MyGame.h"), "w") as f:
        f.write("#pragma once")
    r = tools["gas_edit"](
        [
            {
                "op": "attribute_set",
                "module": "MyGame",
                "name": "HealthSet",
                "attributes": ["Health", "MaxHealth"],
                "write": True,
            }
        ]
    )
    assert r["success"], r
    written = r["results"][0]["written"]
    assert written == [
        "Source/MyGame/Public/HealthSet.h",
        "Source/MyGame/Private/HealthSet.cpp",
        "Source/MyGame/MyGame.Build.cs",
    ]
    assert '"GameplayAbilities"' in open(os.path.join(root, "Source", "MyGame", "MyGame.Build.cs")).read()
