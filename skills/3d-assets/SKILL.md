---
name: 3d-assets
description: >
  Make custom 3D props with headless Blender (blender_run) and bring them into Unreal:
  modelling recipes, scale/pivot/UV/material rules, preview checks, import and placement.
  Use when a level needs a mesh that the engine's basic shapes or the project's content can't supply.
---

# 3D assets: Blender → Unreal

`blender_status` first. If Blender is missing, tell the user (install Blender 4.2+ LTS or set `UNREAL_MCP_BLENDER`).

## Rules that keep imports clean
- **Scale:** Blender metres → Unreal centimetres (1 m = 100 uu). Model at real size: a door is about 2.1 m, a crate 1 m.
- **Pivot:** `origin_to_bottom(obj)` for anything that sits on a floor, so `scene_compose` locations put it on the ground.
- **One asset per export:** `join` parts into one object named like the asset (`SM_Crate`), then `export("SM_Crate")`. The file name becomes the asset name.
- **UVs:** `smart_uv(obj)` before export. Unreal needs them for textures and generates its lightmap UVs from them.
- **Materials:** each `material(...)` becomes a material slot in Unreal. Check `exports[].material_slots` for `<empty>` (a part with no material), then swap the imported materials for real ones with `material_edit` or `material_instance`.
- **Budget:** check `exports[].triangles`. Props stay under about 5k; hero assets under about 50k. Use `modifier(o, "DECIMATE", ratio=0.5)` to cut the count.

## Loop
1. `blender_run(code, show_preview=True)` with no `import_destination`, and look at the preview image.
2. Fix the code and re-run until the preview looks right. Each run starts from `clear_scene()`, so the code is the whole source of the asset.
3. `blender_run(same code, import_destination="/Game/Props")`, then check `import.imports[].imported` for the asset paths.
4. Place it: `scene_compose([{"op":"spawn","asset":"/Game/Props/SM_Crate.SM_Crate","label":"Crate","location":[0,0,0]}])`, or with grid or circle patterns for repetition.
5. `viewport_capture` to see it in the level.

## Recipe: crate with a metal lid
```python
clear_scene()
c = primitive("cube", "Body", size=1)
modifier(c, "BEVEL", width=0.04, segments=2)
material(c, "M_Wood", color=(0.45, 0.3, 0.15), roughness=0.8)
lid = primitive("cylinder", "Lid", radius=0.2, depth=0.1, location=(0, 0, 0.55))
material(lid, "M_Metal", color=(0.7, 0.7, 0.75), metallic=1.0)
crate = join([c, lid], "SM_Crate")
smart_uv(crate); shade_smooth(crate); origin_to_bottom(crate)
preview(); export("SM_Crate")
result = {"tris": triangles(), "size_m": bounds()["size"]}
```
Pickup glow: `material(o, "M_Coin", color=(1, 0.8, 0.1), metallic=1, emission=(1, 0.7, 0), emission_strength=5)`.
Arrays: `modifier(plank, "ARRAY", count=6, relative_offset_displace=(1.05, 0, 0))`. Rounded: `"SUBSURF", levels=2`.
Raw geometry: `mesh_from_data("SM_Ramp", verts, faces)`.

## Limits
`blender_run` makes static meshes. For organic shapes, creatures and characters, generate them instead
(`mesh_generate`), and rig characters with `auto_rig` (skill 3d-generation).
