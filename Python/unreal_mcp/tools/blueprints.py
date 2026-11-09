"""Blueprint v2: export a Blueprint as a JSON graph spec; apply batched edits atomically.

bp_apply is the one-call way to build gameplay logic: variables, components, functions, nodes,
pin defaults and links, compiled at the end with structured errors. With atomic=true (default) a
failed op or a compile error rolls the whole batch back, so the Blueprint is never left half-edited.
"""

from __future__ import annotations

from typing import Any

from .. import bp_analysis
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
        Interfaces, dispatchers, timelines, macros, local variables (see the blueprint-authoring skill):
          create_parent="Interface" makes a Blueprint Interface: add_function ops declare its functions
              (no outputs = an event in implementers). create_parent="MacroLibrary" makes a macro library.
          {"op":"implement_interface","interface":"/Game/BPI_Interact"}  then {"kind":"event","event":"Interact"}
              (functions with outputs get a graph named after them: add nodes with "graph":"<Function>")
          {"op":"add_node","kind":"interface_message","interface":"/Game/BPI_Interact","function":"Interact"}
              (call on any object; "interface_call" for an interface-typed target)
          {"op":"add_dispatcher","name":"OnOpened","inputs":[{"name":"By","type":"object:Actor"}]}
          {"kind":"call_dispatcher","dispatcher":"OnOpened"} / bind_dispatcher / unbind_dispatcher /
              clear_dispatcher, "class":"/Game/BP_Door" for another Blueprint's (wire its Target pin);
              bind_dispatcher "event":"<custom_event id>" wires the handler; make the handler with
              {"kind":"custom_event","name":"HandleOpened","dispatcher":"OnOpened","class":"/Game/BP_Door"}
              (same parameters). {"kind":"create_delegate","function":"Name"} for a function delegate.
          {"kind":"timeline","name":"Lift","length":2,"loop":false,"autoplay":false,"tracks":[
              {"name":"Alpha","type":"float","keys":[[0,0],[2,1,"cubic"]]},
              {"name":"Offset","type":"vector","keys":[[0,[0,0,0]],[2,[0,0,300]]]},
              {"name":"Tint","type":"color","keys":[[0,[1,0,0,1]]]}, {"name":"Ding","type":"event","keys":[1.0]}]}
              pins: Play, PlayFromStart, Stop, Reverse, ReverseFromEnd, SetNewTime / Update, Finished, Direction,
              one output per track
          {"op":"add_local_variable","function":"Calc","name":"Tmp","type":"float","default":0}
              (get_var/set_var in that function find it)
          {"op":"add_function","name":"Area","pure":true,...}   {"op":"add_macro","name":"Clamp01",
              "inputs":[{"name":"In","type":"exec"},{"name":"V","type":"float"}],"outputs":[...]}
              -> nodes "<Name>_entry" / "<Name>_exit"; use it with {"kind":"macro","macro":"Clamp01"}
              ("library":"/Game/ML_Utils" for a macro library's)
          {"kind":"operator","op":"+","type":"float"} (+ - * / > >= < <= == !=: the 5.x promotable operator,
              typed by "type" or by what you connect)
          {"kind":"select","options":3,"index_type":"int"|"bool"|"enum:/Game/E_Rarity","type":"float"}
          {"kind":"switch","on":"int"|"string"|"name"|"enum:/Game/E_Rarity","cases":["A","B"]}  (int: start/count)
          {"kind":"make_struct","struct":"/Game/S_Item"} / {"kind":"break_struct",...}
          {"kind":"format_text","format":"{Name} has {HP} HP"}   (an input pin per {argument})
          {"kind":"get_data_table_row","table":"/Game/DT_Items","row":"Sword"}  (Out Row typed to the row struct)
          {"kind":"async_action","function":"<factory>","class":"<AsyncAction class>"}, {"kind":"ai_move_to"}
        Enum pins take entry names or display names ("Rare").
        "graph" on add_node picks the graph (default EventGraph). "x","y" place nodes (else auto-layout).
        Existing nodes are referenced by the ids from bp_export. Returns success, failed ops with
        reasons (unknown pins list the available ones), ids (alias -> node id), compile status/errors.
        dry_run=true applies, compiles and reports, then undoes everything.
        """
        return _finish(call(get_conn, "bp_apply", {
            "blueprint": blueprint, "operations": operations, "create_parent": create_parent,
            "compile": compile, "atomic": atomic, "dry_run": dry_run, "save": save}, timeout=_LARGE))

    @mcp.tool()
    def bp_explain(blueprint: str) -> dict[str, Any]:
        """Understand a Blueprint quickly: entry points (events, custom/component/key events, functions) with
        the execution flow of each ("BeginPlay -> GetPlayerPawn -> Cast to BP_Hero -> ..."), variables read
        and written per entry point, external calls by class, components, interfaces, dispatchers,
        timelines, and a dependency graph (parent, components, called classes, casts, assets). Returns JSON
        plus `markdown`, a short human summary. Read-only."""
        spec = call(get_conn, "bp_export", {"blueprint": blueprint, "graphs": [], "include_pins": True}, timeout=_LARGE)
        if not spec.get("success"):
            return spec
        return {"success": True, **bp_analysis.explain(spec)}

    @mcp.tool()
    def bp_review(blueprint: str, tick_budget: int = bp_analysis.TICK_NODE_BUDGET) -> dict[str, Any]:
        """Lint a Blueprint. Findings (rule, severity error|warning|info, graph, node id, message):
        compile_error, unwired_exec (an impure node nothing executes), dead_node, unused_variable,
        write_only_variable, tick_heavy (more than tick_budget nodes per frame), cast_in_tick,
        expensive_in_tick (GetAllActorsOfClass, spawning, printing...), latent_in_tick, missing_is_valid
        (an object variable used as a call target with no IsValid check), infinite_loop_risk (exec cycles
        without a Delay, While loops, self-recursive events). Also returns the bp_explain summary and
        `markdown`. ok=true when there are no errors or warnings."""
        spec = call(get_conn, "bp_export", {"blueprint": blueprint, "graphs": [], "include_pins": True}, timeout=_LARGE)
        if not spec.get("success"):
            return spec
        return {"success": True, **bp_analysis.review(spec, tick_budget=tick_budget)}
