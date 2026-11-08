"""bp_*, widget_*, bt_* wrappers: request shapes and result normalization (the C++ side is
verified live: UE-Bench tasks blueprint-*, ui-hud, ai-guard-patrol)."""

from unreal_mcp import bench


def test_bp_apply_forwards_and_reports_failures(fake, tools):
    seen = {}

    def bp_apply(p):
        seen.update(p)
        return {"ok": False, "applied": 1, "failed": [{"index": 1, "op": "connect", "error": "no input pin 'Nope'"}],
                "compile": {"ok": False, "errors": ["Target must have a connection"]}, "rolled_back": True}

    fake.on("bp_apply", bp_apply)
    r = tools["bp_apply"]("/Game/BP_X", [{"op": "add_node", "kind": "event", "event": "BeginPlay"}],
                          create_parent="Actor", dry_run=True)
    assert seen["blueprint"] == "/Game/BP_X" and seen["create_parent"] == "Actor" and seen["dry_run"] is True
    assert seen["atomic"] is True and seen["compile"] is True and len(seen["operations"]) == 1
    assert r["success"] is False and r["rolled_back"] is True
    assert "op 1 (connect): no input pin 'Nope'" in r["error"] and "compile: Target must have" in r["error"]


def test_bp_apply_success_and_export(fake, tools):
    fake.on("bp_apply", lambda p: {"ok": True, "applied": 3, "failed": [], "ids": {"begin": "ABC"}})
    fake.on("bp_export", lambda p: {"path": p["blueprint"], "graphs": [], "include": p["include_pins"]})
    ok = tools["bp_apply"]("/Game/BP_X", [])
    assert ok["success"] is True and ok["ids"] == {"begin": "ABC"} and "ok" not in ok
    e = tools["bp_export"]("/Game/BP_X", graphs=["EventGraph"], include_pins=False)
    assert e["success"] and e["include"] is False


def test_widget_apply_and_export(fake, tools):
    fake.on("widget_apply", lambda p: {"ok": p["create"], "applied": len(p["operations"]), "failed": []})
    fake.on("widget_export", lambda p: {"path": p["widget"], "root": {"name": "Root", "children": []}})
    assert tools["widget_apply"]("/Game/UI/WBP_HUD", [{"op": "add", "name": "Root", "class": "CanvasPanel"}],
                                 create=True)["success"] is True
    assert tools["widget_apply"]("/Game/UI/WBP_HUD", [])["success"] is False
    assert tools["widget_export"]("/Game/UI/WBP_HUD")["root"]["name"] == "Root"


def test_bt_build_maps_errors(fake, tools):
    teleport = ["root.children[0]: 'Teleport' is not a BTTaskNode class"]
    fake.on("bt_build", lambda p: {"ok": not p["root"], "errors": teleport if p["root"] else [], "nodes": 0})
    bad = tools["bt_build"]("/Game/AI/BT", root={"type": "Selector", "children": [{"type": "Teleport"}]})
    assert bad["success"] is False and "Teleport" in bad["error"]
    good = tools["bt_build"](blackboard="/Game/AI/BB", keys=[{"name": "Goal", "type": "vector"}])
    assert good["success"] is True


def test_bench_command_requirement_and_tool_checks(fake, tools):
    task = bench.Task(id="t", domain="d", title="t", prompt="p", check={}, solution=[], requires=["command:bp_apply"])
    assert "lacks bp_apply" in bench.requirement_missing(task, tools)
    fake.on("list_commands", lambda p: {"commands": ["ping", "bp_apply"], "plugin_version": "0.3.0"})
    assert bench.requirement_missing(task, tools) is None
    fake.on("widget_export", lambda p: {"root": {"name": "Root", "children": [
        {"name": "Box", "children": [{"name": "HealthBar", "class": "ProgressBar"}]}]}})
    def check(expect):
        return {"tool": "widget_export", "args": {"widget": "/Game/W"}, "expect": expect}

    r = bench._tool_checks(tools, [check("any(w['name'] == 'HealthBar' for w in walk(r['root']))"),
                                   check("r['root']['nope']"), check("__import__('os')")])
    assert [x["passed"] for x in r["results"]] == [True, False, False]
    assert "KeyError" in r["results"][1]["error"] and "NameError" in r["results"][2]["error"]
