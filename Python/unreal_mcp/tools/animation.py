"""Animation: sequences, montages, BlendSpaces, IK retargeting (anim_inspect / anim_edit) and Animation
Blueprints (animbp_export / animbp_apply: state machines, states, transitions, event graph).

Python reaches sequence settings, curves, BlendSpaces and IK Rig/Retargeter assets. Montage sections and
named notifies, and everything in an AnimGraph, are C++ (anim_asset_apply, animbp_apply)."""

from __future__ import annotations

from typing import Any

from . import call
from .blueprints import _LARGE, _finish
from .python_exec import run_editor_script

PY_OPS = {
    "set_sequence",
    "add_curve",
    "create_blendspace",
    "set_blendspace",
    "create_ik_rig",
    "create_retargeter",
    "retarget",
}
CPP_OPS = {"create_montage", "edit_montage", "add_notify", "remove_notifies"}


def montage_ops(op: dict[str, Any]) -> list[dict[str, Any]]:
    """create_montage spec -> anim_asset_apply operations."""
    out: list[dict[str, Any]] = []
    slot = op.get("slot") or "DefaultGroup.DefaultSlot"
    segments = list(op.get("segments") or [])
    segments += [{"sequence": s} for s in op.get("sequences") or []]
    for seg in segments:
        out.append(
            {"op": "add_segment", "slot": seg.get("slot", slot), **{k: v for k, v in seg.items() if k != "slot"}}
        )
    for extra in op.get("slots") or []:
        out.append({"op": "add_slot", "slot": extra})
    sections = op.get("sections") or []
    for s in sections:
        out.append({"op": "add_section", **s})
    if (
        sections
        and not any(s.get("name") == "Default" for s in sections)
        and any(float(s.get("time", 0)) == 0 for s in sections)
    ):
        out.append({"op": "remove_section", "name": "Default"})  # the factory's starting section is replaced
    for n in op.get("notifies") or []:
        out.append({"op": "add_notify", **n})
    blend = {k: op[k] for k in ("blend_in", "blend_out", "blend_out_trigger_time") if k in op}
    if blend:
        out.append({"op": "set_blend", **blend})
    return out


def register(mcp, get_conn):
    def asset_apply(
        asset: str, operations: list[dict[str, Any]], create: str = "", skeleton: str = ""
    ) -> dict[str, Any]:
        r = call(
            get_conn,
            "anim_asset_apply",
            {
                "asset": asset,
                "operations": operations,
                "create": create,
                "skeleton": skeleton,
                "save": True,
                "atomic": True,
            },
            timeout=_LARGE,
        )
        if r.get("success") and "ok" in r:
            r["success"] = bool(r.pop("ok"))
            if not r["success"]:
                r["error"] = "; ".join(f"op {f['index']} ({f['op']}): {f['error']}" for f in r.get("failed", [])[:8])
        return r

    @mcp.tool()
    def anim_inspect(
        asset: str = "", folder: str = "", skeleton: str = "", class_filter: str = "", limit: int = 200
    ) -> dict[str, Any]:
        """Read animation assets.
        asset: one asset in detail. AnimSequence: length, frames, rate_scale, root_motion, root_lock,
          notifies (name, time, class, duration), curves. AnimMontage: also slots (segments), sections
          (name, time, next), blend in/out. BlendSpace(1D): axes (name, min, max, grid) and samples
          (animation, x, y). IKRigDefinition: mesh, retarget root, chains. IKRetargeter: source/target rigs.
          Skeleton: curve names. AnimBlueprint: use animbp_export.
        folder (default /Game): list animation assets (class, path), optionally only those using
          `skeleton` and of class_filter (AnimSequence, AnimMontage, BlendSpace, BlendSpace1D,
          AnimBlueprint, Skeleton, SkeletalMesh, IKRigDefinition, IKRetargeter).
        Template Mannequin content lives at /Game/Characters/Mannequins (Meshes/SK_Mannequin skeleton,
        Meshes/SKM_Manny_Simple, Anims/Unarmed/...)."""
        r = run_editor_script(
            get_conn,
            "anim_inspect",
            {"asset": asset, "folder": folder, "skeleton": skeleton, "class_filter": class_filter, "limit": limit},
        )
        if r.get("success") and asset and r.get("class") == "AnimMontage":
            m = call(get_conn, "anim_asset_export", {"asset": asset})
            if m.get("success"):  # C++ adds section times/links and blend settings
                r.update({k: m[k] for k in ("sections", "slots", "blend_in", "blend_out") if k in m})
        return r

    @mcp.tool()
    def anim_edit(operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Edit animation assets; ops run in order, each its own undo step.
        Sequences:
          {"op":"set_sequence","asset":"/Game/A/MM_Run","rate_scale":1.2,"root_motion":true,
           "root_lock":"ref_pose|anim_first_frame|zero","force_root_lock":false,"interpolation":"linear|step"}
          {"op":"add_curve","asset":...,"name":"FootPlant","keys":[[0,0],[0.5,1]]}
        Notifies (sequences and montages; C++):
          {"op":"add_notify","asset":...,"name":"Hit","time":0.35}   named skeleton notify -> the AnimBP gets
               an "AnimNotify_Hit" event; "class":"AnimNotify_PlaySound"|"/Game/AN_X.AN_X_C" for notify objects
               ("AnimNotify_PlayMontageNotify" + "name" fires PlayMontage's OnNotifyBegin), a notify-state class
               + "duration" for states; "track":"Combat", "properties":{...}
          {"op":"remove_notifies","asset":...,"name":"Hit"}
        Montages (C++):
          {"op":"create_montage","asset":"/Game/A/AM_Attack","sequences":["/Game/A/MM_Attack_01"],
           "slot":"DefaultGroup.DefaultSlot","slots":["DefaultGroup.UpperBody"],
           "sections":[{"name":"Windup","time":0},{"name":"Strike","time":0.4,"next":"Recover"},
                       {"name":"Recover","time":0.9}],
           "notifies":[{"name":"Hit","time":0.45}],"blend_in":0.2,"blend_out":0.25}
              segments instead of sequences: [{"sequence":...,"slot":...,"play_rate":1,"start":0,"end":0.8,
              "loop_count":1}]; skeleton defaults to the first sequence's. A section at time 0 replaces "Default".
          {"op":"edit_montage","asset":...,"operations":[{"op":"add_section","name":"Loop","time":1.2},
              {"op":"link_section","from":"Loop","to":"Loop"},{"op":"remove_section","name":"Default"},
              {"op":"add_segment","sequence":...,"slot":...},{"op":"set_blend","blend_in":0.1}]}
        BlendSpaces:
          {"op":"create_blendspace","asset":"/Game/A/BS_Locomotion","skeleton":"<Skeleton or SkeletalMesh>",
           "dimensions":1,"axes":[{"name":"Speed","min":0,"max":600,"grid":4}],
           "samples":[{"animation":".../MM_Idle","x":0},{"animation":".../MF_Unarmed_Walk_Fwd","x":200},
                      {"animation":".../MF_Unarmed_Jog_Fwd","x":500}]}   2D: two axes, samples with x and y
          {"op":"set_blendspace","asset":...,"axes":[...],"samples":[...],"replace_samples":true}
        Retargeting (IK Rig plugin):
          {"op":"create_ik_rig","asset":"/Game/R/IK_Manny","mesh":".../SKM_Manny_Simple","auto":true}
               auto: chains + retarget root from the skeleton; "chains":[{"name","start","end","goal"}]
          {"op":"create_retargeter","asset":"/Game/R/RTG_A_to_B","source_rig":...,"target_rig":...,"auto_map":true}
          {"op":"retarget","retargeter":...,"source_mesh":...,"target_mesh":...,"assets":[sequences/montages/
               blendspaces],"target_path":"/Game/R/Anims","suffix":"_B"}
        Returns success and results [{index, op, ok, error?, ...}]."""
        results: list[dict[str, Any]] = []
        batch: list[dict[str, Any]] = []

        def flush():
            if not batch:
                return
            r = run_editor_script(get_conn, "anim_edit", {"operations": list(batch)}, timeout_s=600)
            if r.get("success"):
                results.extend(r.get("results", []))
            else:
                results.extend(
                    {
                        "index": o["_index"],
                        "op": o.get("op"),
                        "ok": False,
                        "error": r.get("traceback") or r.get("error"),
                    }
                    for o in batch
                )
            batch.clear()

        for i, op in enumerate(operations):
            kind = op.get("op")
            if kind in PY_OPS:
                batch.append({**op, "_index": i})
                continue
            flush()
            rec: dict[str, Any] = {"index": i, "op": kind}
            if kind == "create_montage":
                r = asset_apply(op.get("asset", ""), montage_ops(op), create="montage", skeleton=op.get("skeleton", ""))
            elif kind == "edit_montage":
                r = asset_apply(op.get("asset", ""), op.get("operations") or [])
            elif kind in ("add_notify", "remove_notifies"):
                r = asset_apply(op.get("asset", ""), [{k: v for k, v in op.items() if k != "asset"}])
            else:
                r = {"success": False, "error": f"unknown op {kind!r} (ops: {sorted(PY_OPS | CPP_OPS)})"}
            rec["ok"] = bool(r.pop("success", False))
            rec.update(r)
            results.append(rec)
        flush()
        results.sort(key=lambda x: x["index"])
        failed = [x for x in results if not x.get("ok")]
        out: dict[str, Any] = {"success": not failed, "results": results}
        if failed:
            out["error"] = "; ".join(f"op {x['index']} ({x['op']}): {str(x.get('error'))[-300:]}" for x in failed[:6])
        return out

    @mcp.tool()
    def animbp_export(blueprint: str, include_pins: bool = False) -> dict[str, Any]:
        """Read an Animation Blueprint: skeleton, preview mesh, variables, status, the event graph,
        anim_graph nodes (kind sequence_player/blendspace_player/state_machine/slot/output, asset, slot,
        inputs = what feeds each pin), output_chain (what reaches Output Pose, e.g.
        ["slot", "state_machine:Locomotion"]) and state_machines: name, entry state, states (name,
        assets played, pose_wired, nodes) and transitions (from, to, blend, priority, automatic,
        reads = variables the rule reads, calls = functions it calls, rule_wired, rule_nodes).
        Node ids work in animbp_apply connect/set_pins/remove_node."""
        return call(get_conn, "animbp_export", {"blueprint": blueprint, "include_pins": include_pins})

    @mcp.tool()
    def animbp_apply(
        blueprint: str,
        operations: list[dict[str, Any]],
        skeleton: str = "",
        parent_class: str = "",
        preview_mesh: str = "",
        compile: bool = True,
        atomic: bool = True,
        dry_run: bool = False,
        save: bool = True,
    ) -> dict[str, Any]:
        """Create (pass skeleton: a Skeleton or SkeletalMesh path) or edit an Animation Blueprint in ONE undoable
        step, then compile. Every bp_apply op works (add_variable, add_node, connect, set_pins, set_defaults, ...;
        the event graph is "EventGraph"), plus AnimGraph ops:
          {"op":"add_state_machine","id":"loco","name":"Locomotion"}            (graph: AnimGraph by default)
          {"op":"add_state","id":"idle","machine":"loco","name":"Idle","sequence":".../MM_Idle","loop":true}
          {"op":"add_state","id":"move","machine":"loco","name":"Move","blendspace":".../BS_Move",
           "inputs":{"X":"Speed"}}                       first state (or "entry":true) is the entry state
          {"op":"add_transition","id":"idle_move","from":"idle","to":"move","blend":0.2,
           "rule":{"var":"Speed","cmp":">","value":10}}
              rules: {"var":"IsInAir"} (bool) | {"var":"IsInAir","not":true} | {"var":"Speed","cmp":"<=","value":5}
                     | {"all":[...]} | {"any":[...]};  "automatic":true = leave when the state's animation ends
              hand-built rules: add_node with "graph":"idle_move", connect to "idle_move_result.bCanEnterTransition"
          {"op":"add_node","id":"slot","kind":"slot","slot":"DefaultSlot"}       (montages play through it)
          {"op":"add_node","id":"p","kind":"sequence_player"|"blendspace_player","graph":"<state id>",...}
          {"op":"connect","from":"loco.Pose","to":"slot.Source"},
          {"op":"connect","from":"slot.Pose","to":"output.Result"}
              "output" is the Output Pose node; "<state id>_result" a state's result ("Result" pin);
              states/machines can also be referenced by name ("Idle" or "Locomotion/Idle")
          {"op":"set_entry","state":"idle"}  {"op":"set_properties","node":"idle_move","properties":{"PriorityOrder":1}}
        Event graph (owner pawn -> variables), e.g.:
          {"op":"add_variable","name":"Speed","type":"float"}, {"op":"add_variable","name":"IsInAir","type":"bool"},
          {"op":"add_node","id":"upd","kind":"event","event":"BlueprintUpdateAnimation"},
          {"op":"add_node","id":"pawn","kind":"call_function","function":"TryGetPawnOwner"},
          {"op":"add_node","id":"vel","kind":"call_function","function":"GetVelocity","class":"Actor"},
          {"op":"add_node","id":"len","kind":"call_function","function":"VSize","class":"KismetMathLibrary"},
          {"op":"add_node","id":"setspeed","kind":"set_var","variable":"Speed"}, connect upd.then->setspeed.exec,
          pawn.ReturnValue->vel.self, vel.ReturnValue->len.A, len.ReturnValue->setspeed.Speed; IsInAir: cast to
          Character, GetCharacterMovement, IsFalling (class CharacterMovementComponent) -> set_var IsInAir.
        Returns success, failed ops, ids (alias -> node id), compile status/errors; atomic rollback as bp_apply."""
        return _finish(
            call(
                get_conn,
                "animbp_apply",
                {
                    "blueprint": blueprint,
                    "operations": operations,
                    "skeleton": skeleton,
                    "parent_class": parent_class,
                    "preview_mesh": preview_mesh,
                    "compile": compile,
                    "atomic": atomic,
                    "dry_run": dry_run,
                    "save": save,
                },
                timeout=_LARGE,
            )
        )
