---
name: animation
description: >
  Animation work: inspect and edit sequences (rate scale, root motion, curves, notifies), build montages
  (segments, slots, sections, Hit notifies), 1D/2D BlendSpaces, IK Rig / IK Retargeter retargeting, and
  Animation Blueprints with animbp_apply (state machines, states, transitions with rules, slot, event graph
  that reads the owning pawn). Use for locomotion, attacks, character setup.
---

# Animation

Tools: `anim_inspect` (read), `anim_edit` (asset edits), `animbp_export` / `animbp_apply` (Animation Blueprints).
Python reaches sequence settings, curves, BlendSpaces and IK assets; montages, notifies and the AnimGraph are C++
(`anim_asset_apply`, `animbp_apply`), so they need the 0.4 plugin build.

## Find content first
`anim_inspect(folder="/Game/Characters", class_filter="AnimSequence")` lists animations; `skeleton=` keeps only one
skeleton's. Every asset an animation uses must share the skeleton (`anim_inspect(asset=...)` shows `skeleton`).
Template Mannequin content (install it with `python scripts/install_mannequin.py` into the sample project, or start
from the ThirdPerson template) lives at `/Game/Characters/Mannequins`:
- skeleton `Meshes/SK_Mannequin`, meshes `Meshes/SKM_Manny_Simple`, `Meshes/SKM_Quinn_Simple`
- `Anims/Unarmed/MM_Idle`, `Walk/MF_Unarmed_Walk_Fwd`, `Jog/MF_Unarmed_Jog_Fwd` (and Left/Right/Bwd),
  `Jump/MM_Jump`, `Jump/MM_Fall_Loop`, `Jump/MM_Land`, `Attack/MM_Attack_01..03`, `BS_Idle_Walk_Run`, `ABP_Unarmed`

## Locomotion recipe (BlendSpace + AnimBP), two calls
```json
anim_edit(operations=[{"op":"create_blendspace","asset":"/Game/Anim/BS_Locomotion",
  "skeleton":"/Game/Characters/Mannequins/Meshes/SK_Mannequin","dimensions":1,
  "axes":[{"name":"Speed","min":0,"max":600,"grid":4}],
  "samples":[{"animation":".../Unarmed/MM_Idle","x":0},{"animation":".../Walk/MF_Unarmed_Walk_Fwd","x":200},
             {"animation":".../Jog/MF_Unarmed_Jog_Fwd","x":500}]}])

animbp_apply(blueprint="/Game/Anim/ABP_Hero", skeleton="/Game/Characters/Mannequins/Meshes/SK_Mannequin", operations=[
  {"op":"add_variable","name":"Speed","type":"float"}, {"op":"add_variable","name":"IsInAir","type":"bool"},
  // event graph: owner pawn -> Speed, IsInAir
  {"op":"add_node","id":"upd","kind":"event","event":"BlueprintUpdateAnimation"},
  {"op":"add_node","id":"pawn","kind":"call_function","function":"TryGetPawnOwner"},
  {"op":"add_node","id":"valid","kind":"macro","macro":"IsValid"},
  {"op":"add_node","id":"vel","kind":"call_function","function":"GetVelocity","class":"Actor"},
  {"op":"add_node","id":"len","kind":"call_function","function":"VSize","class":"KismetMathLibrary"},
  {"op":"add_node","id":"setspeed","kind":"set_var","variable":"Speed"},
  {"op":"add_node","id":"cast","kind":"cast","class":"Character","pure":true},
  {"op":"add_node","id":"cmove","kind":"get_var","variable":"CharacterMovement","class":"Character"},
  {"op":"add_node","id":"falling","kind":"call_function","function":"IsFalling","class":"CharacterMovementComponent"},
  {"op":"add_node","id":"setair","kind":"set_var","variable":"IsInAir"},
  {"op":"connect","from":"upd.then","to":"valid.exec"}, {"op":"connect","from":"pawn.ReturnValue","to":"valid.InputObject"},
  {"op":"connect","from":"valid.Is Valid","to":"setspeed.exec"}, {"op":"connect","from":"pawn.ReturnValue","to":"vel.self"},
  {"op":"connect","from":"vel.ReturnValue","to":"len.A"}, {"op":"connect","from":"len.ReturnValue","to":"setspeed.Speed"},
  {"op":"connect","from":"setspeed.then","to":"setair.exec"}, {"op":"connect","from":"pawn.ReturnValue","to":"cast.Object"},
  {"op":"connect","from":"cast.as","to":"cmove.self"}, {"op":"connect","from":"cmove.CharacterMovement","to":"falling.self"},
  {"op":"connect","from":"falling.ReturnValue","to":"setair.IsInAir"},
  // AnimGraph
  {"op":"add_state_machine","id":"loco","name":"Locomotion"},
  {"op":"add_state","id":"idle","machine":"loco","name":"Idle","sequence":".../Unarmed/MM_Idle","loop":true},
  {"op":"add_state","id":"move","machine":"loco","name":"Move","blendspace":"/Game/Anim/BS_Locomotion","inputs":{"X":"Speed"}},
  {"op":"add_state","id":"jump","machine":"loco","name":"Jump","sequence":".../Jump/MM_Fall_Loop"},
  {"op":"add_transition","from":"idle","to":"move","blend":0.2,"rule":{"var":"Speed","cmp":">","value":10}},
  {"op":"add_transition","from":"move","to":"idle","blend":0.2,"rule":{"var":"Speed","cmp":"<=","value":10}},
  {"op":"add_transition","from":"idle","to":"jump","rule":{"var":"IsInAir"}},
  {"op":"add_transition","from":"move","to":"jump","rule":{"var":"IsInAir"}},
  {"op":"add_transition","from":"jump","to":"idle","rule":{"var":"IsInAir","not":true}},
  {"op":"add_node","id":"slot","kind":"slot","slot":"DefaultSlot"},
  {"op":"connect","from":"loco.Pose","to":"slot.Source"}, {"op":"connect","from":"slot.Pose","to":"output.Result"}])
```
Then put it on a character: `bp_apply(..., operations=[{"op":"set_defaults","properties":{"Mesh.SkeletalMeshAsset":
".../SKM_Manny_Simple.SKM_Manny_Simple","Mesh.AnimClass":"/Game/Anim/ABP_Hero.ABP_Hero_C"}}])`.

Rules for `animbp_apply`:
- The first state of a machine is its entry (or `"entry":true`, or `set_entry`).
- `"output"` is the Output Pose node (pin `Result`). Each state gets `<id>_result` (pin `Result`) and, with
  `sequence`/`blendspace`, `<id>_player`. Each transition gets `<id>_result` (pin `bCanEnterTransition`).
- Rules: `{"var":"Speed","cmp":">","value":10}`, `{"var":"IsInAir"}`, `{"var":"IsInAir","not":true}`,
  `{"all":[...]}`, `{"any":[...]}`; `"automatic":true` leaves when the state's animation finishes (land → idle).
  Anything else: `add_node` with `"graph":"<transition id>"`, then connect into `<id>_result.bCanEnterTransition`.
- Rule variables must exist first (`add_variable` earlier in the same call is fine).
- States and machines can be named instead of ids in later calls: `"Idle"` or `"Locomotion/Idle"`.
- A failed op or compile error rolls the whole call back; a created AnimBP is deleted again.

Verify with `animbp_export`: `output_chain` (e.g. `["slot","state_machine:Locomotion"]`), `state_machines[].entry`,
`states[].assets`/`pose_wired`, `transitions[].reads`/`rule_wired`. In play, read the AnimBP's variables:
`pie_test` assertion `a.mesh.get_anim_instance().get_editor_property('Speed') > 10`.

## Montages
```json
anim_edit(operations=[{"op":"create_montage","asset":"/Game/Anim/AM_Attack","sequences":[".../Attack/MM_Attack_01"],
  "sections":[{"name":"Windup","time":0},{"name":"Strike","time":0.4},{"name":"Recover","time":0.9}],
  "notifies":[{"name":"Hit","time":0.45}],"blend_in":0.15}])
```
- Slots are named like the AnimBP slot node: `DefaultSlot` (`DefaultGroup.DefaultSlot` is accepted). Extra slots:
  `"slots":["UpperBody"]` or per segment `"slot"`. The AnimBP needs a slot node with that name to play it.
- Sections chain in time order; `{"op":"edit_montage","operations":[{"op":"link_section","from":"Loop","to":"Loop"}]}`
  loops one. A section at time 0 replaces the factory's `Default`.
- Notifies: `"name"` alone is a skeleton notify; an AnimBP reacts with an `AnimNotify_Hit` event, which bp_apply
  cannot create yet (add it in the editor, or use the class form). `"class":"AnimNotify_PlayMontageNotify","name":"Hit"`
  fires `Play Montage`'s `OnNotifyBegin` with `NotifyName == Hit`, the usual Blueprint/GAS pattern. Notify states
  take `"duration"`.
- Same `add_notify` / `remove_notifies` ops work on plain sequences (footsteps).

## Sequences
`{"op":"set_sequence","asset":...,"rate_scale":1.2,"root_motion":true,"root_lock":"ref_pose"}`;
`{"op":"add_curve","asset":...,"name":"FootPlant","keys":[[0,0],[0.4,1]]}`.
Template assets copied from the engine may be read-only on disk; `install_mannequin.py` copies them writable.
A save that fails is reported as an error ("could not save ...").

## Retargeting (IK Rig plugin, on by default)
```json
anim_edit(operations=[
  {"op":"create_ik_rig","asset":"/Game/Retarget/IK_Manny","mesh":".../Meshes/SKM_Manny_Simple"},
  {"op":"create_ik_rig","asset":"/Game/Retarget/IK_Target","mesh":"/Game/MyChar/SK_Target"},
  {"op":"create_retargeter","asset":"/Game/Retarget/RTG_Manny_Target","source_rig":"/Game/Retarget/IK_Manny",
   "target_rig":"/Game/Retarget/IK_Target"},
  {"op":"retarget","retargeter":"/Game/Retarget/RTG_Manny_Target","source_mesh":".../SKM_Manny_Simple",
   "target_mesh":"/Game/MyChar/SK_Target","assets":[".../MM_Idle",".../BS_Idle_Walk_Run"],"target_path":"/Game/MyChar/Anims"}])
```
`create_ik_rig` auto-generates chains and the retarget root from the skeleton (`anim_inspect` shows them; add or fix
with `"chains":[{"name","start","end"}]`). `create_retargeter` auto-maps chains by name (`"map_type":"exact|fuzzy"`).
Non-humanoid or oddly named skeletons need hand-made chains; check the result in the IK Retargeter editor.

## Gaps
- AnimGraph nodes beyond sequence/blendspace players, slot and state machines: use `add_node` `"kind":"node"` with an
  `AnimGraphNode_*` class (LayeredBoneBlend, TwoWayBlend, ...), then `set_properties` / connect. Untested beyond the
  kinds above.
- Linked anim layers, Control Rig nodes, pose search, montage time-stretch curves: not covered.
- Retarget pose editing (auto-align) is basic; verify visually.
