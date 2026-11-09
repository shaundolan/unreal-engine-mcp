"""Blueprint v2: export a Blueprint as a JSON graph spec; apply batched edits atomically.

bp_apply is the one-call way to build gameplay logic: variables, components, functions, nodes,
pin defaults and links, compiled at the end with structured errors. With atomic=true (default) a
failed op or a compile error rolls the whole batch back, so the Blueprint is never left half-edited.
"""

from __future__ import annotations

from typing import Any

from . import call

_LARGE = 300.0


def _finish(r: dict[str, Any]) -> dict[str, Any]:
    if r.get("success") and "ok" in r:
        r["success"] = bool(r.pop("ok"))
        if not r["success"] and not r.get("error"):
            errs = [f"op {f['index']} ({f['op']}): {f['error']}" for f in r.get("failed", [])]
            errs += [f"compile: {e}" for e in (r.get("compile") or {}).get("errors", [])]
            errs += [f"default: {e}" for e in r.get("default_errors", [])]
            r["error"] = "; ".join(errs[:8]) or "bp_apply failed"
    return r


def register(mcp, get_conn):
    @mcp.tool()
    def bp_export(blueprint: str, graphs: list[str] | None = None, include_pins: bool = True) -> dict[str, Any]:
        """Read a Blueprint as a JSON spec: parent class, compile status, variables (type, value),
        components (class, parent), and every graph's nodes with kind-specific fields, position,
        pins (name, dir, type, default) and links ("<node id>.<pin>"). Node ids are stable GUIDs
        usable in bp_apply connect/set_pins/remove_node. graphs: limit to e.g. ["EventGraph"].
        Works for Actor, Character, GameMode, AIController, widget and BT-task Blueprints."""
        return call(get_conn, "bp_export", {"blueprint": blueprint, "graphs": graphs or [],
                                            "include_pins": include_pins})

    @mcp.tool()
    def bp_apply(blueprint: str, operations: list[dict[str, Any]], create_parent: str = "",
                 compile: bool = True, atomic: bool = True, dry_run: bool = False,
                 save: bool = True) -> dict[str, Any]:
        """Edit (or create, with create_parent) a Blueprint in ONE undoable step, then compile.

        create_parent: Actor, Character, Pawn, GameModeBase, PlayerController, AIController,
        BTTask_BlueprintBase, ActorComponent, ... (widgets: widget_apply). Ops run in order:
          {"op":"add_variable","name":"Score","type":"int","default":0,"instance_editable":true}
              types: bool int float string name text vector rotator transform linearcolor
                     object:<Class> class:<Class> struct:<Struct> enum:<Enum>, suffix [] for arrays
          {"op":"add_component","name":"Trigger","class":"SphereComponent","as_root":true,
           "properties":{"SphereRadius":80}}
          {"op":"add_component","name":"Mesh","class":"StaticMeshComponent","parent":"Trigger",
           "properties":{"StaticMesh":"/Engine/BasicShapes/Sphere.Sphere","RelativeScale3D":[0.5,0.5,0.5]}}
          {"op":"set_component","name":"Mesh","properties":{...}}
          {"op":"add_function","name":"AddScore","inputs":[{"name":"Amount","type":"int"}],
           "outputs":[{"name":"NewScore","type":"int"}], "pure":false}
              -> its nodes are "AddScore_entry" / "AddScore_result"; add nodes with "graph":"AddScore"
          {"op":"add_node","id":"begin","kind":"event","event":"BeginPlay"}
          {"op":"add_node","id":"hit","kind":"component_event","component":"Trigger",
           "event":"OnComponentBeginOverlap"}
          {"op":"add_node","id":"evt","kind":"custom_event","name":"OnCollected",
           "inputs":[{"name":"By","type":"object:Actor"}]}
          {"op":"add_node","id":"print","kind":"call_function","function":"PrintString",
           "pins":{"InString":"Hello"}}           (class optional: KismetSystemLibrary, GameplayStatics,
                                                    Actor, self, ...; K2_ prefixes are tried automatically)
          {"op":"add_node","id":"get","kind":"get_var","variable":"Score"}   (set_var likewise; inherited
                                                    variables too, "class" may name the Blueprint's parent)
          {"op":"add_node","id":"use","kind":"key_event","key":"E"}   ("pressed"/"released" exec outputs,
                                                    "Key" output; optional ctrl/shift/alt, consume_input)
          {"op":"add_node","id":"br","kind":"branch"}     ("then"/"else" outputs, "Condition" input)
          {"op":"add_node","id":"seq","kind":"sequence","outputs":3}
          {"op":"add_node","id":"cast","kind":"cast","class":"Character"}
          {"op":"add_node","kind":"self"} / {"kind":"macro","macro":"DoOnce"} / {"kind":"spawn_actor"}
          {"op":"add_node","kind":"node","class":"K2Node_CreateWidget","pins":{"Class":"/Game/UI/WBP_HUD.WBP_HUD_C"}}
          {"op":"connect","from":"begin.then","to":"print.exec"}      (pins by name or label;
                                                                       exec/then/else/return aliases)
          {"op":"set_pins","node":"print","pins":{"Duration":5}}
          {"op":"disconnect","from":"begin.then"}   {"op":"remove_node","node":"<id>"}
          {"op":"remove_variable","name":"Old"}
          {"op":"set_defaults","properties":{"AutoPossessAI":"PlacedInWorldOrSpawned",
           "CharacterMovement.MaxWalkSpeed":300}}   (class defaults, applied after compiling)
        "graph" on add_node picks the graph (default EventGraph). "x","y" place nodes (else auto-layout).
        Existing nodes are referenced by the ids from bp_export. Returns success, failed ops with
        reasons (unknown pins list the available ones), ids (alias -> node id), compile status/errors.
        dry_run=true applies, compiles and reports, then undoes everything.
        """
        return _finish(call(get_conn, "bp_apply", {
            "blueprint": blueprint, "operations": operations, "create_parent": create_parent,
            "compile": compile, "atomic": atomic, "dry_run": dry_run, "save": save}, timeout=_LARGE))
