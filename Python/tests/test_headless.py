"""headless_check: map open + pie_test suite + counters -> compact verdict, and its CLI."""

import json
from pathlib import Path

import pytest
from test_editor_scripts import ue  # noqa: F401  (fixture)
from test_pie import _world, pie  # noqa: F401  (fixture)

from unreal_mcp.tools import headless as h

DOCS_SUITE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "headless_midtown_traffic.json"


@pytest.fixture
def opened(fake):
    maps = []

    def level_open(p):
        maps.append(p)
        return {"current_level": p["path"]}

    fake.on("level_open", level_open)
    return maps


def _driving_world(ue):  # noqa: F811
    hero = _world(ue)
    hero.add_movement_input = lambda d, scale: hero.set_actor_location(
        ue.Vector(hero.get_actor_location().x + d.x * 10 * scale, 0, 100), False, False)
    return hero


SUITE = {
    "name": "drive", "map": "/Game/Maps/City", "duration_s": 0,
    "steps": [{"do": "player.add_movement_input(vec([1, 0, 0]), 1.0)", "repeat": 6, "interval_s": 0.1},
              {"capture": "shot"}],
    "assertions": [{"name": "hero moved", "type": "distance", "a": "@player", "point": [0, 0, 100],
                    "op": ">=", "value": 50}],
    "counters": [{"name": "hero_x", "expr": "player.get_actor_location().x", "min_delta": 50},
                 {"name": "coins", "expr": "len([a for a in actors if 'Coin' in a.get_actor_label()])", "min": 3}],
}


def test_headless_check_passes_with_counters(pie, ue, conn, opened):  # noqa: F811
    _driving_world(ue)
    r = h.run_headless_check(lambda: conn, SUITE, sleep=lambda s: None)
    assert r["success"] and r["passed"], r
    assert opened == [{"path": "/Game/Maps/City", "create": False, "save_current": False, "discard_changes": True}]
    assert r["map"] == "/Game/Maps/City"
    assert r["counters"]["hero_x"] == {"start": 0.0, "end": 60.0, "delta": 60.0}
    assert r["counters"]["coins"]["end"] == 3
    assert r["skipped_steps"][0]["step"] == 1  # capture needs a renderer
    assert r["assertions"] == 1 and r["failures"] == []
    assert not pie.get("pie")  # stopped


def test_headless_check_reports_failures_compactly(pie, ue, conn, opened):  # noqa: F811
    _driving_world(ue)
    pie["pie_log"] = [{"seq": 2, "verbosity": "Error", "message": "LogNyc: traffic lane missing"},
                      {"seq": 3, "verbosity": "Error", "message": "LogStreaming: harmless"}]
    suite = {**SUITE, "counters": [{"name": "hero_x", "expr": "player.get_actor_location().x", "min_delta": 500},
                                   {"name": "broken", "expr": "nope()"}],
             "assertions": [{"name": "boss", "type": "actor_exists", "label": "Boss"}],
             "fail_on_log_errors": True, "ignore_log_errors": ["logstreaming"]}
    r = h.run_headless_check(lambda: conn, suite, sleep=lambda s: None)
    assert r["success"] and not r["passed"]
    text = "\n".join(r["failures"])
    assert "boss" in text and "min delta 500 not met (delta = 60.0)" in text
    assert "counter broken: NameError" in text
    assert "1 error line(s)" in text
    assert r["log_errors"] == ["LogNyc: traffic lane missing"]


def test_headless_check_failing_step(pie, ue, conn, opened):  # noqa: F811
    _driving_world(ue)
    suite = {"name": "s", "duration_s": 0, "steps": [
        {"check": [{"name": "far", "type": "distance", "a": "@player", "point": [0, 0, 100], "op": ">", "value": 9}]}]}
    r = h.run_headless_check(lambda: conn, suite, sleep=lambda s: None)
    assert not r["passed"] and r["failures"][0].startswith("step 0 (assertions): far actual=0")
    assert opened == []  # no map: current level


def test_headless_check_infrastructure_errors(fake, conn):
    assert "bad suite" in h.run_headless_check(lambda: conn, {"nope": 1})["error"]
    fake.on("level_open", lambda p: (_ for _ in ()).throw(RuntimeError("no such map")))
    r = h.run_headless_check(lambda: conn, {"map": "/Game/X"}, pie=lambda *a, **k: pytest.fail("ran pie"))
    assert not r["success"] and "level_open failed" in r["error"]


def test_headless_check_passes_suite_to_run_pie_test(fake, conn, opened):
    seen = {}

    def fake_pie(get_conn, assertions, **kw):
        seen.update(assertions=assertions, **kw)
        return {"success": False, "error": "PIE did not start within 60.0s"}

    r = h.run_headless_check(lambda: conn, {"map": "/Game/X", "steps": [{"wait_s": 1}],
                                            "counters": [{"name": "n", "expr": "1"}]}, pie=fake_pie)
    assert not r["passed"] and "did not start" in r["error"]
    assert seen["steps"][0] == {"check": [{"type": "python", "name": "counter:n", "expr": "1"}]}
    assert seen["assertions"][-1]["name"] == "counter:n" and seen["stop"] is True
    assert (seen["width"], seen["height"]) == (640, 360)


def test_load_suite_from_file_and_string(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"name": "x"}))
    assert h.load_suite(str(p))["name"] == "x"
    assert h.load_suite('{"name": "y"}')["name"] == "y"
    with pytest.raises(ValueError, match="counter needs"):
        h.load_suite({"counters": [{"name": "c"}]})


def test_docs_example_suite_is_valid():
    s = h.load_suite(str(DOCS_SUITE))
    assert s["map"] == "/Game/Planet/Maps/NYC_Planet"
    assert any(st.get("hold") == "W" and st.get("duration_s", 0) >= 50 for st in s["steps"])


def test_cli_exit_codes(pie, ue, conn, opened, tmp_path, monkeypatch, capsys):  # noqa: F811
    _driving_world(ue)
    monkeypatch.setattr(h.time, "sleep", lambda s: None)  # run_pie_test looks time.sleep up per call
    import unreal_mcp.connection as cmod
    monkeypatch.setattr(cmod, "UnrealConnection", lambda *a, **k: conn)
    p = tmp_path / "suite.json"
    p.write_text(json.dumps(SUITE))
    assert h.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS drive on /Game/Maps/City") and "counter hero_x: 0.0 -> 60.0" in out
    p.write_text(json.dumps({**SUITE, "assertions": [{"type": "actor_exists", "label": "Boss"}]}))
    assert h.main([str(p), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False
    p.write_text("[]")
    assert h.main([str(p)]) == 2
