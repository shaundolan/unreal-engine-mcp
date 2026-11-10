---
name: material-vfx
description: >
  Materials and VFX with the local UnrealMCP tools: create materials and
  expression graphs, material instances, apply them, and place Niagara systems,
  all through execute_python. Use when creating or editing materials, material
  instances, or placing particle effects.
---

# Materials & VFX

Dedicated tools: `material_inspect`, `material_edit` (a whole graph in one call), `material_instance`.
Use the `execute_python` recipes below only for things those tools don't cover. Look up exact names with `unreal_api_search`.

## Preferred: material_edit
```json
{"path":"/Game/Materials/M_Glow","create":true,"clear":true,
 "operations":[
  {"op":"add","id":"tint","class":"VectorParameter","properties":{"parameter_name":"Tint","default_value":[0.1,0.6,1,1]}},
  {"op":"add","id":"power","class":"ScalarParameter","properties":{"parameter_name":"Glow","default_value":5}},
  {"op":"add","id":"mul","class":"Multiply"},
  {"op":"connect","from":"tint","to":"mul","to_input":"A"},
  {"op":"connect","from":"power","to":"mul","to_input":"B"},
  {"op":"output","from":"tint","property":"BaseColor"},
  {"op":"output","from":"mul","property":"EmissiveColor"}]}
```
Then `material_instance(path="/Game/Materials/MI_Glow_Red", parent="/Game/Materials/M_Glow", parameters={"Tint":[1,0.1,0.05,1]})`.
Imported textures/meshes: `asset_import(local_files=[...], destination="/Game/Props")`.

Details that bite:
- `clear=true` really empties the graph (UE's own delete_all_material_expressions leaves every other node).
- Pins match loosely: Noise's position input is named "World Position", so `"to_input":"Position"` works. A bad
  pin name lists the available ones.
- More outputs: `SubsurfaceColor` (with `shading_model` MSM_SUBSURFACE / MSM_PREINTEGRATED_SKIN), `Refraction`,
  `PixelDepthOffset`, `Anisotropy`, `Tangent`, `ShadingModel`, `Displacement`, `SurfaceThickness`, `CustomizedUV0`..`7`,
  `FrontMaterial` (Substrate, when `r.Substrate` is on; `material_inspect` reports `substrate`).
- Clear coat (car paint, lacquer): `settings={"shading_model":"MSM_CLEAR_COAT"}`, then
  `{"op":"output","from":"cc","property":"ClearCoat"}` and `"ClearCoatRoughness"` (UE calls them CustomData0 / 1).
  Python cannot see these or the other hidden outputs (PixelDepthOffset, ShadingModel, ...). `material_edit` connects them
  through the plugin, so there's no need to route them through MakeMaterialAttributes.
- Custom (HLSL) nodes: `"properties":{"code":"...","output_type":"CMOT_FLOAT3","inputs":["UV0",{"input_name":"Night"}],
  "additional_outputs":[{"output_name":"Win","output_type":"float1"}]}`, then connect to `to_input:"UV0"` and from
  `from_output:"Win"`.
- A texture parameter with no texture gets an engine default that matches its `sampler_type` (Normal, LinearColor,
  Masks, Grayscale). Otherwise it compiles against the sRGB DefaultTexture and fails.
- Any failed op makes `success` false. `material_instance` checks names against the parent and reads every value
  back: an unknown parameter or a missing parent is an error, not a silent no-op.
- Recreating a parent at the same path (delete + create) strands its instances. `material_edit(create/clear)`
  reparents the instances it finds (`reparented_instances`), and `material_instance` re-resolves its parent by
  path every time.

## Python equivalents (for reference)

## Quick color on an actor
`set_mesh_material_color` / `apply_material_to_actor` for simple cases.

## New material with an expression graph
```python
tools = unreal.AssetToolsHelpers.get_asset_tools()
mel = unreal.MaterialEditingLibrary
mat = tools.create_asset("M_Glow", "/Game/Materials", unreal.Material, unreal.MaterialFactoryNew())
color = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -400, 0)
color.set_editor_property("parameter_name", "Color")
color.set_editor_property("default_value", unreal.LinearColor(0.1, 0.6, 1.0, 1.0))
power = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -400, 200)
power.set_editor_property("parameter_name", "Glow")
power.set_editor_property("default_value", 5.0)
mul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -200, 100)
mel.connect_material_expressions(color, "", mul, "A")
mel.connect_material_expressions(power, "", mul, "B")
mel.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)
mel.connect_material_property(mul, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
mel.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat.get_path_name())
result = mat.get_path_name()
```

## Material instance
```python
tools = unreal.AssetToolsHelpers.get_asset_tools()
mi = tools.create_asset("MI_Glow_Red", "/Game/Materials", unreal.MaterialInstanceConstant,
                        unreal.MaterialInstanceConstantFactoryNew())
mel = unreal.MaterialEditingLibrary
mel.set_material_instance_parent(mi, unreal.load_asset("/Game/Materials/M_Glow"))
mel.set_material_instance_vector_parameter_value(mi, "Color", unreal.LinearColor(1, 0.1, 0.05, 1))
mel.update_material_instance(mi)
result = mi.get_path_name()
```
Apply it with `apply_material_to_actor`, or `property_set` on `StaticMeshComponent.OverrideMaterials`.

## Niagara: place an existing system
```python
sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
system = unreal.load_asset("/Game/FX/NS_Fire")   # find real paths via the asset registry
actor = sub.spawn_actor_from_object(system, unreal.Vector(0, 0, 100))
result = actor.get_actor_label()
```
Prefer `niagara_edit` (spawn + User parameters) for placement. To **author** Niagara systems (emitters, modules,
inputs, curves, renderers, events) use `niagara_export` / `niagara_apply`; recipes and tuning rules are in `skill://vfx`.

## Verify
`viewport_capture` after `editor_action(action="focus_actor", ...)`. Shader compiles are asynchronous: call
`wait_for_shaders` before capturing, or the capture shows the old shaders or the default checker.
