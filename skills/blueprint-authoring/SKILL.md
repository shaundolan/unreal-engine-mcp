---
name: blueprint-authoring
description: >
  Build and edit Blueprints with bp_export / bp_apply: variables, components, functions, event graphs,
  pin defaults and links in one atomic call, compiled with structured errors. Also widget trees
  (widget_apply) and Behavior Trees (bt_build). Use for any gameplay logic, UI or AI work.
---

# Blueprint authoring

The loop is **export → plan → `bp_apply` → read `compile` → fix → `pie_test`**. A `bp_apply` call is atomic: if any op fails or the
Blueprint doesn't compile, the whole batch is undone (`rolled_back: true`) and `failed` / `compile.errors` say why.
Use `dry_run: true` to try a batch without keeping it.

## Read first
`bp_export(blueprint)` returns variables (with current values), components, and every graph's nodes. Each node has a `kind`
with the same fields `add_node` takes, pins with types and defaults, and links as `"<node id>.<pin>"`. Use those node ids to
connect to existing nodes. Template events (BeginPlay, Tick, ActorBeginOverlap) start out `disabled`; adding the same event
re-enables that node instead of creating a duplicate.

## One call builds a feature
```json
bp_apply(blueprint="/Game/Game/BP_Coin", create_parent="Actor", operations=[
  {"op":"add_variable","name":"Value","type":"int","default":10,"instance_editable":true},
  {"op":"add_component","name":"Trigger","class":"SphereComponent","as_root":true,"properties":{"SphereRadius":80}},
  {"op":"add_component","name":"Mesh","class":"StaticMeshComponent","parent":"Trigger",
   "properties":{"StaticMesh":"/Engine/BasicShapes/Cylinder.Cylinder","RelativeScale3D":[0.6,0.6,0.1],
                 "BodyInstance.CollisionProfileName":"NoCollision"}},
  {"op":"add_node","id":"hit","kind":"component_event","component":"Trigger","event":"OnComponentBeginOverlap"},
  {"op":"add_node","id":"destroy","kind":"call_function","function":"DestroyActor"},
  {"op":"connect","from":"hit.then","to":"destroy.exec"}])
```
- **Ids** are your own aliases, valid within the call. The result maps them to node GUIDs (`ids`) for later calls.
- **Pins** match by name or label, case-insensitive (`exec`, `then`, `else`, `ReturnValue`, `self`/`Target`).
  - An unknown pin lists the node's real pins in the error.
  - A by-reference input, e.g. `SetValueAsVector.KeyName`, gets a MakeLiteral node wired in automatically when you give it a value in `pins`.
- **Functions** are found across KismetSystemLibrary, KismetMathLibrary, GameplayStatics, Actor, Pawn, Character, controllers, UserWidget, AI and Blackboard libraries, and the Blueprint itself.
  - Pass `"class"` to pick one, e.g. `"class":"BlackboardComponent"` for `SetValueAsVector`, or `"class":"UserWidget"` for `AddToViewport`.
  - `K2_` prefixes are tried for you, so `DestroyActor` finds `K2_DestroyActor`.
  - If unsure of a name, search with `unreal_api_search`.
- **Class defaults** go through `set_defaults`, which is applied after compiling and accepts paths like
  `"CharacterMovement.MaxWalkSpeed"`, `"AIControllerClass"` or `"AutoPossessAI":"PlacedInWorldOrSpawned"`.

## Node kinds
`event` (BeginPlay, Tick, ActorBeginOverlap, AnyDamage, EndPlay; widgets: Construct; BT tasks: ExecuteAI) ·
`custom_event` (+inputs) · `component_event` (OnComponentBeginOverlap, OnComponentHit, ...) · `call_function` ·
`get_var` / `set_var` · `branch` · `sequence` · `cast` · `self` · `macro` (DoOnce, Gate, ForLoop, ForEachLoop, IsValid) ·
`spawn_actor` · `node` with any K2Node class, e.g. `K2Node_CreateWidget` with `pins: {"Class": "/Game/UI/WBP_HUD.WBP_HUD_C"}`.

## Recipes
- **Show a HUD.** On BeginPlay (pawn, controller or a placed actor): `GetPlayerController` → `K2Node_CreateWidget.OwningPlayer`,
  `CreateWidget.ReturnValue` → `AddToViewport.self`.
- **AI that runs a tree.** AIController Blueprint:
  - BeginPlay → `RunBehaviorTree`, with `pins: {"BTAsset": ".../BT_X.BT_X"}`.
  - For a Blackboard value: `self` → `GetBlackboard.Target` → `SetValueAsVector.self`, with `pins: {"KeyName": "Goal", "VectorValue": [x, y, z]}`.
  - On the Character Blueprint: `set_defaults {"AIControllerClass": ".../BP_AI.BP_AI_C", "AutoPossessAI": "PlacedInWorldOrSpawned"}`.
- **Function with a return value.** `add_function` with `inputs` and `outputs`. Its nodes are `<Name>_entry` and `<Name>_result`; add nodes with `"graph":"<Name>"`.

## Widgets (UMG)
`widget_apply(widget, create=true, operations=[...])`:
- The first `add` becomes the root, usually a `CanvasPanel`.
- Canvas children take `slot: {anchors, position, size, alignment, auto_size}`.
- Conveniences: `text`, `font_size`, `color`, `percent`, `fill_color`.
- Mark widgets you'll drive from logic with `is_variable: true`. They become variables for `bp_apply` `get_var` on the widget Blueprint.

## Behavior Trees
`bt_build(tree, blackboard, keys=[...], root={...})`:
- Composites: Selector, Sequence, SimpleParallel. Tasks: MoveTo, Wait, RotateToFaceBBEntry, RunBehavior, or Blueprint tasks.
- Decorators: Blackboard, Cooldown, Loop, TimeLimit. Services: DefaultFocus, ...
- Blackboard key properties take the key name.
- Numbers like `AcceptableRadius` / `WaitTime` take a value, or `{"key":"Name"}` to read them from the Blackboard.
- The whole tree is validated before anything changes.
- AI movement needs a NavMesh: place a `NavMeshBoundsVolume` with `scene_compose`. It builds automatically, and `pie_test` waits for it.

## Gotchas
- Callable nodes (exec pins) only run when exec flows into them: `GetActorOfClass`, `Delay`, `SetText`, ... `bp_apply` returns
  `warnings` for any new node whose exec input is unwired. Pure nodes (no exec pins, e.g. `GetPlayerPawn`, math) run on demand.
- Python's script names differ from C++: `unreal.WidgetLibrary` is `WidgetBlueprintLibrary`, `unreal.AIBlueprintHelperLibrary` doesn't
  exist in Python (use the Blueprint node), and BT `MoveTo` params are `ValueOrBBKey` structs (handled by `bt_build`).
- An actor spawned already inside a trigger gets no BeginOverlap. Test pickups by moving the pawn into them (`pie_test` steps).
- Legacy node-by-node tools (`add_node`, `connect_nodes`, ...) still work, but `bp_apply` is faster, atomic and reports better errors.
