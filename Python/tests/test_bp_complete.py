"""Blueprint completeness, data types and audio: wrappers against the fake plugin, bp_explain / bp_review
analysis on export specs with planted issues, and the data_types routing (plugin ops vs editor scripts).
The C++ side (interfaces, dispatchers, timelines, struct/enum authoring, MetaSound builder) is verified
live; see docs/WINDOWS_TEST.md."""

from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp import bp_analysis


def _pin(name, d, t="exec", links=(), default=None):
    p = {"name": name, "dir": d, "type": t}
    if links:
        p["links"] = list(links)
    if default is not None:
        p["default"] = default
    return p


def _node(nid, kind, pins, **kw):
    return {"id": nid, "kind": kind, "title": kw.pop("title", kind), "pins": pins, **kw}


def _spec():
    """A Blueprint with one clean event and a Tick full of planted problems."""
    nodes = [
        _node("begin", "event", [_pin("then", "out", links=["print.execute"])], event="ReceiveBeginPlay"),
        _node(
            "print",
            "call_function",
            [
                _pin("execute", "in", links=["begin.then"]),
                _pin("then", "out"),
                _pin("InString", "in", "string", default="hi"),
            ],
            function="PrintString",
            **{"class": "KismetSystemLibrary"},
        ),
        _node("tick", "event", [_pin("then", "out", links=["cast.execute"])], event="ReceiveTick"),
        _node(
            "pawn",
            "call_function",
            [_pin("ReturnValue", "out", "object:Pawn", links=["cast.Object"])],
            function="GetPlayerPawn",
            pure=True,
            **{"class": "GameplayStatics"},
        ),
        _node(
            "cast",
            "cast",
            [
                _pin("execute", "in", links=["tick.then"]),
                _pin("then", "out", links=["all.execute"]),
                _pin("CastFailed", "out"),
                _pin("Object", "in", "object:Object", links=["pawn.ReturnValue"]),
            ],
            **{"class": "/Game/BP_Hero.BP_Hero_C"},
        ),
        _node(
            "all",
            "call_function",
            [_pin("execute", "in", links=["cast.then"]), _pin("then", "out", links=["use.execute"])],
            function="GetAllActorsOfClass",
            **{"class": "GameplayStatics"},
        ),
        _node(
            "tgt", "get_var", [_pin("Target", "out", "object:Actor", links=["use.self"])], variable="Target", pure=True
        ),
        _node(
            "use",
            "call_function",
            [
                _pin("execute", "in", links=["all.then"]),
                _pin("then", "out", links=["loop.execute"]),
                _pin("self", "in", "object:Actor", links=["tgt.Target"]),
            ],
            function="K2_DestroyActor",
            **{"class": "Actor"},
        ),
        # exec cycle: loop -> back -> loop, no Delay
        _node(
            "loop",
            "branch",
            [
                _pin("execute", "in", links=["use.then", "back.then"]),
                _pin("then", "out", links=["back.execute"]),
                _pin("else", "out"),
            ],
        ),
        _node(
            "back",
            "set_var",
            [_pin("execute", "in", links=["loop.then"]), _pin("then", "out", links=["loop.execute"])],
            variable="Count",
        ),
        # never executed
        _node(
            "orphan",
            "call_function",
            [_pin("execute", "in"), _pin("then", "out")],
            function="Jump",
            **{"class": "Character"},
        ),
    ]
    return {
        "path": "/Game/T/BP_Planted.BP_Planted",
        "parent_class": "/Script/Engine.Actor",
        "status": "ok",
        "variables": [
            {"name": "Target", "type": "object:Actor"},
            {"name": "Count", "type": "int"},
            {"name": "Unused", "type": "float"},
        ],
        "components": [{"name": "Mesh", "class": "StaticMeshComponent"}],
        "graphs": [{"name": "EventGraph", "kind": "event_graph", "nodes": nodes}],
    }


def test_explain_entry_points_flow_and_dependencies():
    e = bp_analysis.explain(_spec())
    eps = {x["name"]: x for x in e["entry_points"]}
    assert set(eps) == {"ReceiveBeginPlay", "ReceiveTick"}
    assert eps["ReceiveBeginPlay"]["flow"] == "PrintString"
    tick = eps["ReceiveTick"]
    assert tick["flow"].startswith("Cast to BP_Hero_C → GetAllActorsOfClass → K2_DestroyActor")
    assert tick["reads"] == ["Target"] and tick["writes"] == ["Count"]
    assert any("GetPlayerPawn" in c for c in tick["calls"])
    assert e["external_calls"]["GameplayStatics"] == ["GetAllActorsOfClass", "GetPlayerPawn"]
    kinds = {x["to"]: x["kind"] for x in e["dependencies"]["edges"]}
    assert kinds["/Game/BP_Hero.BP_Hero_C"] == "casts" and kinds["/Script/Engine.Actor"] == "parent"
    assert "StaticMeshComponent" in kinds
    unused = next(v for v in e["variables"] if v["name"] == "Unused")
    assert not unused["read"] and not unused["written"]
    assert "**Entry points**" in e["markdown"] and "`ReceiveTick`" in e["markdown"]


def test_review_finds_every_planted_issue():
    r = bp_analysis.review(_spec(), tick_budget=3)
    rules = {(f["rule"], f.get("node") or f.get("variable")) for f in r["findings"]}
    assert ("unwired_exec", "orphan") in rules
    assert ("unused_variable", "Unused") in rules
    assert ("cast_in_tick", "cast") in rules
    assert ("expensive_in_tick", "all") in rules
    assert ("missing_is_valid", "use") in rules
    assert ("tick_heavy", "tick") in rules
    assert any(f["rule"] == "infinite_loop_risk" and f["severity"] == "error" for f in r["findings"])
    assert r["ok"] is False and r["counts"]["error"] >= 1
    assert r["findings"][0]["severity"] == "error"  # sorted by severity
    assert "infinite_loop_risk" in r["markdown"]


def test_review_clean_blueprint_and_guards():
    spec = _spec()
    g = spec["graphs"][0]["nodes"]
    # Fix everything: drop Tick logic, the orphan and the unused variable; guard Target with IsValid.
    spec["graphs"][0]["nodes"] = [n for n in g if n["id"] in ("begin", "print")]
    spec["variables"] = []
    r = bp_analysis.review(spec)
    assert r["ok"] is True and r["findings"] == []
    # An IsValid on the variable silences missing_is_valid; components are always valid.
    spec2 = _spec()
    spec2["graphs"][0]["nodes"].append(
        _node("iv", "macro", [_pin("InputObject", "in", "object:Object", links=["tgt.Target"])], macro="IsValid")
    )
    assert not any(f["rule"] == "missing_is_valid" for f in bp_analysis.review(spec2)["findings"])
    spec3 = _spec()
    spec3["components"].append({"name": "Target", "class": "SceneComponent"})
    assert not any(f["rule"] == "missing_is_valid" for f in bp_analysis.review(spec3)["findings"])


def test_latent_breaks_loop_and_recursion():
    nodes = [
        _node("ev", "custom_event", [_pin("then", "out", links=["d.execute"])], name="Pulse"),
        _node(
            "d",
            "call_function",
            [_pin("execute", "in", links=["ev.then"]), _pin("then", "out", links=["again.execute"])],
            function="Delay",
            latent=True,
        ),
        _node(
            "again",
            "call_function",
            [_pin("execute", "in", links=["d.then"]), _pin("then", "out")],
            function="Pulse",
            **{"class": "BP_X_C"},
        ),
    ]
    spec = {
        "path": "/Game/BP_X.BP_X",
        "status": "ok",
        "variables": [],
        "graphs": [{"name": "EventGraph", "nodes": nodes}],
    }
    assert not any(f["rule"] == "infinite_loop_risk" for f in bp_analysis.review(spec)["findings"])
    nodes.pop(1)
    nodes[0]["pins"][0]["links"] = ["again.execute"]
    nodes[1]["pins"][0]["links"] = ["ev.then"]
    assert any(f["rule"] == "infinite_loop_risk" for f in bp_analysis.review(spec)["findings"])


def test_bp_explain_and_review_tools(fake, tools):
    fake.on("bp_export", lambda p: {**_spec(), "include": p["include_pins"]})
    e = tools["bp_explain"]("/Game/T/BP_Planted")
    assert e["success"] and e["counts"]["entry_points"] == 2
    r = tools["bp_review"]("/Game/T/BP_Planted", tick_budget=100)
    assert r["success"] and not any(f["rule"] == "tick_heavy" for f in r["findings"])
    fake.on("bp_export", lambda p: {"success": False, "error": "Blueprint '/Game/Nope' not found"})
    assert tools["bp_review"]("/Game/Nope")["success"] is False


def test_bp_apply_forwards_new_ops(fake, tools):
    seen = {}
    fake.on(
        "bp_apply", lambda p: seen.update(p) or {"ok": True, "applied": len(p["operations"]), "failed": [], "ids": {}}
    )
    ops = [
        {"op": "add_dispatcher", "name": "OnOpened"},
        {"op": "implement_interface", "interface": "/Game/BPI_Use"},
        {
            "op": "add_node",
            "kind": "timeline",
            "name": "Lift",
            "tracks": [{"name": "A", "type": "float", "keys": [[0, 0]]}],
        },
    ]
    assert tools["bp_apply"]("/Game/BPI_Use", [], create_parent="Interface")["success"]
    assert seen["create_parent"] == "Interface"
    assert tools["bp_apply"]("/Game/BP_Door", ops)["applied"] == 3 and seen["operations"] == ops


def test_data_types_routes_plugin_and_script_ops(fake, ue, tools):  # noqa: F811
    batches = []

    def data_types(p):
        batches.append([o["op"] for o in p["operations"]])
        results = []
        for o in p["operations"]:
            if o["op"] == "struct":  # the plugin made the struct; the fake editor needs to know its fields
                ue.register_asset(
                    ue.ScriptStruct("S_Item", "/Game/Data/S_Item.S_Item", [m["name"] for m in o["members"]])
                )
            results.append(
                {
                    "op": o["op"],
                    "ok": o["op"] != "curve",
                    "path": o["asset"],
                    **({"error": "curve type 'x'"} if o["op"] == "curve" else {}),
                }
            )
        return {"ok": all(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results), "results": results}

    fake.on("data_types", data_types)
    r = tools["data_types"](
        [
            {"op": "enum", "asset": "/Game/Data/E_Rarity", "entries": ["Common", "Rare"]},
            {
                "op": "struct",
                "asset": "/Game/Data/S_Item",
                "members": [{"name": "Label", "type": "string"}, {"name": "Damage", "type": "float"}],
            },
            {
                "op": "data_table",
                "asset": "/Game/Data/DT_Items",
                "struct": "/Game/Data/S_Item",
                "csv": "Name,Label,Damage\nSword,Sword,10\nAxe,Axe,14",
            },
            {
                "op": "data_table",
                "asset": "/Game/Data/DT_Items",
                "rows": {"Bow": {"Label": "Bow", "Damage": 7}},
                "mode": "merge",
            },
            {"op": "curve_table", "asset": "/Game/Data/CT", "rows": {"Dmg": [[1, 2]]}},
        ]
    )
    assert r["success"], r
    assert batches == [["enum", "struct"], ["curve_table"]]
    assert [x["op"] for x in r["results"]] == ["enum", "struct", "data_table", "data_table", "curve_table"]
    assert r["results"][2]["created"] and r["results"][2]["row_count"] == 2
    assert r["results"][3]["row_count"] == 3  # merge kept Sword and Axe
    table = ue._state["assets"]["/Game/Data/DT_Items"]
    assert table.rows["Axe"] == {"Label": "Axe", "Damage": "14"} and table.rows["Bow"]["Damage"] == 7
    bad = tools["data_types"]([{"op": "curve", "asset": "/Game/C", "type": "x"}, {"op": "enum", "asset": "/Game/E"}])
    assert bad["success"] is False and bad["failed"] == 1 and "curve type" in bad["error"]
    assert tools["data_types"]([{"op": "nope"}])["success"] is False


def test_data_types_data_asset(ue, tools):  # noqa: F811
    class HeroData(ue.PrimaryDataAsset):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.max_health = 100.0
            self.display_name = ""

    ue.HeroData = HeroData
    r = tools["data_types"](
        [
            {
                "op": "data_asset",
                "asset": "/Game/Data/DA_Hero",
                "class": "HeroData",
                "properties": {"MaxHealth": 250, "display_name": "Hero"},
            }
        ]
    )
    assert r["success"], r
    da = ue._state["assets"]["/Game/Data/DA_Hero"]
    assert da.max_health == 250.0 and da.display_name == "Hero" and r["results"][0]["created"]
    bad = tools["data_types"]([{"op": "data_asset", "asset": "/Game/Data/DA_Hero", "properties": {"Nope": 1}}])
    assert bad["success"] is False and "no such property" in bad["error"]


def test_audio_tools(fake, tools):
    seen = {}

    def apply(p):
        seen.update(p)
        bad = [i for i, o in enumerate(p["operations"]) if o.get("class") == "Nope"]
        return {
            "ok": not bad,
            "failed": [{"index": i, "op": "add_node", "error": "no MetaSound node class 'Nope'"} for i in bad],
            "applied": len(p["operations"]) - len(bad),
            "ids": {"osc": "G1"},
        }

    fake.on("metasound_apply", apply)
    ok = tools["metasound_apply"](
        "/Game/Audio/MS_Tone", [{"op": "add_node", "id": "osc", "class": "Sine"}], format="stereo", one_shot=False
    )
    assert ok["success"] and ok["ids"] == {"osc": "G1"} and seen["format"] == "stereo" and seen["one_shot"] is False
    bad = tools["metasound_apply"]("/Game/Audio/MS_Tone", [{"op": "add_node", "class": "Nope"}])
    assert bad["success"] is False and "op 0 (add_node)" in bad["error"]
    fake.on("metasound_nodes", lambda p: {"nodes": [{"class": "UE.Sine.Audio"}], "total": 1, "q": p["query"]})
    assert tools["metasound_nodes"]("sine")["q"] == "sine"
    fake.on("sound_cue_apply", lambda p: {"ok": len(p["waves"]) > 0, "waves": len(p["waves"]), "pr": p["pitch_range"]})
    assert tools["sound_cue_apply"]("/Game/Audio/SC_Step", ["/Game/A", "/Game/B"], pitch_range=[0.9, 1.1])["pr"] == [
        0.9,
        1.1,
    ]
