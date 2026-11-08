# Static level performance audit. ARGS: top (int), triangle_budget (int)
# ruff: noqa: F821
_top = int(ARGS.get("top") or 15)
_budget = int(ARGS.get("triangle_budget") or 5_000_000)
_sme = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)

_mesh_tris = {}
_heavy = []
_total_tris = 0
_mesh_components = 0
_material_slots = 0
_unique_meshes = set()
_lights = {"static": 0, "stationary": 0, "movable": 0, "shadow_casting_movable": 0}
_issues = []

for _a in all_actors():
    for _c in _a.get_components_by_class(unreal.StaticMeshComponent):
        _m = _c.static_mesh
        if _m is None:
            continue
        _mesh_components += 1
        _material_slots += len(_c.get_materials())
        _p = _m.get_path_name()
        _unique_meshes.add(_p)
        if _p not in _mesh_tris:
            try:
                _mesh_tris[_p] = int(_sme.get_number_triangles(_m, 0)) if hasattr(_sme, "get_number_triangles") \
                    else int(_m.get_num_triangles(0))
            except Exception:
                _mesh_tris[_p] = 0
        _t = _mesh_tris[_p]
        _total_tris += _t
        _heavy.append((_t, _a.get_actor_label(), _p))
    for _l in _a.get_components_by_class(unreal.LightComponent):
        _mob = str(_l.get_editor_property("mobility")).split(".")[-1].lower()
        _key = "movable" if "movable" in _mob else "stationary" if "stationary" in _mob else "static"
        _lights[_key] += 1
        if _key == "movable" and _l.get_editor_property("cast_shadows"):
            _lights["shadow_casting_movable"] += 1

_heavy.sort(reverse=True)
if _total_tris > _budget:
    _issues.append(f"{_total_tris:,} triangles at LOD0 exceeds budget {_budget:,}: enable Nanite or add LODs")
if _lights["shadow_casting_movable"] > 4:
    _issues.append(f"{_lights['shadow_casting_movable']} movable shadow-casting lights: each costs shadow passes")
if _lights["stationary"] > 4:
    _issues.append("more than 4 overlapping stationary lights may fall back to dynamic shadows")
if _mesh_components > 5000:
    _issues.append(f"{_mesh_components} static mesh components: merge or use instancing (ISM/HISM)")
_dupes = {}
for _t, _lbl, _p in _heavy:
    _dupes[_p] = _dupes.get(_p, 0) + 1
_instancing = [{"mesh": p, "copies": n} for p, n in sorted(_dupes.items(), key=lambda kv: -kv[1]) if n >= 50][:10]
if _instancing:
    _issues.append("meshes placed 50+ times as separate actors are instancing candidates (see instancing_candidates)")

result = {
    "triangles_lod0": _total_tris, "mesh_components": _mesh_components, "unique_meshes": len(_unique_meshes),
    "material_slots": _material_slots, "lights": _lights,
    "heaviest": [{"triangles": t, "actor": lbl, "mesh": p} for t, lbl, p in _heavy[:_top]],
    "instancing_candidates": _instancing, "issues": _issues,
    "note": "static analysis; for frame timings run console_command('stat unit') during pie_test",
}
