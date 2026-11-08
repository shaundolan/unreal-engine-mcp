---
name: material-vfx
description: >
  Materials and VFX with the local UnrealMCP tools: create materials and
  expression graphs, material instances, apply them, and place Niagara systems,
  all through execute_python. Use when creating or editing materials, material
  instances, or placing particle effects.
---

# Materials & VFX

There are no dedicated material tools yet (Phase 2). Use `execute_python` with `MaterialEditingLibrary`.
Look up exact names first: `unreal_api_search("material editing library connect")`.

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
Python can't author Niagara emitter/module graphs well. Duplicate a template system (`EditorAssetLibrary.duplicate_asset`) and change exposed **user parameters** instead.

## Verify
`viewport_capture` after `editor_action(action="focus_actor", ...)`. Shader compiles are asynchronous, so if the capture still shows the default checker, capture again after a few seconds.
