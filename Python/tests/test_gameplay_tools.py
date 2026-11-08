"""Enhanced Input, game setup, C++ source, Live Coding wait, performance audit."""

from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp.tools.gameplay import wait_for_live_coding


def test_enhanced_input(ue, tools):  # noqa: F811
    r = tools["enhanced_input"](
        actions=[{"name": "IA_Move", "value_type": "Axis2D"}, {"name": "IA_Jump"}],
        contexts=[{"name": "IMC_Default", "mappings": [
            {"action": "IA_Jump", "key": "SpaceBar"},
            {"action": "IA_Move", "key": "S", "modifiers": ["SwizzleAxis", "Negate"]},
        ]}])
    assert r["success"] and [a["value_type"] for a in r["actions"]] == ["AXIS2D", "BOOLEAN"]
    assert r["contexts"][0]["mappings"] == ["SpaceBar -> IA_Jump", "S -> IA_Move [SwizzleAxis, Negate]"]
    imc = ue._state["assets"]["/Game/Input/IMC_Default"]
    assert [m.key.key_name for m in imc.mappings] == ["SpaceBar", "S"]
    assert [type(x).__name__ for x in imc.mappings[1].modifiers] == ["InputModifierSwizzleAxis", "InputModifierNegate"]
    again = tools["enhanced_input"](actions=[{"name": "IA_Move", "value_type": "Axis2D"}])
    assert again["actions"][0]["created"] is False
    assert tools["enhanced_input"](actions=[{"name": "IA_X", "value_type": "Axis9D"}])["success"] is False
    bad = tools["enhanced_input"](contexts=[{"name": "IMC_B", "mappings": [
        {"action": "IA_Jump", "key": "E", "modifiers": ["Teleport"]}]}])
    assert bad["success"] is False and "InputModifierTeleport" in bad["traceback"]


def test_game_setup(ue, tools):  # noqa: F811
    ue.PlayerGM = type("BP_ArenaGameMode_C", (ue.Object,), {})
    assert tools["game_setup"]()["changed"] == []
    r = tools["game_setup"](level_game_mode="unreal.PlayerGM", game_default_map="/Game/Maps/Arena")
    assert sorted(r["changed"]) == ["game_default_map", "level_game_mode"]
    assert r["game_default_map"] == "/Game/Maps/Arena"
    assert ue.GameMapsSettings.get_game_maps_settings().saved == 1
    assert tools["game_setup"](level_game_mode="/Game/Nope.Nope_C")["success"] is False


def test_cpp_source(ue, tools, tmp_path):  # noqa: F811
    proj = tmp_path / "Proj"
    (proj / "Source/Game/Private").mkdir(parents=True)
    (proj / "Source/Game/Private/Foo.cpp").write_text("int x;\n")
    (proj / "secret.txt").write_text("no")
    ue.Paths.project = str(proj) + "/"
    listed = tools["cpp_source"]("list", pattern="Source/*")
    assert listed["files"] == ["Source/Game/Private/Foo.cpp"]
    assert tools["cpp_source"]("read", path="Source/Game/Private/Foo.cpp")["content"] == "int x;\n"
    w = tools["cpp_source"]("write", path="Source/Game/Public/Foo.h", content="#pragma once\n")
    assert w["created"] and (proj / "Source/Game/Public/Foo.h").read_text() == "#pragma once\n"
    for bad in ("../outside.cpp", "secret.txt", "Source/../../x.cpp"):
        r = tools["cpp_source"]("read", path=bad)
        assert r["success"] is False and "PermissionError" in r["traceback"], bad
    assert tools["cpp_source"]("write", path="Source/Game/run.bat", content="x")["success"] is False


def _log_conn(entries):
    class C:
        def send_command(self, cmd, params, timeout=None):
            since = params.get("since_seq", 0)
            return {"status": "success", "result": {"entries": [e for e in entries if e["seq"] > since]}}
    c = C()
    return lambda: c


def test_live_coding_success_and_failure():
    ok = _log_conn([{"seq": 5, "category": "LogLiveCoding", "message": "Starting Live Coding compile."},
                    {"seq": 6, "category": "LogLiveCoding", "message": "Live coding succeeded"}])
    assert wait_for_live_coding(ok, 4, 5, sleep=lambda s: None)["compiled"] is True
    bad = _log_conn([{"seq": 7, "category": "LogLiveCoding", "message": "Foo.cpp(12): error C2065: 'y': undeclared"},
                     {"seq": 8, "category": "LogLiveCoding", "message": "Live coding failed"}])
    r = wait_for_live_coding(bad, 0, 5, sleep=lambda s: None)
    assert r["compiled"] is False and "C2065" in r["errors"][0]
    none = _log_conn([])
    assert wait_for_live_coding(none, 0, 0.01, sleep=lambda s: None)["success"] is False


def test_performance_audit(ue, tools):  # noqa: F811
    rock = ue.StaticMesh("SM_Rock", "/Game/SM_Rock.SM_Rock")
    rock.triangles = 100_000
    ue.register_asset(rock)
    ops = [{"op": "spawn", "asset": "/Game/SM_Rock", "pattern": {"type": "line", "count": 60}}]
    ops += [{"op": "spawn", "class": "PointLight"} for _ in range(5)]
    tools["scene_compose"](ops)
    r = tools["performance_audit"](triangle_budget=1_000_000)
    assert r["triangles_lod0"] == 6_000_000 and r["mesh_components"] == 60 and r["unique_meshes"] == 1
    assert r["lights"]["movable"] == 5 and r["lights"]["shadow_casting_movable"] == 5
    assert r["instancing_candidates"] == [{"mesh": "/Game/SM_Rock.SM_Rock", "copies": 60}]
    assert len(r["issues"]) == 3
