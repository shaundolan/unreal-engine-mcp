---
name: blueprint-authoring
description: >
  Build and edit Blueprints with bp_export / bp_apply: variables, components, functions, event graphs,
  interfaces, event dispatchers, timelines, macros, local variables, typed operators, structs and enums,
  in one atomic call, compiled with structured errors. Understand and lint existing ones with bp_explain /
  bp_review. Data assets with data_types (structs, enums, DataTables, DataAssets, curves). Also widget
  trees (widget_apply) and Behavior Trees (bt_build). Use for any gameplay logic, data, UI or AI work.
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
`get_var` / `set_var` (inherited variables work with or without `"class"`: the Blueprint's own parent class is
accepted) · `key_event` (`"key":"E"`, outputs `pressed` / `released`: a key binding without Tick polling) · `branch` · `sequence` · `cast` · `self` · `macro` (DoOnce, Gate, ForLoop, ForEachLoop, IsValid) ·
`spawn_actor` · `node` with any K2Node class, e.g. `K2Node_CreateWidget` with `pins: {"Class": "/Game/UI/WBP_HUD.WBP_HUD_C"}`.

Array functions (`Array_Length`, `Array_Add`, `Array_Contains`, ...) and `ForEachLoop` start out as wildcards. They take their
type from the array you connect, the same as in the editor. `Array_Get` becomes the editor's **Get** node, with pins `Array`,
`Dimension 1` and `Output`; the names `TargetArray`, `Index` and `Item` work too. A `warnings` entry about a "wildcard although it
is connected" means the source pin itself has no type.

## Recipes
- **Show a HUD.** On BeginPlay (pawn, controller or a placed actor): `GetPlayerController` → `K2Node_CreateWidget.OwningPlayer`,
  `CreateWidget.ReturnValue` → `AddToViewport.self`.
- **AI that runs a tree.** AIController Blueprint:
  - BeginPlay → `RunBehaviorTree`, with `pins: {"BTAsset": ".../BT_X.BT_X"}`.
  - For a Blackboard value: `self` → `GetBlackboard.Target` → `SetValueAsVector.self`, with `pins: {"KeyName": "Goal", "VectorValue": [x, y, z]}`.
  - On the Character Blueprint: `set_defaults {"AIControllerClass": ".../BP_AI.BP_AI_C", "AutoPossessAI": "PlacedInWorldOrSpawned"}`.
- **Function with a return value.** `add_function` with `inputs` and `outputs`. Its nodes are `<Name>_entry` and `<Name>_result`; add nodes with `"graph":"<Name>"`.

## Understand and review before you edit
- `bp_explain(blueprint)` gives the entry points (events, custom, component and key events, functions) and the execution flow of each
  (`BeginPlay → GetPlayerPawn → Cast to BP_Hero → ...`). It also lists the variables each one reads and writes, the external calls by class,
  and a dependency graph. Read its `markdown` first.
- `bp_review(blueprint)` lints the Blueprint. Each finding has a rule, a severity, the graph and the node id, so you can go straight to
  `bp_apply` `remove_node` / `connect`. Rules:
  - `unwired_exec`, `dead_node`;
  - `unused_variable`, `write_only_variable`;
  - `tick_heavy`, `cast_in_tick`, `expensive_in_tick` (GetAllActorsOfClass, spawning, printing), `latent_in_tick`;
  - `missing_is_valid`;
  - `infinite_loop_risk` (an exec cycle without a Delay, While loops, a custom event calling itself).

  Fix errors and warnings; info notes are judgment calls.

## Interfaces, dispatchers, timelines
- **Interface.** `bp_apply("/Game/BPI_Interact", create_parent="Interface", operations=[{"op":"add_function","name":"Interact","inputs":[...]}])`.
  - Implementers: `{"op":"implement_interface","interface":"/Game/BPI_Interact"}`, then `{"kind":"event","event":"Interact"}`.
    A function with outputs is a graph instead: add nodes with `"graph":"Interact"`.
  - Callers: `{"kind":"interface_message","interface":"/Game/BPI_Interact","function":"Interact"}`, with the target on `self`.
    It's safe on any actor, so you don't need a cast.
- **Event dispatcher.**
  - Owner: `{"op":"add_dispatcher","name":"OnOpened","inputs":[...]}`, then `{"kind":"call_dispatcher","dispatcher":"OnOpened"}`.
  - Listener in another Blueprint:
    - `{"kind":"custom_event","name":"HandleOpened","dispatcher":"OnOpened","class":"/Game/BP_Door"}` gives a handler with matching parameters;
    - `{"kind":"bind_dispatcher","dispatcher":"OnOpened","class":"/Game/BP_Door","event":"<handler id>"}`, with the door reference wired to its `self` pin.
  - `unbind_dispatcher` / `clear_dispatcher` work the same way.
- **Timeline.** `{"kind":"timeline","name":"Lift","length":2,"tracks":[{"name":"Alpha","type":"float","keys":[[0,0],[2,1,"cubic"]]}]}`.
  - Track types: `float`, `vector` (`[t,[x,y,z]]`), `color` (`[t,[r,g,b,a]]`), `event` (key times only).
  - Exec in: `Play`, `PlayFromStart`, `Reverse`, `Stop`. Out: `Update` (every frame while playing), `Finished`, and one data pin per track.
  - Lerp from a stored start to a target with `VLerp` / `Lerp` and `Alpha`.
- **Functions, macros, locals.**
  - `add_function` with `"pure":true` for getters.
  - `{"op":"add_local_variable","function":"F","name":"Tmp","type":"int"}`: get_var/set_var inside `F` then resolve it.
  - `{"op":"add_macro","name":"M","inputs":[{"name":"In","type":"exec"},...],"outputs":[...]}`: nodes `M_entry` / `M_exit`; use it with `{"kind":"macro","macro":"M"}`.
- **Typed operators and flow.**
  - `{"kind":"operator","op":"+","type":"vector"}` is the 5.x promotable operator node.
  - `select` (`options`, `index_type` int/bool/enum, `type`), and `switch` on int, string, name or `enum:<Enum>` (`cases`).
  - `make_struct` / `break_struct` (`struct`), `format_text` (`"format":"{Name}: {HP}"` makes the argument pins).
  - `get_data_table_row` (`table`, `row`; exec outs `RowFound` / `RowNotFound`, data `OutRow`).
  - `async_action` (`function`, `class`) and `ai_move_to`.
- **Enum pins** take the entry's name or its display name (`"Rare"`). User-defined enums store `NewEnumerator1` internally.

## Data types (`data_types`)
One call can make the enum, the struct that uses it, and a table of it:
```json
data_types(operations=[
  {"op":"enum","asset":"/Game/Data/E_Rarity","entries":["Common","Rare","Epic"]},
  {"op":"struct","asset":"/Game/Data/S_Item","members":[{"name":"Label","type":"string"},{"name":"Damage","type":"float","default":1},
                                                     {"name":"Rarity","type":"enum:/Game/Data/E_Rarity","default":"Common"}]},
  {"op":"data_table","asset":"/Game/Data/DT_Items","struct":"/Game/Data/S_Item","rows":{"Sword":{"Label":"Sword","Damage":10,"Rarity":"Rare"}}},
  {"op":"curve","asset":"/Game/Data/C_Falloff","keys":[[0,1],[1000,0,"cubic"]]},
  {"op":"curve_table","asset":"/Game/Data/CT_XP","rows":{"XP":[[1,0],[10,4500]]}},
  {"op":"data_asset","asset":"/Game/Data/DA_Hero","class":"/Game/Data/BP_HeroData","properties":{"MaxHealth":250}}])
```
- Editing a struct keeps existing members and their data. Use `remove` and `rename` to change them.
- DataTables also take `csv` (the first column is the row name) and `mode:"merge"`.
- Read any of these back with `data_types_export`.

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
