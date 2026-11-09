---
name: scene-building
description: >
  Scene and level building with the local UnrealMCP tools: finding, spawning,
  editing and verifying actors; lighting; procedural structures; levels and PIE.
  Use when placing actors, laying out a level, lighting a scene, or building
  structures.
---

# Scene & Level Building

## Find
- `scene_brief`: first look at an unfamiliar level (counts, classes, folders, bounds, lights, cameras).
- `scene_query(class_name=, label_contains=, tag=, folder=, near=, radius=, box_min=, box_max=)`: actor labels (Outliner names, **not** asset paths).
- `actor_inspect(target)`: one actor's transform, components, meshes, materials. `object_describe` for raw reflection.
- Assets: `search_assets(query="rock", class_name="StaticMesh")` returns full object paths. Engine basic shapes: `/Engine/BasicShapes/Cube.Cube`, `Sphere`, `Cylinder`, `Cone`, `Plane`.

## Spawn & edit
- **Default:** `scene_compose` puts spawn, modify and delete in one call and one undo step, with grid/circle/line patterns:
  ```json
  [{"op":"spawn","asset":"/Engine/BasicShapes/Cylinder.Cylinder","label":"Pillar","location":[0,0,0],
    "scale":[1,1,4],"pattern":{"type":"circle","count":12,"radius":1500,"face_center":true},"folder":"Arena"},
   {"op":"spawn","class":"PointLight","label":"Torch","location":[0,0,400],
    "properties":{"light_component.intensity":8000}}]
  ```
- Anything scene_compose can't express: a single `execute_python` call with a loop. Don't make one tool call per actor.
  ```python
  sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
  mesh = unreal.load_asset("/Engine/BasicShapes/Cube.Cube")
  with unreal.ScopedEditorTransaction("pillars"):
      for i in range(10):
          a = sub.spawn_actor_from_object(mesh, unreal.Vector(i * 300, 0, 0))
          a.set_actor_label(f"Pillar_{i}")
          a.set_actor_scale3d(unreal.Vector(1, 1, 4))
  result = "ok"
  ```
- One property: `property_set(path="Pillar_3", property="StaticMeshComponent.Mobility", value="Movable")`.
- Transforms: `set_actor_transform`. Deletion: `delete_actor`.
- Procedural structures: `create_town`, `create_castle_fortress`, `construct_house`, `construct_mansion`, `create_maze`, `create_tower`, `create_wall`, `create_staircase`, `create_arch`, `create_pyramid`, `create_suspension_bridge`, `create_aqueduct`.

## Lighting
Spawn `unreal.DirectionalLight`, `unreal.SkyLight`, `unreal.SkyAtmosphere`, `unreal.ExponentialHeightFog`, `unreal.PointLight` with `spawn_actor_from_class`. Tune them with `property_set`, e.g. `LightComponent.Intensity`, `LightComponent.LightColor` = `{"R":255,"G":200,"B":150,"A":255}`.

## Levels & play
- `project_context` → `maps`, `current_level`. Switch maps with `level_open(path="/Game/Maps/Arena")` (`create=true` makes and saves a new one). Don't call `LevelEditorSubsystem.load_level` / `new_level` or load a `duplicate_asset` copy of a map from `execute_python`: a map whose world is already in memory crashes the editor ("World Memory Leaks"), and `level_open` unloads it first.
- Save with `editor_action(action="save_all")`. Nothing is saved to disk until you do. It never prompts, saves maps too, and lists `failed` packages (for example read-only files); any failure makes `success` false.
- Many copies of one mesh (trees, lamps, cars): `instanced_mesh_place` (one HISM component, bulk transforms, cull distance), not one actor each. Meshes without collision: `mesh_collision` (box / convex / complex_as_simple, collision profile).
- PIE: `editor_action(action="pie_start")`, then poll `project_context().pie_running`, read `editor_log`, then `pie_stop`.

## Verify
- `viewport_capture` after visual changes. To frame the subject first, use `editor_action(action="focus_actor", actor_name=...)`. After new materials or imports, call `wait_for_shaders` first; until the queue is empty the capture shows old shaders or placeholders.
- Run `editor_log(since_seq=..., min_verbosity="Warning")` after any batch.
- Count check: re-query and compare with what you intended to create.
