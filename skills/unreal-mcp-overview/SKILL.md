---
name: unreal-mcp-overview
description: >
  Overview of the local UnrealMCP server: tool layers, when to use which tool,
  and the observe -> act -> verify loop. Use when first connecting to a running
  Unreal Editor, when unsure which tool handles a task, or when the user asks
  what the tools can do.
---

# UnrealMCP: Overview

This MCP server drives a **live Unreal Editor** through the UnrealMCP C++ plugin (TCP, port 55557).
If a tool returns "cannot reach Unreal", the editor isn't running or the plugin isn't enabled.

## Start every session with
1. `unreal_status`: confirms the connection, the plugin version, and whether `execute_python` is available.
2. `project_context`: engine version, current level, maps, enabled plugins.

## Intent first
- **Question**: answer from knowledge, using read-only tools only if you need specifics.
- **Inspection**: read-only tools only (`object_describe`, `property_get`, `editor_log`, `viewport_capture`, `get_*`, `read_*`, `analyze_*`).
- **Change**: observe -> act -> verify (below). When unclear, don't modify.

## Tool layers (most specific first, most general last)
| Layer | Tools | Use for |
|---|---|---|
| Domain | `scene_brief`, `scene_query`, `actor_inspect`, `scene_compose`, `level_inspect`, `search_assets`, `asset_references`, `asset_manage`, `asset_import`, `material_inspect`, `material_edit`, `material_instance`, `niagara_edit`, `sequence_edit`, `data_table`, `enhanced_input`, `game_setup`, `cpp_source`, `live_coding_compile`, `performance_audit` | Batched, one-call workflows: prefer these |
| Verification | `pie_test` | Proving gameplay works at runtime |
| Dedicated | `create_blueprint`, `add_node`, `connect_nodes`, `create_variable`, `create_function`, `spawn_physics_blueprint_actor`, `apply_material_to_actor`, `set_actor_transform`, ... | Blueprint graphs and common actor work, all battle-tested |
| World building | `create_town`, `create_castle_fortress`, `construct_house`, `create_maze`, `create_tower`, ... | Fast procedural structures out of basic shapes |
| Reflection | `object_describe`, `property_get`, `property_set`, `function_call` | Reading or writing **any** property and calling **any** UFUNCTION on any object or actor, with no dedicated tool needed |
| Universal | `execute_python` | Anything the `unreal` Python module can do: materials, assets, import, sequencer, Niagara placement, editor utilities |
| Knowledge | `unreal_api_search`, `unreal_api_doc` | Exact Python API names and signatures, offline. Look things up before writing `execute_python` code. |
| Observation | `editor_log`, `viewport_capture`, `console_command` | Seeing what happened: errors, visuals, stats |
| Editor | `editor_action` (undo/redo/save_all/open_asset/load_level/select_actors/focus_actor/pie_start/pie_stop) | Session control |

## The loop
1. **Observe**: `get_actors_in_level` / `find_actors_by_name` / `object_describe` / `project_context`. Note `editor_log` → `last_seq`.
2. **Act**: the most specific tool that fits. In `execute_python`, wrap edits in `with unreal.ScopedEditorTransaction("what"):`.
3. **Verify**: re-read the state (`property_get`, `object_describe`), check `editor_log(since_seq=last_seq, min_verbosity="Warning")`, and `viewport_capture` for anything visual. For gameplay, `pie_test` with assertions is the proof.
4. If it's wrong: `editor_action(action="undo")`, then fix and retry. After 3 failed attempts on the same step, stop and ask the user.

Never report work as done because there were no errors. Verify that the change actually took effect.

## Other skills
- `scene-building`: actors, levels, lighting, world building
- `blueprint-authoring`: Blueprint graphs, variables, functions, compile/fix loop
- `material-vfx`: materials, material instances, Niagara placement through Python
- `game-development`: prompt → playable, end to end, proven with `pie_test`
