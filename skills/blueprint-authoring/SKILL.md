---
name: blueprint-authoring
description: >
  Blueprint authoring with the local UnrealMCP tools: create a Blueprint, add
  components/variables/functions, build event graphs node by node, compile, and
  fix errors. Use when creating or editing Blueprints or debugging compile errors.
---

# Blueprint Authoring

## Cycle
1. **Inspect**: `read_blueprint_content`, `analyze_blueprint_graph`, `get_blueprint_variable_details`, `get_blueprint_function_details`.
2. **Declare**: `create_blueprint(name, parent_class)` (assets go to `/Game/Blueprints/`), `add_component_to_blueprint`, `create_variable`, `create_function` + `add_function_input` / `add_function_output`.
3. **Graph**: `add_event_node` (BeginPlay, Tick, ...), `add_node` (node types are listed in Guides/blueprint-graph-guide.md), `connect_nodes`, `set_node_property`. Note every `node_id` that comes back; connections need them.
4. **Compile**: `compile_blueprint`. Then `editor_log(since_seq=..., min_verbosity="Warning")` to read the compiler messages.
5. **Verify**: `analyze_blueprint_graph` and check every intended connection exists. For runtime behaviour, spawn it (`spawn_blueprint_actor`) and run PIE (`editor_action pie_start` → `editor_log` → `pie_stop`).

## Rules
- Pure nodes (getters, math, `K2_GetActorLocation`, make/break struct) have **no exec pins**. Wire data only.
- `K2_` prefix: some AActor functions have it (`K2_GetActorLocation`, `K2_SetActorLocation`, `K2_DestroyActor`) and some don't (`GetActorForwardVector`, `GetOwner`). Confirm with `object_describe(class_name="Actor", filter="Location")`.
- Fix narrowly: add the missing node or connection. Don't rebuild the whole graph.
- Defaults on the class default object: `property_set(path="/Game/Blueprints/BP_X.Default__BP_X_C", property="Health", value=100)`.

## When the dedicated tools aren't enough
`execute_python` with `unreal.BlueprintEditorLibrary` (5.1+) covers more, e.g. reparenting, `compile_blueprint`, and finding the event graph. Search first: `unreal_api_search("blueprint editor library")`.
Graph authoring from a declarative spec (one call for a whole graph) is on the Phase 2 roadmap (`bp_author`).
