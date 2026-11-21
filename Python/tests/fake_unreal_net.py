"""Multi-client PIE for the fake `unreal` module: several PIE worlds (server, client:1 ...), each with its own actors,
and the MCPScriptLibrary helpers the editor scripts use to reach them (GetPieWorld, FindReplica, GetNetId)."""

from __future__ import annotations

from typing import Any


def add_net(u) -> None:
    state = u._state
    state.setdefault("net_worlds", {})  # target -> World (with _actors, _player)
    stats = u.GameplayStatics
    single_actors, single_pawn = stats.get_all_actors_of_class, stats.get_player_pawn

    def get_all_actors_of_class(w, cls):
        if hasattr(w, "_actors"):
            return [a for a in w._actors if isinstance(a, cls)]
        return single_actors(w, cls)

    def get_player_pawn(w, index):
        if hasattr(w, "_actors"):
            return getattr(w, "_player", None)
        return single_pawn(w, index)

    stats.get_all_actors_of_class = staticmethod(get_all_actors_of_class)
    stats.get_player_pawn = staticmethod(get_player_pawn)

    class MCPScriptLibrary:
        @staticmethod
        def get_pie_worlds():
            return list(state["net_worlds"].values())

        @staticmethod
        def get_pie_world(target):
            t = str(target).lower()
            return state["net_worlds"].get("client:1" if t == "client" else t)

        @staticmethod
        def get_pie_world_target(w):
            return next((t for t, x in state["net_worlds"].items() if x is w), "")

        @staticmethod
        def get_net_id(actor):
            return getattr(actor, "_net_id", "")

        @staticmethod
        def find_replica(actor, w):
            if actor in w._actors:
                return actor
            nid = getattr(actor, "_net_id", "")
            for a in w._actors:
                if type(a) is type(actor) and ((nid and getattr(a, "_net_id", "") == nid)
                                               or (not nid and a.get_name() == actor.get_name())):
                    return a
            return None

    u.MCPScriptLibrary = MCPScriptLibrary


def net_world(u, target: str):
    w = u.World(f"UEDPIE_{len(u._state['net_worlds'])}_Map", "/Game/Map")
    w._actors, w._player = [], None
    u._state["net_worlds"][target] = w
    return w


def place(u, w, cls, label: str, loc=(0, 0, 0), net_id: str = "", **props: Any):
    """An actor in one PIE world; the same net_id in another world makes it that actor's replica."""
    a = cls(u.Vector(*loc), u.Rotator())
    a.set_actor_label(label)
    a._net_id = net_id
    for k, v in props.items():
        setattr(a, k, v)
    w._actors.append(a)
    return a
