"""UE-Bench: every task's checker passes on its reference solution and fails on an untouched level,
run end to end against the fake editor (reset -> setup -> agent -> scene_assert / pie_test)."""

import json
import sys

import pytest
from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp import bench

TASKS = bench.load_tasks()


@pytest.fixture(scope="module")
def gen3d_server():
    from gen3d_mock import MockGen3D
    srv = MockGen3D()  # the real servers/gen3d app with the mock backend (gen3d-* tasks)
    yield srv
    srv.close()


@pytest.fixture
def editor(fake, ue, tools, monkeypatch, gen3d_server, tmp_path):  # noqa: F811
    """Fake editor with engine basic shapes and a PIE that spawns a pawn at the PlayerStart."""
    monkeypatch.setenv("UNREAL_MCP_GEN3D_URL", gen3d_server.url)
    monkeypatch.setenv("UNREAL_MCP_WORK_DIR", str(tmp_path / "work"))
    state = ue._state
    for shape in ("Cube", "Cylinder", "Plane", "Sphere"):
        ue.register_asset(ue.StaticMesh(shape, f"/Engine/BasicShapes/{shape}.{shape}"))
    ue.add_mannequin()  # template content the anim-* tasks need (scripts/install_mannequin.py live)

    def gameplay_tags(p):
        added = [t for t in p.get("tags", []) if t not in state["tags"]]
        state["tags"].update(added)
        return {"ok": True, "tags": sorted(state["tags"]), "added": added, "already_existed": [], "errors": []}

    fake.on("gameplay_tags", gameplay_tags)
    log = []

    def editor_action(p):
        if p["action"] == "pie_start":
            starts = [a for a in state["actors"] if isinstance(a, ue.PlayerStart)]
            loc = starts[0].get_actor_location() if starts else None
            if loc is not None:
                pawn = ue.Character(ue.Vector(loc.x, loc.y, loc.z), ue.Rotator())
                state["actors"].append(pawn)
                state["player"] = pawn
            state["pie"] = True
        elif p["action"] == "pie_stop":
            pawn = state.pop("player", None)
            if pawn in state["actors"]:
                state["actors"].remove(pawn)
            state["pie"] = False
        return {"action": p["action"]}

    fake.on("project_context", lambda p: {"pie_running": bool(state.get("pie"))})
    fake.on("editor_action", editor_action)
    fake.on("editor_log", lambda p: {"entries": [e for e in log if e["seq"] > p.get("since_seq", 0)],
                                     "last_seq": len(log)})
    monkeypatch.setattr("unreal_mcp.tools.testing.time.sleep", lambda s: None)
    return tools


def test_tasks_load_and_cover_domains():
    assert len(TASKS) >= 15
    assert {t.domain for t in TASKS} >= {"scene", "assets", "materials", "cinematics", "input", "performance",
                                         "modeling", "gameplay"}
    assert len({t.id for t in TASKS}) == len(TASKS)


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_reference_solution_passes(editor, task):
    r = bench.run_task(editor, task, agent="solution")
    if r["status"] == "skipped":
        pytest.skip(r["reason"])
    bad_steps = [s for s in r["agent"]["steps"] if not s["ok"]]
    assert not bad_steps, bad_steps
    assert r["status"] == "passed", r["failures"]


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_untouched_level_fails(editor, task):
    r = bench.run_task(editor, task, agent="none")
    if r["status"] == "skipped":
        pytest.skip(r["reason"])
    assert r["status"] == "failed" and r["failures"]


def test_reset_isolates_tasks(editor, ue):  # noqa: F811
    task = next(t for t in TASKS if t.id == "scene-cleanup")
    assert bench.run_task(editor, task, agent="solution")["status"] == "passed"
    again = bench.run_task(editor, task, agent="none")  # fresh level + setup: debris is back
    assert again["status"] == "failed"
    assert ue._state["assets"]  # earlier levels' maps exist; reset never touches other roots
    bad = bench.Task(id="x", domain="d", title="t", prompt="p", check={}, solution=[])
    r = editor["execute_python"](code=bench.reset_code(bad, root="/Game/NotBench"))
    assert r["success"] is False and "UEBench" in r["traceback"]


def test_cmd_agent_and_scoreboard(editor, tmp_path):
    task = next(t for t in TASKS if t.id == "scene-cleanup")
    cmd = f"{sys.executable} -c \"import sys; print(open(sys.argv[1]).read()[:20])\" {{prompt_file}}"
    r = bench.run_task(editor, task, agent="cmd", agent_cmd=cmd, work_dir=tmp_path)
    assert r["agent"]["ok"] and r["status"] == "failed"
    assert "Delete all of them" in (tmp_path / task.id / "prompt.txt").read_text()
    results = [r, {"id": "y", "domain": "scene", "title": "", "status": "passed"},
               {"id": "z", "domain": "modeling", "title": "", "status": "skipped", "reason": "blender not installed"}]
    s = bench.summarize(results)
    assert (s["passed"], s["scored"], s["skipped"], s["pass_rate"]) == (1, 2, 1, 0.5)
    md = bench.scoreboard_markdown(s, results, "demo")
    assert "**1/2 passed** (50%)" in md and "| modeling | 0 | 0 |" in md and "blender not installed" in md


def test_task_file_validation(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"id": "b", "domain": "d", "title": "t", "prompt": "p",
                                                 "check": {}, "solution": []}))
    with pytest.raises(ValueError, match="must match"):
        bench.load_tasks(tmp_path)
    assert bench.substitute({"k": ["{root}/x", 3]}, {"root": "/Game/UEBench/t"}) == {"k": ["/Game/UEBench/t/x", 3]}


def test_cli_list(capsys):
    assert bench.main(["list"]) == 0
    assert "scene-greybox-arena" in capsys.readouterr().out


MUTATIONS = {  # near-miss solutions the checkers must reject
    "scene-raise-crates": lambda t: t.solution[0]["args"]["operations"][2].update(location=[600, 200, 40]),
    "scene-light-ring": lambda t: t.solution[0]["args"]["operations"][0]["pattern"].update(radius=500),
    "input-wasd": lambda t: t.solution[0]["args"]["contexts"][0]["mappings"].pop(),
    "gameplay-playable-start": lambda t: t.solution[0]["args"]["operations"].pop(0),  # no floor
    "material-emissive": lambda t: t.solution[0]["args"]["operations"].pop(5),  # emissive unwired
    "scene-pillar-grid": lambda t: t.solution[0]["args"]["operations"][0].update(folder="Columns"),
    "perf-shadow-budget": lambda t: t.solution[0]["args"]["operations"].pop(),
    "anim-blendspace-locomotion": lambda t: t.solution[0]["args"]["operations"][0]["samples"][2].update(x=400),
    "gas-heal-effect": lambda t: t.solution[0]["args"]["operations"][0]["modifiers"][0].update(magnitude=20),
    "gen3d-barrel-prop": lambda t: t.solution[0]["args"].update(height_cm=120),
    "gen3d-character-walk": lambda t: t.solution[1]["args"].update(
        animations=["/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle"]),  # idles instead of walking
}


@pytest.mark.parametrize("task_id", sorted(MUTATIONS))
def test_near_miss_solutions_fail(editor, task_id):
    import copy
    task = copy.deepcopy(next(t for t in TASKS if t.id == task_id))
    MUTATIONS[task_id](task)
    r = bench.run_task(editor, task, agent="solution")
    assert r["status"] == "failed" and r["failures"]
