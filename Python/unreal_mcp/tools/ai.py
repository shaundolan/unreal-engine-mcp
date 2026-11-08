"""AI: Blackboards and Behavior Trees from a nested spec."""

from __future__ import annotations

from typing import Any

from . import call


def register(mcp, get_conn):
    @mcp.tool()
    def bt_export(tree: str) -> dict[str, Any]:
        """Read a Behavior Tree (or a Blackboard asset): blackboard keys (name, type, base_class)
        and the node tree (type, name, properties incl. blackboard key names, decorators,
        services, children)."""
        return call(get_conn, "bt_export", {"tree": tree})

    @mcp.tool()
    def bt_build(tree: str = "", blackboard: str = "", keys: list[dict[str, Any]] | None = None,
                 root: dict[str, Any] | None = None, save: bool = True) -> dict[str, Any]:
        """Create/update a Blackboard and build a Behavior Tree in one call. The whole tree is
        validated first; on any error nothing changes and errors lists every problem.

        keys (upserted): [{"name":"PatrolLocation","type":"vector"},
                          {"name":"Target","type":"object","base_class":"Actor"},
                          {"name":"HasLineOfSight","type":"bool"}]
            types: vector rotator bool int float name string object class enum
        root (replaces the tree; composites: Selector, Sequence, SimpleParallel):
          {"type":"Selector","children":[
             {"type":"Sequence","name":"Chase",
              "decorators":[{"type":"Blackboard","properties":{"BlackboardKey":"Target"}}],
              "children":[{"type":"MoveTo","properties":{"BlackboardKey":"Target","AcceptableRadius":80}}]},
             {"type":"Sequence","name":"Patrol","children":[
                {"type":"/Game/AI/BTT_PickPatrolPoint.BTT_PickPatrolPoint_C"},
                {"type":"MoveTo","properties":{"BlackboardKey":"PatrolLocation"}},
                {"type":"Wait","properties":{"WaitTime":2.0}}]}]}
        Node types accept short names (MoveTo, Wait, RotateToFaceBBEntry, RunBehavior, Blackboard,
        Cooldown, Loop, TimeLimit, DefaultFocus, ...), full class names, or Blueprint task classes.
        Blackboard key properties take the key name. Run it from an AIController Blueprint:
        bp_apply event BeginPlay -> call_function RunBehaviorTree (BTAsset pin = this tree)."""
        r = call(get_conn, "bt_build", {"tree": tree, "blackboard": blackboard, "keys": keys,
                                        "root": root, "save": save}, timeout=120)
        if r.get("success") and "ok" in r:
            r["success"] = bool(r.pop("ok"))
            if not r["success"]:
                r["error"] = "; ".join(r.get("errors", [])[:8]) or "bt_build failed"
        return r
