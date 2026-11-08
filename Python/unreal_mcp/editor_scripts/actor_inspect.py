# One actor in depth. ARGS: target, properties [names to read], include_components (bool)
# ruff: noqa: F821
_a = require_actor(ARGS["target"])
_out = actor_summary(_a, ("class", "location", "rotation", "scale", "tags", "folder", "bounds"))
_out["hidden_in_editor"] = bool(_a.is_hidden_ed()) if hasattr(_a, "is_hidden_ed") else None

if ARGS.get("include_components", True):
    _comps = []
    for _c in _a.get_components_by_class(unreal.ActorComponent):
        _info = {"name": _c.get_name(), "class": class_name_of(_c)}
        if isinstance(_c, unreal.StaticMeshComponent):
            _m = _c.static_mesh
            _info["static_mesh"] = _m.get_path_name() if _m else None
            _info["materials"] = [(_x.get_path_name() if _x else None) for _x in _c.get_materials()]
        if isinstance(_c, unreal.SceneComponent):
            _info["relative_location"] = v3(_c.relative_location)
        if isinstance(_c, unreal.LightComponent):
            _info["intensity"] = _c.intensity
        _comps.append(_info)
    _out["components"] = _comps

_props = {}
for _p in ARGS.get("properties") or []:
    try:
        _obj, _name = _a, _p
        if "." in _p:
            _head, _name = _p.split(".", 1)
            _obj = _a.get_editor_property(_head)
        _props[_p] = _obj.get_editor_property(_name)
    except Exception as _e:  # report per property, keep going
        _props[_p] = f"<error: {_e}>"
if _props:
    _out["properties"] = _props
result = _out
