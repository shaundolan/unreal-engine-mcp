"""Multi-client PIE: net_mode / clients on pie_start, world targets on every step, replication assertions."""

import base64

import pytest
from fake_unreal_net import add_net, net_world, place
from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp.tools.testing import run_pie_test


@pytest.fixture
def net(fake, ue):  # noqa: F811
    """Fake plugin side of a listen server + 2 clients session (worlds live in the fake unreal module)."""
    add_net(ue)
    state = ue._state
    state.update(calls=[], connected=2, pie_worlds_calls=0)

    def record(cmd, reply=None):
        def handler(p):
            state["calls"].append((cmd, p))
            return reply(p) if reply else {}
        return handler

    def editor_action(p):
        state["calls"].append(("editor_action", p))
        if p["action"] == "pie_start":
            state["pie"] = True
            state["requested"] = p
        elif p["action"] == "pie_stop":
            state["pie"] = False
        return {"action": p["action"]}

    def pie_worlds(p):
        state["pie_worlds_calls"] += 1
        req = state.get("requested", {})
        n = int(req.get("clients", 1))
        c = state["connected"]
        connected = c() if callable(c) else min(c, n)
        worlds = [{"target": t, "net_mode": "listen_server" if t == "server" else "client"}
                  for t in state["net_worlds"]]
        return {"worlds": worlds, "net_mode": req.get("net_mode", "standalone"), "clients": n,
                "requested_clients": n, "clients_connected": connected,
                "server_players": 1 + connected, "all_connected": connected >= n}

    fake.on("project_context", lambda p: {"pie_running": bool(state.get("pie"))})
    fake.on("editor_action", editor_action)
    fake.on("editor_log", lambda p: {"entries": [], "last_seq": 0})
    fake.on("pie_worlds", pie_worlds)
    fake.on("pie_possess", record("pie_possess", lambda p: {"possessed": True, "world": "server", "for": p.get("world")}))
    fake.on("pie_input", record("pie_input", lambda p: {"key": p.get("key"), "mode": p["mode"], "world": p.get("world")}))
    fake.on("spawn_in_pie", record("spawn_in_pie", lambda p: {"name": "BP_Car_C_0", "world": p.get("world", "pie")}))
    png = base64.b64encode(b"\x89PNG fake").decode()
    fake.on("viewport_capture", record("viewport_capture", lambda p: {"data_base64": png, "width": 64, "height": 36}))

    server, c1, c2 = net_world(ue, "server"), net_world(ue, "client:1"), net_world(ue, "client:2")
    state["worlds"] = {"server": server, "client:1": c1, "client:2": c2}
    return state


def _cars(ue, net, on=("server", "client:1", "client:2"), x=(0, 0, 0)):  # noqa: F811
    return {t: place(ue, net["worlds"][t], ue.StaticMeshActor, "Car" if t == "server" else "BP_Car_C_0",
                     loc=x[t] if isinstance(x, dict) else x, net_id="17") for t in on}


def test_pie_start_net_mode_waits_for_clients_and_reports_worlds(net, ue, conn):  # noqa: F811
    joined = iter([0, 1, 2])
    net["connected"] = lambda: next(joined, 2)
    r = run_pie_test(lambda: conn, [{"type": "player_count"}, {"type": "clients_connected"}],
                     duration_s=0, sleep=lambda s: None, net_mode="listen_server", clients=2)
    assert r["success"] and r["passed"], r
    start = net["requested"]
    assert start["net_mode"] == "listen_server" and start["clients"] == 2
    assert [w["target"] for w in r["worlds"]] == ["server", "client:1", "client:2"]
    assert r["clients_connected"] == 2 and r["server_players"] == 3
    assert {x["type"]: x["actual"] for x in r["results"]} == {"player_count": 3, "clients_connected": 2}
    assert net["pie"] is False


def test_standalone_default_does_not_touch_net_settings(net, ue, conn):  # noqa: F811
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None)
    assert r["success"] and "net_mode" not in net["requested"] and "clients" not in net["requested"]
    assert net["pie_worlds_calls"] == 0 and "worlds" not in r


def test_clients_that_never_join_fail_the_run_and_stop_pie(net, ue, conn):  # noqa: F811
    net["connected"] = lambda: 1
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None, net_mode="client", clients=2,
                     start_timeout_s=1.0)
    assert r["success"] is False and "1 of 2 clients connected" in r["error"]
    assert net["pie"] is False


def test_client_one_drives_client_two_sees_the_car_move(net, ue, conn, tmp_path):  # noqa: F811
    cars = _cars(ue, net, on=("server", "client:1"))  # client 2 has not received the car yet

    def sleep(s):
        # time passes: the car reaches client 2, and client 1's driving moves it everywhere
        if s > 0 and "client:2" not in cars:
            cars["client:2"] = place(ue, net["worlds"]["client:2"], ue.StaticMeshActor, "BP_Car_C_0", net_id="17")
        for a in cars.values():
            a._loc = ue.Vector(a._loc.x + 100 * s, 0, 0)

    r = run_pie_test(lambda: conn, [
        {"type": "replicated_property", "target": "Car", "property": "location", "tolerance": 1},
    ], duration_s=0, sleep=sleep, net_mode="listen_server", clients=2, capture_dir=str(tmp_path), steps=[
        {"wait_clients": True},
        {"spawn": "/Game/BP_Car", "label": "Car", "world": "server", "possess_for": "client:1"},
        {"check": [{"type": "replicated", "label": "Car", "within_s": 5}]},
        {"possess": "Car", "world": "client:1"},
        {"hold": "W", "duration_s": 2, "world": "client:1"},
        {"check": [{"type": "python", "expr": "player is None and len(actors) == 1"}], "world": "client:2"},
        {"check": [{"type": "distance", "a": "BP_Car_C_0", "point": [0, 0, 0], "op": ">=", "value": 100,
                    "net_role": "client:2"}]},
        {"capture": "seen_by_client_2", "world": "client:2"},
    ])
    assert r["passed"], r["steps"]
    steps = r["steps"]
    rep = steps[2]["assertions"][0]
    assert rep["passed"] and rep["tries"] == 2 and set(rep["actual"]["replicas"]["Car"]) == {"client:1", "client:2"}
    assert "net_id" in rep["actual"]["replicas"]["Car"]["client:2"]
    calls = dict((c, p) for c, p in net["calls"] if c != "editor_action")
    assert calls["spawn_in_pie"]["world"] == "server" and calls["spawn_in_pie"]["possess_for"] == "client:1"
    assert calls["pie_possess"] == {"target": "Car", "player": 0, "destroy_previous": False, "world": "client:1"}
    assert calls["pie_input"] == {"key": "W", "mode": "hold", "duration_s": 2.0, "world": "client:1"}
    assert steps[4]["input"] == [{"key": "W", "mode": "hold", "world": "client:1"}]
    assert calls["viewport_capture"]["world"] == "client:2" and steps[7]["world"] == "client:2"
    assert (tmp_path / "seen_by_client_2.png").exists()
    assert steps[6]["assertions"][0]["world"] == "client:2" and steps[6]["assertions"][0]["actual"] >= 100
    final = r["results"][0]
    assert final["passed"] and set(final["actual"]) == {"server", "client:1", "client:2"}


def test_replicated_property_mismatch_fails_after_within_s(net, ue, conn):  # noqa: F811
    _cars(ue, net, x={"server": (500, 0, 0), "client:1": (500, 0, 0), "client:2": (0, 0, 0)})
    waits = []
    r = run_pie_test(lambda: conn, [
        {"name": "car synced", "type": "replicated_property", "target": "Car", "property": "location",
         "tolerance": 10, "within_s": 0.3},
        {"type": "replicated_property", "target": "Car", "property": "location", "clients": [1], "tolerance": 10},
        {"type": "replicated", "label": "Ghost", "within_s": 0.2},
    ], duration_s=0, sleep=waits.append, net_mode="listen_server", clients=2)
    assert r["success"] and r["passed"] is False
    bad, one, ghost = r["results"]
    assert bad["name"] == "car synced" and bad["passed"] is False and bad["tries"] >= 3
    assert bad["actual"] == {"server": [500.0, 0.0, 0.0], "client:1": [500.0, 0.0, 0.0], "client:2": [0.0, 0.0, 0.0]}
    assert one["passed"] and one["tries"] == 1 and set(one["actual"]) == {"server", "client:1"}
    assert ghost["passed"] is False and ghost["actual"]["on_server"] == 0  # nothing to replicate
    assert sum(waits) < 1.0


def test_world_targets_reach_the_editor_scripts(net, ue, conn):  # noqa: F811
    net["worlds"]["client:1"]._player = place(ue, net["worlds"]["client:1"], ue.Character, "Me", loc=(5, 0, 0))
    r = run_pie_test(lambda: conn, [
        {"type": "distance", "a": "@player", "point": [5, 0, 0], "op": "approx", "value": 0, "world": "client:1"},
        {"type": "python", "expr": "net_role == 'client:1' and pie_world('server') is not None", "world": "client:1"},
        {"type": "actor_exists", "label": "Me", "world": "server", "max": 0},
        {"type": "actor_exists", "label": "Me", "world": "client:7"},
    ], duration_s=0, sleep=lambda s: None, net_mode="listen_server", clients=2)
    assert r["success"] is False and "client:7" in str(r["detail"])  # an unknown world fails the script loudly

    r = run_pie_test(lambda: conn, [
        {"type": "distance", "a": "@player", "point": [5, 0, 0], "op": "approx", "value": 0, "world": "client:1"},
        {"type": "python", "expr": "net_role == 'client:1' and pie_world('server') is not None", "world": "client:1"},
        {"type": "actor_exists", "label": "Me", "world": "server", "min": 0, "max": 0},
    ], duration_s=0, sleep=lambda s: None, net_mode="listen_server", clients=2,
        steps=[{"do": "player.set_actor_location(vec([9, 0, 0]), False, False)", "world": "client:1"}])
    assert r["passed"] is False  # moved to 9 by the step
    assert [x["passed"] for x in r["results"]] == [False, True, True]
    assert r["results"][0]["actual"] == 4.0 and r["results"][0]["world"] == "client:1"


def test_wait_clients_step_times_out(net, ue, conn):  # noqa: F811
    net["connected"] = lambda: 1
    r = run_pie_test(lambda: conn, [], duration_s=0, sleep=lambda s: None, steps=[
        {"wait_clients": 2, "timeout_s": 0.5}, {"wait_s": 1}])
    assert r["passed"] is False and len(r["steps"]) == 1
    assert "1 of 2 clients connected" in r["steps"][0]["error"]


def test_net_tools_pass_world_through(net, fake, tools):
    net["requested"] = {"net_mode": "client", "clients": 2}
    r = tools["pie_worlds"]()
    assert r["success"] and r["all_connected"] and r["requested_clients"] == 2
    tools["pie_possess"]("Car", world="client:2")
    tools["pie_input"](key="W", mode="hold", world="client:2")
    tools["spawn_in_pie"]("/Game/BP_Car", label="Car", world="server", possess=True, possess_for="client:2")
    tools["pie_possess"]("Car")
    calls = [p for c, p in net["calls"] if c != "editor_action"]
    assert calls[0]["world"] == "client:2" and calls[1]["world"] == "client:2"
    assert calls[2]["world"] == "server" and calls[2]["possess_for"] == "client:2" and calls[2]["possess"] is True
    assert "world" not in calls[3]  # default: unchanged behaviour
