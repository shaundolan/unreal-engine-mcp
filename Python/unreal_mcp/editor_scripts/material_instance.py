# Create or update a material instance. ARGS: path, parent (required when creating), parameters
#   {name: number | [r,g,b,a] | {"r":..} | "/Game/Tex.Tex" | true/false}
# ruff: noqa: F821
_mel = unreal.MaterialEditingLibrary
_path = ARGS["path"]
_mi = unreal.load_asset(_path) if unreal.EditorAssetLibrary.does_asset_exist(_path) else None
_created = False
if _mi is None:
    if not ARGS.get("parent"):
        raise ValueError("instance does not exist; pass parent to create it")
    _pkg, _name = _path.rsplit("/", 1)
    _mi = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        _name.split(".")[0], _pkg, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    _created = True
if ARGS.get("parent"):
    _parent = unreal.load_asset(ARGS["parent"])
    if _parent is None:
        raise LookupError(f"parent {ARGS['parent']!r} not found")
    _mel.set_material_instance_parent(_mi, _parent)

_set = []
_errors = {}
for _n, _v in (ARGS.get("parameters") or {}).items():
    try:
        if isinstance(_v, bool):
            _ok = _mel.set_material_instance_static_switch_parameter_value(_mi, _n, _v)
        elif isinstance(_v, (int, float)):
            _ok = _mel.set_material_instance_scalar_parameter_value(_mi, _n, float(_v))
        elif isinstance(_v, (list, dict)):
            _ok = _mel.set_material_instance_vector_parameter_value(_mi, _n, color(_v))
        elif isinstance(_v, str):
            _tex = unreal.load_asset(_v)
            if _tex is None:
                raise LookupError(f"texture {_v!r} not found")
            _ok = _mel.set_material_instance_texture_parameter_value(_mi, _n, _tex)
        else:
            raise TypeError(f"unsupported value {_v!r}")
        if _ok is False:
            raise LookupError("parameter not found on parent")
        _set.append(_n)
    except Exception as _e:
        _errors[_n] = f"{type(_e).__name__}: {_e}"

_mel.update_material_instance(_mi)
unreal.EditorAssetLibrary.save_loaded_asset(_mi, only_if_is_dirty=False)
result = {"path": _mi.get_path_name(), "created": _created, "set": _set, "errors": _errors}
