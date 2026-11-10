"""Niagara authoring tools: name resolution, request shapes and result normalization for niagara_apply /
niagara_export, particle sampling and the pie_test "niagara" assertion, all against the fake plugin
(tests/fake_unreal_niagara.py models the C++ command semantics: atomic batches, rollback, dry run)."""

import pytest
from fake_unreal_niagara import FakeNiagara

from unreal_mcp.tools import niagara as nia


@pytest.fixture
def fx(fake):
    model = FakeNiagara()
    model.install(fake)
    return model


# ---------------------------------------------------------------------------------------------- names


def test_friendly_names_resolve_to_engine_assets():
    ops = nia.normalize_ops(
        [
            {"op": "add_emitter", "name": "A", "from": "fountain"},
            {"op": "add_emitter", "name": "B", "template": "ribbon"},
            {"op": "add_module", "emitter": "A", "stage": "Particle Update", "script": "collision"},
            {
                "op": "set_inputs",
                "emitter": "A",
                "stage": "spawn",
                "module": "InitializeParticle",
                "inputs": {
                    "Lifetime": {"dynamic": "uniform ranged float", "inputs": {"Minimum": 1}},
                    "Color": {"dynamic": {"script": "ColorFromCurve"}},
                },
            },
            {
                "op": "set_input",
                "emitter": "A",
                "stage": "event:0",
                "module": "M",
                "input": "X",
                "dynamic": "RandomRangeVector",
            },
        ]
    )
    assert ops[0]["from"] == "/Niagara/DefaultAssets/Templates/Emitters/Fountain"
    assert ops[1]["from"].endswith("/LocationBasedRibbon") and "template" not in ops[1]
    assert ops[2]["script"] == "/Niagara/Modules/Collision/Collision" and ops[2]["stage"] == "particle_update"
    assert ops[3]["stage"] == "particle_spawn"
    life = ops[3]["inputs"]["Lifetime"]
    assert life["dynamic"] == "/Niagara/DynamicInputs/UniformRange/UniformRangedFloat" and life["inputs"] == {
        "Minimum": 1
    }
    assert ops[3]["inputs"]["Color"]["dynamic"]["script"].endswith("/ValueFromCurve/ColorFromCurve")
    assert ops[4]["stage"] == "event:0" and ops[4]["dynamic"].endswith("/V2/RandomRangeVector")
    # unknown names pass through (the plugin searches the asset registry)
    assert nia.resolve_module("MyScratchModule") == "MyScratchModule"
    assert nia.resolve_create_from("template:SimpleExplosion") == "template:SimpleExplosion"
    assert nia.resolve_create_from("fountain") == "template:Fountain"
    assert nia.resolve_create_from("empty") == "empty"
    with pytest.raises(ValueError, match="add_renderer"):
        nia.normalize_ops([{"op": "add_renderer", "type": "laser"}])
    with pytest.raises(ValueError, match="needs"):
        nia.normalize_ops([{"emitter": "x"}])


def test_apply_reports_failures_and_rolls_back(fx, tools):
    r = tools["niagara_apply"](
        "/Game/FX/NS_T",
        create_from="empty",
        operations=[
            {"op": "add_emitter", "name": "Sparks", "from": "burst"},
            {"op": "add_module", "emitter": "Sparks", "stage": "particle_update", "script": "NoSuchModule"},
        ],
    )
    assert r["success"] is False and r["rolled_back"] and not r["created"]
    assert "op 1 (add_module)" in r["error"] and "NoSuchModule" in r["error"]
    assert "/Game/FX/NS_T" not in fx.systems  # a failed create leaves nothing behind
    sent = fx.requests[-1]
    assert sent["create_from"] == "empty" and sent["operations"][0]["from"].endswith("/OmnidirectionalBurst")


def test_apply_dry_run_and_compile_errors(fx, tools):
    ok = tools["niagara_apply"]("/Game/FX/NS_A", create_from="fountain", operations=[])
    assert ok["success"] and ok["created"] and ok["compile"]["ok"]
    before = tools["niagara_export"]("/Game/FX/NS_A")
    dry = tools["niagara_apply"](
        "/Game/FX/NS_A", dry_run=True, operations=[{"op": "set_emitter", "emitter": "Fountain", "rename": "Water"}]
    )
    assert dry["success"] and dry["rolled_back"] and dry["dry_run"]
    assert tools["niagara_export"]("/Game/FX/NS_A")["emitters"] == before["emitters"]
    fx.force_compile_error = "Fountain: Particles.Bogus is never written"
    bad = tools["niagara_apply"](
        "/Game/FX/NS_A", operations=[{"op": "set_emitter", "emitter": "Fountain", "rename": "W"}]
    )
    assert bad["success"] is False and bad["rolled_back"] and "Particles.Bogus" in bad["error"]
    assert tools["niagara_export"]("/Game/FX/NS_A")["emitters"][0]["name"] == "Fountain"


def test_missing_system_without_create_from(fx, tools):
    r = tools["niagara_apply"]("/Game/FX/NS_None", operations=[])
    assert r["success"] is False and "create_from" in r["error"]


# ---------------------------------------------------------------------------------------------- sampling


def test_sampling_stats_runs_and_bounds(fake, tools, monkeypatch):
    seq = iter([0, 30, 12, 0, 0, 25, 3, 0])
    zs = iter([5.0, 2.0, 1.0, 4.0, 6.0, 0.5, 1.0, 1.0])

    def particles(p):
        n, z = next(seq), next(zs)
        e = {"name": "Sparks", "particles": n}
        if n:
            e["positions"] = {"min": [0, 0, z], "max": [10, 10, z + 100]}
        return {
            "world": "pie",
            "particles": n,
            "components": [{"actor": "Sparks", "system": "/Game/NS", "active": True, "emitters": [e]}],
        }

    fake.on("niagara_particles", particles)
    r = tools["niagara_particles"](actor="Sparks", samples=8, interval_s=0)
    em = r["emitters"][0]
    assert (em["max"], em["min"], em["last"], em["runs"]) == (30, 0, 0, 2)
    assert em["positions"]["min"][2] == 0.5 and em["positions"]["max"][2] == 102.0
    assert r["total"]["series"] == [0, 30, 12, 0, 0, 25, 3, 0]


def test_niagara_assertion_metrics(fake, monkeypatch):
    from unreal_mcp.config import Settings
    from unreal_mcp.connection import UnrealConnection

    conn = UnrealConnection(Settings(port=fake.port))
    fake.on(
        "niagara_particles",
        lambda p: {
            "world": "pie",
            "components": [
                {
                    "actor": "Rain",
                    "emitters": [
                        {"name": "Drops", "particles": 40, "positions": {"min": [0, 0, -3], "max": [5, 5, 900]}}
                    ],
                },
                {"actor": "Other", "emitters": [{"name": "X", "particles": 2}]},
            ],
        },
    )
    get = lambda: conn  # noqa: E731
    a = nia.niagara_assertion(
        get,
        {
            "type": "niagara",
            "actor": "Rain",
            "metric": "min_z",
            "op": ">=",
            "value": -10,
            "samples": 3,
            "interval_s": 0,
        },
        0,
        sleep=lambda s: None,
    )
    assert a["passed"] and a["actual"] == -3
    b = nia.niagara_assertion(
        get,
        {
            "type": "niagara",
            "actor": "Rain",
            "emitter": "Drops",
            "op": ">=",
            "value": 50,
            "samples": 2,
            "interval_s": 0,
        },
        1,
        sleep=lambda s: None,
    )
    assert not b["passed"] and b["actual"] == 40
    c = nia.niagara_assertion(get, {"type": "niagara", "actor": "Nope", "samples": 1}, 2, sleep=lambda s: None)
    assert not c["passed"] and "Nope" in c["error"]
    conn.disconnect()


def test_pie_test_routes_niagara_assertions(fake, tools, monkeypatch):
    state = {"pie": False}
    fake.on("project_context", lambda p: {"pie_running": state["pie"]})
    fake.on("editor_action", lambda p: state.update(pie=p["action"] == "pie_start") or {"action": p["action"]})
    fake.on("editor_log", lambda p: {"entries": [], "last_seq": 0})
    fake.on("execute_python", lambda p: {"result": {"volumes": 0}, "stdout": ""})
    fake.on(
        "niagara_particles",
        lambda p: {
            "world": "pie",
            "components": [{"actor": "Fire", "emitters": [{"name": "Flames", "particles": 50}]}],
        },
    )
    monkeypatch.setattr("unreal_mcp.tools.testing.time.sleep", lambda s: None)
    r = tools["pie_test"](
        duration_s=0,
        assertions=[
            {
                "type": "niagara",
                "actor": "Fire",
                "op": ">=",
                "value": 20,
                "samples": 2,
                "interval_s": 0,
                "name": "fire alive",
            }
        ],
    )
    assert r["success"] and r["passed"], r
    assert r["results"][0]["name"] == "fire alive" and r["results"][0]["actual"] == 50
