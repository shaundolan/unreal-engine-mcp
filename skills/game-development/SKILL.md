---
name: game-development
description: >
  End-to-end "prompt to playable" workflow with the local UnrealMCP tools:
  plan the game loop, build the level, wire input and the game mode, author
  gameplay Blueprints, and prove it works with pie_test. Use when asked to make
  a game, a playable prototype, a mechanic, or a level that must be playable.
---

# Game Development: prompt → playable

The work isn't done until **`pie_test` passes**. Build in vertical slices, and test each slice before starting the next.

## 0. Plan (write it down before calling tools)
- The core loop in one sentence ("collect 10 coins before the timer ends").
- Actors: player, pickups/enemies/props, game mode, HUD.
- For each slice, the pie_test assertion that proves it works.

## 1. Orient
`unreal_status` → `project_context` (engine version, maps, plugins) → `scene_brief`.
Check `enabled_plugins` for EnhancedInput (default in 5.x) before relying on it.

## 2. Level greybox
- One `scene_compose` call builds the floor, walls, platforms (engine basic shapes, scaled), a PlayerStart and lights:
  `{"op":"spawn","class":"PlayerStart","label":"PlayerStart","location":[0,0,100]}`,
  `{"op":"spawn","class":"DirectionalLight",...}`, `{"op":"spawn","class":"SkyLight",...}`.
- Put things in folders (`"folder":"Arena/Walls"`) so later queries stay cheap.
- Check with `viewport_capture` after `editor_action(action="focus_actor", ...)`.

## 3. Input
`enhanced_input` creates `IA_Move` (Axis2D), `IA_Look` (Axis2D), `IA_Jump` (Boolean) and `IMC_Default` with
WASD (SwizzleAxis/Negate), Mouse2D and gamepad mappings in one call.

## 4. Game framework
- `create_blueprint(name="BP_ArenaGameMode", parent_class="GameModeBase")` and the player character
  (`parent_class="Character"`), or reuse the template character if the project has one (`search_assets(class_name="Blueprint")`).
- Defaults through reflection on the class default object:
  `property_set(path="/Game/Blueprints/BP_ArenaGameMode.Default__BP_ArenaGameMode_C", property="DefaultPawnClass", value="/Game/Blueprints/BP_Hero.BP_Hero_C")`.
- `game_setup(level_game_mode="/Game/Blueprints/BP_ArenaGameMode.BP_ArenaGameMode_C")`, then `editor_action(action="save_all")`.

## 5. Gameplay logic (Blueprint)
Use the `blueprint-authoring` skill: variables (`create_variable`), events (`add_event_node`), nodes (`add_node`),
wiring (`connect_nodes`), `compile_blueprint`, then read `editor_log` for compiler errors.
Typical pickups: a BP with a sphere collision component, OnComponentBeginOverlap → cast to the player → add score → DestroyActor.
Data-driven values (damage, speeds, loot) go in a DataTable (`data_table`).

C++ instead of Blueprint: `cpp_source` (write) → `live_coding_compile` for function bodies. New classes or reflected members need an editor restart.

## 6. Juice
Custom props (pickups, crates, doors) come from `blender_run` with `import_destination`: see the `3d-assets` skill.
`material_edit` / `material_instance` for readable colours (pickups emissive), `niagara_edit` for effects,
`sequence_edit` for an intro camera.

## 7. Prove it
```json
pie_test(duration_s=3, assertions=[
  {"name":"player spawned","type":"actor_exists","class":"Character","min":1},
  {"name":"10 coins","type":"python","expr":"len([a for a in actors if 'Coin' in a.get_actor_label()]) == 10"},
  {"name":"player on floor","type":"distance","a":"@player","b":"PlayerStart","op":"<=","value":300},
  {"name":"clean run","type":"no_errors"},
  {"type":"log_absent","text":"Accessed None"}])
```
Drive the game with `steps` (they run after `duration_s`):
```json
steps=[
  {"check":[{"type":"distance","a":"@player","b":"PlayerStart","op":"<=","value":150}]},
  {"do":"player.add_movement_input(vec([1,0,0]), 1.0)","repeat":40,"interval_s":0.05},
  {"check":[{"type":"distance","a":"@player","b":"PlayerStart","op":">=","value":200}]},
  {"do":"player.jump()"}, {"wait_s":0.3},
  {"check":[{"type":"python","expr":"player.get_actor_location().z > 150"}]}]
```
If something fails, read `results[].actual`, `steps[].assertions` and `errors_in_log`, fix just that, and re-run.
Then run `performance_audit` and `editor_action(action="save_all")`.

## Limits today (be honest with the user)
- PIE input is driven through pawn/controller calls (`add_movement_input`, `jump`, BlueprintCallable functions), not raw key events. Enhanced Input action bindings aren't exercised directly.
- UMG widget trees, Behavior Tree graphs and AnimBP graphs can't be authored yet. Create the assets and describe the remaining manual steps.
