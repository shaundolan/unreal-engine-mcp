"""pie_test orchestration: start -> wait -> assert in game world -> log checks -> stop."""

import pytest
from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp.tools.testing import run_pie_test


@pytest.fixture
def pie(fake, ue):  # noqa: F811
    state = ue._state
    log = []

    def project_context(p):
        return {"pie_running": bool(state.get("pie"))}

    def editor_action(p):
        if p["action"] == "pie_start":
            state["pie"] = True
            log.append({"seq": len(log) + 1, "verbosity": "Display", "message": "PIE: Play in editor start"})
            log.extend(state.get("pie_log", []))
        elif p["action"] == "pie_stop":
            state["pie"] = False
        return {"action": p["action"]}

    def editor_log(p):
        since = p.get("since_seq", 0)
        return {"entries": [e for e in log if e["seq"] > since], "last_seq": len(log)}

    fake.on("project_context", project_context)
    fake.on("editor_action", editor_action)
    fake.on("editor_log", editor_log)
    return state


def _world(ue):  # noqa: F811
    sub = ue.get_editor_subsystem(ue.EditorActorSubsystem)
    hero = sub.spawn_actor_from_class(ue.Character, ue.Vector(0, 0, 100), ue.Rotator())
    hero.set_actor_label("Hero")
    ue._state["player"] = hero
    for i in range(3):
        c = sub.spawn_actor_from_class(ue.StaticMeshActor, ue.Vector(200 * (i + 1), 0, 0), ue.Rotator())
        c.set_actor_label(f"Coin_{i}")
    return hero


def test_pie_test_passes_and_fails_per_assertion(pie, ue, conn):  # noqa: F811
    _world(ue)
    pie["pie_log"] = [{"seq": 2, "verbosity": "Warning", "message": "Game Started"}]
    r = run_pie_test(lambda: conn, [
        {"name": "player alive", "type": "property", "target": "@player", "property": "health",
         "op": ">=", "value": 100},
        {"type": "actor_exists", "class": "StaticMeshActor", "min": 3, "max": 3},
        {"type": "actor_exists", "label": "Boss"},
        {"type": "distance", "a": "@player", "b": "Coin_0", "op": "<=", "value": 250},
        {"type": "distance", "a": "Hero", "point": [0, 0, 100], "op": "approx", "value": 0},
        {"type": "python", "expr": "len([a for a in actors if 'Coin' in a.get_actor_label()]) == 3"},
        {"type": "property", "target": "Hero", "property": "nope", "op": "==", "value": 1},
        {"type": "log_contains", "text": "game started"},
        {"type": "no_errors"},
        {"type": "property", "target": "Hero", "property": "health", "op": "~", "value": 1},
    ], duration_s=0, sleep=lambda s: None)
    assert r["success"] and r["total"] == 10
    passed = {x["index"]: x["passed"] for x in r["results"]}
    assert passed == {0: True, 1: True, 2: False, 3: True, 4: True, 5: True, 6: False, 7: True, 8: True, 9: False}
    assert r["results"][0]["name"] == "player alive" and r["results"][3]["actual"] == 223.61
    assert "AttributeError" in r["results"][6]["error"] and "unknown op" in r["results"][9]["error"]
    assert r["passed"] is False and r["failed_count"] == 3
    assert pie["pie"] is False  # stopped afterwards


def test_pie_test_detects_errors_in_log(pie, ue, conn):  # noqa: F811
    _world(ue)
    pie["pie_log"] = [{"seq": 2, "verbosity": "Error", "message": "Accessed None trying to read Health"}]
    r = run_pie_test(lambda: conn, [{"type": "no_errors"}, {"type": "log_absent", "text": "accessed none"}],
                     duration_s=0, sleep=lambda s: None)
    assert r["passed"] is False and r["failed_count"] == 2
    assert r["errors_in_log"] == ["Accessed None trying to read Health"]


def test_pie_test_refuses_when_already_running(pie, conn):
    pie["pie"] = True
    r = run_pie_test(lambda: conn, [{"type": "no_errors"}], duration_s=0, sleep=lambda s: None)
    assert r["success"] is False and "already running" in r["error"]
    assert pie["pie"] is True  # left alone


def test_pie_test_keep_running(pie, ue, conn):  # noqa: F811
    _world(ue)
    r = run_pie_test(lambda: conn, [{"type": "actor_exists", "label": "Hero"}], duration_s=0, stop=False,
                     sleep=lambda s: None)
    assert r["passed"] and pie["pie"] is True


def test_pie_timeline_drives_the_game(pie, ue, conn):  # noqa: F811
    hero = _world(ue)
    hero.add_movement_input = lambda d, scale: hero.set_actor_location(  # one step per call
        ue.Vector(hero.get_actor_location().x + d.x * 10 * scale, 0, 100), False, False)
    hero.jump = lambda: setattr(hero, "jumped", True)
    hero.jumped = False
    waits = []
    r = run_pie_test(lambda: conn, [{"type": "property", "target": "@player", "property": "jumped", "value": True}],
                     duration_s=0, sleep=waits.append, steps=[
        {"check": [{"type": "distance", "a": "@player", "point": [0, 0, 100], "op": "approx", "value": 0}]},
        {"do": "player.add_movement_input(vec([1, 0, 0]), 1.0)", "repeat": 5, "interval_s": 0.1},
        {"wait_s": 0.5},
        {"check": [{"type": "distance", "a": "@player", "point": [0, 0, 100], "op": ">=", "value": 49}]},
        {"do": "player.jump()"},
    ])
    assert r["passed"], r["steps"]
    assert [s["ok"] for s in r["steps"]] == [True] * 5
    assert r["steps"][3]["assertions"][0]["actual"] == 50.0
    assert waits.count(0.1) == 4 and 0.5 in waits


def test_pie_timeline_stops_on_broken_step(pie, ue, conn):  # noqa: F811
    _world(ue)
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None, steps=[
        {"do": "player.fly_to_moon()"}, {"check": [{"type": "no_errors"}]}, {"bogus": 1}])
    assert r["passed"] is False and len(r["steps"]) == 1
    assert "fly_to_moon" in r["steps"][0]["error"] and pie["pie"] is False


def test_pie_until_step_drives_until_condition(pie, ue, conn):  # noqa: F811
    hero = _world(ue)
    r = run_pie_test(lambda: conn, [{"type": "python", "expr": "player.get_actor_location().x >= 300"}], duration_s=0,
                     sleep=lambda s: None, steps=[
        {"do": "player.set_actor_location(vec([player.get_actor_location().x + 50, 0, 100]), False, False)",
         "until": "player.get_actor_location().x >= 300", "timeout_s": 5},
        {"until": "False", "timeout_s": 0}])
    assert r["steps"][0]["ok"] and r["steps"][0]["tries"] == 6 and hero.get_actor_location().x == 300
    assert r["steps"][1]["ok"] is False and "still false" in r["steps"][1]["error"]
    assert r["results"][0]["passed"] and r["passed"] is False


def test_pie_input_steps_send_keys_and_actions(pie, fake, ue, conn):  # noqa: F811
    _world(ue)
    sent = []

    def pie_input(p):
        sent.append(p)
        if p.get("key") == "Nope":
            return {"success": False, "error": "unknown key 'Nope'"}
        return {"key": p.get("key"), "action": p.get("action"), "mode": p.get("mode"), "pawn": "Hero"}

    fake.on("pie_input", pie_input)
    fake.on("frame_stats", lambda p: {"done": True, "frames": 3, "wanted": 3, "fps_avg": 125.0,
                                      "frame_ms": {"avg": 8.0, "p95": 9.5}, "gpu_ms": {"avg": 4.0}})
    waits = []
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=waits.append, steps=[
        {"press": "SpaceBar"},
        {"hold": "W", "duration_s": 1.5, "async": True},
        {"axis": "/Game/Input/IA_Move", "value": [0, 1], "duration_s": 2},
        {"look": [5, -2], "duration_s": 0.5},
        {"look": [1, 0], "action": "/Game/Input/IA_Look", "player": 1},
        {"release_all": True},
        {"frame_stats": 3, "max_p95_ms": 33.3, "min_fps": 30},
        {"frame_stats": {"frames": 3}, "max_gpu_ms": 1.0},
    ])
    assert [s["ok"] for s in r["steps"]] == [True] * 7 + [False], r["steps"]
    assert "gpu_ms.avg 4.0 > 1.0" in r["steps"][7]["error"] and r["passed"] is False
    assert r["steps"][6]["frame_stats"]["fps_avg"] == 125.0 and "wanted" not in r["steps"][6]["frame_stats"]
    assert r["steps"][0]["input"] == [{"key": "SpaceBar", "mode": "press", "pawn": "Hero"}]
    inputs = [p for p in sent if p.get("mode") != "release_all"]
    assert inputs[0] == {"key": "SpaceBar", "mode": "press", "duration_s": 0.1}
    assert inputs[1] == {"key": "W", "mode": "hold", "duration_s": 1.5}
    assert inputs[2] == {"action": "/Game/Input/IA_Move", "mode": "axis", "value": [0, 1], "duration_s": 2.0}
    assert inputs[3] == {"key": "MouseX", "mode": "axis", "value": 5.0, "duration_s": 0.5}
    assert inputs[4] == {"key": "MouseY", "mode": "axis", "value": -2.0, "duration_s": 0.5}
    assert inputs[5] == {"action": "/Game/Input/IA_Look", "mode": "axis", "value": [1.0, 0.0],
                         "duration_s": 0.5, "player": 1}
    assert [p["mode"] for p in sent].count("release_all") == 1
    # blocking steps wait for their input to finish; the async hold does not
    assert 0.1 + 0.05 in waits and 2.0 + 0.05 in waits and 1.5 + 0.05 not in waits

    bad = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None,
                       steps=[{"press": "Nope"}, {"press": "SpaceBar"}])
    assert bad["passed"] is False and len(bad["steps"]) == 1 and "unknown key" in bad["steps"][0]["error"]


def test_pie_test_reports_navmesh_repair(pie, ue, conn, monkeypatch):  # noqa: F811
    from unreal_mcp.tools import testing
    _world(ue)
    probes = iter([{"volumes": 1, "empty": 1, "building": False}, {"volumes": 1, "empty": 0, "building": True},
                   {"volumes": 1, "empty": 0, "building": False}])
    rebuild_requested = []

    def fake_run_python(get_conn, code, timeout_s=30):
        rebuild_requested.append("if empty and True" in code)
        return {"success": True, "result": next(probes)}

    monkeypatch.setattr(testing, "run_python", fake_run_python)
    r = run_pie_test(lambda: conn, [{"type": "actor_exists", "label": "Hero"}], duration_s=0, sleep=lambda s: None)
    assert r["passed"] and r["navigation_ready"] is True and r["navmesh_repaired"] is True
    assert rebuild_requested == [True, False, False]  # rebuilt once, then only polled

    probes = iter([{"volumes": 1, "empty": 0, "building": False}])
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None)
    assert r["navigation_ready"] is True and r["navmesh_repaired"] is False


def test_frame_stats_and_pie_input_tools(fake, tools, monkeypatch):
    from unreal_mcp.tools import testing
    monkeypatch.setattr(testing.time, "sleep", lambda s: None)
    reads = iter([{"done": False, "frames": 10}, {"done": True, "frames": 60, "frame_ms": {"avg": 16.7}}])
    seen = []

    def frame_stats(p):
        seen.append(p["action"])
        return {"frames": p.get("frames")} if p["action"] == "start" else next(reads)

    fake.on("frame_stats", frame_stats)
    r = tools["frame_stats"](60)
    assert r["success"] and r["frames"] == 60 and seen == ["start", "read", "read"]
    fake.on("pie_input", lambda p: p)
    r = tools["pie_input"](key="SpaceBar", mode="hold", duration_s=2)
    assert r == {"success": True, "key": "SpaceBar", "mode": "hold", "duration_s": 2, "player": 0}
