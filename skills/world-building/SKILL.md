---
name: world-building
description: >
  Outdoor world building with the local UnrealMCP tools: landscapes (create,
  sculpt procedurally or from a heightmap, paint layers by slope/height),
  foliage and HISM scattering, PCG graphs and volumes, and spline roads,
  rivers and pipes. Use when making terrain, forests, vegetation, roads or
  any procedural environment.
---

# World Building

Every tool here is an **inspect / edit pair** that batches many ops into one call (one undo step) and returns
per-op results with structured errors. Most take `dry_run=true`: run it first when unsure.

| Task | Edit | Inspect |
|---|---|---|
| Terrain | `landscape_edit` | `landscape_inspect` |
| Vegetation, rocks, props | `foliage_scatter` | `foliage_inspect` |
| Rule-based procedural scatter | `pcg_graph` | `pcg_inspect` |
| Roads, rivers, pipes, fences | `spline_edit` | `spline_inspect` |

Units are **world cm** everywhere. Positions are `[x, y]` world, or `center_uv` / `start_uv` = 0..1 across the landscape.

## 1. Landscape
One `landscape_edit` call creates, sculpts, assigns the material and paints the layers. Heights are read once and written once.
```json
[{"op":"create","label":"Terrain","location":[0,0,0],"component_count":[8,8],"section_size":63},
 {"op":"noise","amplitude":1500,"wavelength":8000,"octaves":4,"seed":1},
 {"op":"hill","center_uv":[0.7,0.3],"radius":6000,"height":3000},
 {"op":"flatten","center":[0,0],"radius":1500,"falloff":1000,"height":200},
 {"op":"erode","iterations":20,"talus_deg":35},
 {"op":"material","path":"/Game/Landscape/M_Terrain","layers":[
     {"name":"Grass","color":[0.12,0.3,0.05]},{"name":"Rock","color":[0.3,0.28,0.25]},{"name":"Snow","color":[0.9,0.9,0.95]}]},
 {"op":"paint","rules":[{"layer":"Grass"},
                        {"layer":"Rock","slope_min":30,"slope_falloff":5},
                        {"layer":"Snow","height_min":2500,"height_falloff":300}]}]
```
- **Size**: vertices per axis = `component_count × section_size × sections_per_component + 1`, and `scale.x/y` = cm per quad (default 100).
  For 8×8 components of 63 quads you get 505² vertices, about 504 m square. Keep it small (4×4 × 31, 125²) for fast iteration.
  Generation runs in the MCP server in pure Python. 505² noise takes about 4 s; erosion costs about 0.3 s per iteration at that size.
- **Height range**: ±256 × scale.z cm around `location.z`. The result reports `clipped_vertices` if you exceed it. Raise `scale.z` for big mountains.
- `location` on create is the **centre**. `landscape_inspect` reports the actor location, which is the corner.
- **Sculpt ops** apply in order: `flat`, `noise` (`ridged:true` for mountain ridges; `mode` add/set/max/min), `hill` (a negative height makes a crater or valley),
  `ramp`, `flatten` (building pads), `smooth`, `erode` (thermal), `terrace`, `clamp`, `scale`, `offset`, `import_heightmap`
  (8/16-bit PNG or RAW `.r16` **on the MCP server's disk**, mapped to `min_height`..`max_height`).
- **Paint rules** layer in order: each rule covers the earlier ones by its mask. Weights always sum to 255, and layers the rules don't mention are zeroed.
  `{"op":"paint","layer":"Grass"}` fills a single layer.
- The `material` op builds a simple LandscapeLayerBlend material with one flat colour (or `texture`) per layer.
  To use an existing material, pass its `path` with `"create":false` plus `layers` names.
- On an existing landscape, pass `target`: sculpt ops modify the current heights.
- Verify with `landscape_inspect`: `height_world_cm`, `slope_deg {mean,p90,max}`, and per-layer `coverage` / `dominant_fraction`.

## 2. Foliage / scatter
```json
[{"op":"scatter","meshes":[{"mesh":"/Game/Trees/SM_Pine","weight":3},{"mesh":"/Game/Trees/SM_Birch"}],
  "surface":"Terrain","density":6,"slope_max":25,"height_max":2400,"scale":[0.8,1.4],
  "min_distance":300,"seed":7},
 {"op":"scatter","mesh":"/Game/Rocks/SM_Rock","surface":"Terrain","density":1,"slope_min":30,
  "align_to_normal":true,"mode":"hism","label":"Rocks"}]
```
- `density` is instances per 100 m² (a 10 m square). Or pass `count`. `area` limits the region (`min`/`max` box or `center` + `radius`).
- Placement traces down onto the surface, so `surface` can be any actor with collision, not only a landscape.
- `mode:"foliage"` (default) places editable InstancedFoliageActor instances, with an `FT_<mesh>` foliage type in `foliage_type_dir`.
  `mode:"hism"` places one actor tagged `mcp_scatter` with HISM components.
- Read `rejected` (miss / other_surface / slope / height / spacing) when `placed < requested`.
- `{"op":"remove","mesh":...,"area":...}` and `{"op":"clear"}` undo scatter. Remove a road corridor before carving the road.
- Verify: `foliage_inspect(sample_slopes=true)` reports the real ground slope under every instance.

## 3. PCG (procedural, regenerates when the landscape changes)
```python
pcg_graph(graph="/Game/PCG/PCG_Forest", create=True, clear=True,
  nodes=[{"id":"landscape","type":"GetLandscapeData"},
         {"id":"sampler","type":"SurfaceSampler","properties":{"points_per_squared_meter":0.05}},
         {"id":"jitter","type":"TransformPoints","properties":{"rotation_max":[0,0,360]}},
         {"id":"spawn","type":"StaticMeshSpawner","meshes":[{"mesh":"/Game/Trees/SM_Pine"}]}],
  edges=[{"from":"landscape.Out","to":"sampler.Surface"},{"from":"input.In","to":"sampler.Bounding Shape"},
         {"from":"sampler","to":"jitter"},{"from":"jitter","to":"spawn"},{"from":"spawn","to":"output"}],
  volume={"label":"ForestVolume","fit":"Terrain","seed":42})
```
- The call is atomic: the spec is built on a scratch graph first, so a bad type, property or pin changes nothing.
  The error lists the valid pins.
- Node ids become node titles, so reuse an id to update that node. `pcg_inspect(graph=...)` lists node ids, types, pin labels and edges.
- Generation is asynchronous. The tool waits up to `wait_s` for spawned instances, then reports `volume.instances` and per-mesh counts.
- Property names are snake_case editor properties of the settings class (`unreal_api_doc PCGSurfaceSamplerSettings`).

## 4. Splines: roads, rivers, pipes
```json
[{"op":"create","label":"Road","points":[[-5000,-4000,0],[0,-1000,0],[5000,4000,0]],
  "snap_to_ground":true,"ground_offset":5,"mesh":"/Engine/BasicShapes/Cube.Cube","mesh_scale":[5,0.1],
  "segment_length":400},
 {"op":"carve","target":"Road","landscape":"Terrain","width":500,"falloff":400,"layer":"Dirt"}]
```
- One SplineMeshComponent per segment, bent along the spline. `mesh_scale` is the cross-section (Y, Z) scale.
  Pipes: Cylinder with `"forward_axis":"z"`.
- `carve` flattens the landscape to the spline (LandscapeProxy.EditorApplySpline) and optionally paints a layer.
  The layer must already exist (`landscape_edit` layers/material op).
- Rivers: put the points below the surface, `carve` with `"raise":false`, then add a water-material plane spline.
- `modify` takes `points` or `append` and rebuilds the segments. Verify with `spline_inspect` (points, length, segments).

## Order of work for a scene
1. Landscape: create, sculpt, material, paint. Check `landscape_inspect`.
2. Roads and rivers: `spline_edit` create + carve (carving changes heights, so do it before scattering).
3. Scatter or PCG. `foliage_scatter` `remove` with an `area` clears instances off roads.
4. `viewport_capture` from a high angle (`editor_action focus_actor` on the landscape), then `editor_action save_all`.

## Limits
- Landscapes are created without world-partition streaming proxies and with yaw 0. Rotate after creating if needed.
- Paint layers are weight-blended. Heights and weights are written to the landscape's first edit layer.
- `import_heightmap` reads files where the MCP server runs, not the editor machine, which matters for a remote editor.
