---
name: 3d-generation
description: >
  Generate 3D assets from text or images with LOCAL open-weight models (mesh_generate), post-process and import them
  into Unreal with LODs and collision, and auto-rig characters so the UE5 Mannequin animations play on them
  (auto_rig). Covers when to generate vs model in Blender vs kitbash, prompt tips, quality expectations and model
  licences. Use when a level needs organic or detailed props, creatures or characters that the project doesn't have.
---

# 3D generation: text/image → mesh → Unreal (local, free)

`gen3d_status` first. If it isn't available, tell the user to start `servers/gen3d/gen3d_server.py` (see its README)
or to set `UNREAL_MCP_GEN3D_URL` to their GPU box. Blender must be installed too (`blender_status`).

## Generate, model, or kitbash?
| Need | Use |
|---|---|
| Boxy, architectural, modular, exact dimensions (walls, crates, stairs, pillars, pipes) | **Blender** `blender_run` (3d-assets skill). Clean topology, exact sizes, cheap. |
| Many variants built from existing parts (fences, rubble, shelves) | **Kitbash**: `scene_compose` / `instanced_mesh_place` with project or engine meshes. |
| Organic or irregular shapes, detailed props, creatures, characters, "something like this photo" | **Generate**: `mesh_generate`. |
| Hero assets the player inspects up close | Generate a draft, then treat it as a blockout; real production art still wins. |

## Quality to expect
- Silhouettes are good. Small details are soft and thin parts (straps, swords, fingers) melt or vanish. Text and logos
  don't survive.
- The back of the object is invented from one view. Check the render before you place it.
- TripoSR: fast, low detail, colours baked from vertex colours. SF3D, Hunyuan3D and TRELLIS.2: sharper, with real
  UVs and PBR maps.
- Generated meshes come out dense and irregular. `target_tris` decimates them: props 2-8k, large props 10-20k,
  characters 15-40k.

## Prompting (text → image → 3D)
- Ask for **one object**, whole, centred, on a plain background. The server appends that framing for you.
  - Good: `"a wooden barrel with dark iron hoops, game asset"`.
  - Bad: `"a tavern with barrels"`.
- Name materials and colours: "rusty iron", "mossy stone", "red lacquered wood".
- For characters, pass `subject="character"` and ask for a **T-pose or A-pose, arms away from the body, full body,
  front view** so auto_rig can find the limbs.
- An image beats a prompt when you have one: `image_path=...` (a file on the MCP server machine). The same `seed`
  with the same input gives the same mesh, so change the seed for variations.

## Loop
1. `mesh_generate(prompt, height_cm=<real size>, out_name="SM_X", destination="/Game/Generated/Props",
   import_to_unreal=False)`. Look at both returned images: the conditioning image, then the render.
2. If it's wrong, adjust the prompt or seed, or fix orientation with `rotate=[x,y,z]`, and run again.
3. Run the same call with `import_to_unreal=True` (the default), then check:
   - `asset`;
   - `postprocess.size_cm` (Z is your `height_cm`);
   - `unreal.asset.lod_count`;
   - `unreal.collision`.

   A re-run replaces the asset safely.
4. Place it: `scene_compose([{"op":"spawn","asset":"/Game/Generated/Props/SM_X.SM_X","label":"X"}])`.
5. To reuse an existing GLB or OBJ (a scan, or output from a model you ran by hand), call `mesh_postprocess(file, ...)`.
   It runs the same cleanup, scale, pivot, LOD and collision steps.

Defaults that matter:
- `pivot=bottom_center`, so the mesh stands on the floor at Z=0.
- `collision=convex`: 8 convex hulls. Use `box` for crates, `complex_as_simple` for static terrain-like pieces, and
  `none` for characters.
- `lods=3`: three extra LODs at 50%, 25% and 12.5%.
- `uv=auto`: the model's UVs are kept when it has them, otherwise a smart unwrap with colours baked to a texture.
- Nanite: UE 5.8's importer turns it on. LODs then don't render, and the result shows `nanite: true` and
  `fallback_triangles`. Pass `nanite=False` to use the classic LODs, for example for mobile.
- Layout: the mesh ends up at `<destination>/<out_name>`. Its materials and textures go in
  `<destination>/<out_name>/Materials` and `/Textures`.

## Characters: auto_rig
1. `mesh_generate(image_path=..., subject="character", height_cm=180, target_tris=20000, collision="none",
   lods=0)`.
2. Rig it: `auto_rig(mesh=<result glb, or the imported /Game/... static mesh>, type="humanoid",
   place_actor="Hero")`.
   - Bones carry UE5 Mannequin names (`pelvis`, `spine_01..05`, `upperarm_l`, `thigh_r`, ...).
   - Weights are Blender bone-heat automatic weights.
   - In Unreal it creates an IK Rig (engine auto-characterisation), an IK Retargeter from the Mannequin with chains
     auto-mapped and bones auto-aligned, and retargeted copies of `MM_Idle` and `MF_Unarmed_Walk_Fwd`. Pass
     `animations=[...]` for more.
   - The template Mannequin must be in the project: `scripts/install_mannequin.py`.
3. Check the result:
   - `rig.vertices_per_bone`: every limb bone should have vertices.
   - `rig.warnings`: for example "arms not spread".
   - `chains`: Spine, Head, LeftArm and the rest.
4. `pie_test` or `viewport_capture` to watch it walk.

   If it walks backwards or sideways, re-run with `rotate=[0,0,180]` (or 90). The mesh must face -Y in Blender, which
   is what generators output.
5. `type="quadruped"` rigs a spine, neck, head, tail and four 3-bone legs. There is no Mannequin retarget for it, so
   animate it with your own clips. `type="prop"` makes a single bone, for physics or simple swaying.
6. To give the character gameplay, use the animation skill (`animbp_apply`, a locomotion BlendSpace) with the
   retargeted animations.

The rig goes to Unreal as GLB. Blender's FBX export puts a 100x scale on the root bone, which breaks retargeting.
Re-running replaces the skeletal mesh and its skeleton.

Limits: no fingers, no face, no twist bones. Loose clothing and capes get stiff or stretchy weights, and long hair
follows the head. For a hero character, take the FBX into Blender and fix the weights by hand.

## Licences (check every result's `licence`, `commercial` and `warnings`)
| Backend | Licence | Commercial use |
|---|---|---|
| TripoSR | MIT | yes |
| Stable Fast 3D | Stability AI Community License | only under US$1M annual revenue |
| Hunyuan3D 2.1 | Tencent Hunyuan 3D Community License | **not licensed in the EU, the UK or South Korea** (outputs too); a licence is needed above 1M MAU |
| TRELLIS.2 | MIT, but nvdiffrast/nvdiffrec are NVIDIA non-commercial | treat as non-commercial unless you replace those |
| FLUX.1-schnell (text→image) | Apache-2.0 | yes |
| SDXL base (text→image) | Open RAIL++-M | yes, within its use restrictions |

When a result carries warnings, **tell the user** before the asset goes into a shipping project.
