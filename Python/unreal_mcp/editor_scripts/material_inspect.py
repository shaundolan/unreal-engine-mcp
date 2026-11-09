# Material / material instance overview. ARGS: path
# ruff: noqa: F821
_mel = unreal.MaterialEditingLibrary
_m = unreal.load_asset(ARGS["path"])
if _m is None:
    raise LookupError(f"asset {ARGS['path']!r} not found")

_out = {"path": _m.get_path_name(), "class": class_name_of(_m)}
_params = {}
if isinstance(_m, unreal.MaterialInstance):
    _parent = _m.get_editor_property("parent")
    _out["parent"] = _parent.get_path_name() if _parent else None
    for _n in _mel.get_scalar_parameter_names(_m):
        _params[str(_n)] = {"type": "scalar", "value": _mel.get_material_instance_scalar_parameter_value(_m, _n)}
    for _n in _mel.get_vector_parameter_names(_m):
        _params[str(_n)] = {"type": "vector",
                            "value": _mel.get_material_instance_vector_parameter_value(_m, _n)}
    for _n in _mel.get_texture_parameter_names(_m):
        _t = _mel.get_material_instance_texture_parameter_value(_m, _n)
        _params[str(_n)] = {"type": "texture", "value": _t.get_path_name() if _t else None}
    for _n in _mel.get_static_switch_parameter_names(_m):
        _params[str(_n)] = {"type": "static_switch",
                            "value": _mel.get_material_instance_static_switch_parameter_value(_m, _n)}
else:
    for _kind, _fn in (("scalar", _mel.get_scalar_parameter_names), ("vector", _mel.get_vector_parameter_names),
                       ("texture", _mel.get_texture_parameter_names),
                       ("static_switch", _mel.get_static_switch_parameter_names)):
        for _n in _fn(_m):
            _params[str(_n)] = {"type": _kind}
    _out["expression_count"] = _mel.get_num_material_expressions(_m)
    _inputs = {}
    if mcp_lib() is not None:
        # Every output, including the ones Python cannot see (ClearCoat = CustomData0, PixelDepthOffset, ...).
        for _line in mcp_lib().get_connected_material_outputs(_m):
            _o, _ename = (str(_line).split("\t") + [""])[:2]
            _key = "MP_" + re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", _o).upper()  # CustomData0 -> MP_CUSTOM_DATA0
            _inputs[_key] = re.sub(r"_\d+$", "", _ename)  # MaterialExpressionMultiply_0 -> its class name
        _out["substrate"] = bool(mcp_lib().is_substrate_enabled())
    else:
        for _prop in ("MP_BASE_COLOR", "MP_METALLIC", "MP_SPECULAR", "MP_ROUGHNESS", "MP_EMISSIVE_COLOR",
                      "MP_OPACITY", "MP_OPACITY_MASK", "MP_NORMAL", "MP_AMBIENT_OCCLUSION", "MP_WORLD_POSITION_OFFSET"):
            _node = _mel.get_material_property_input_node(_m, getattr(unreal.MaterialProperty, _prop))
            if _node is not None:
                _inputs[_prop] = class_name_of(_node)
    _out["connected_outputs"] = _inputs
    for _k in ("blend_mode", "shading_model", "two_sided", "material_domain"):
        try:
            _out[_k] = str(_m.get_editor_property(_k))
        except Exception:
            pass
_out["parameters"] = _params
_out["textures_used"] = [t.get_path_name() for t in _mel.get_used_textures(_m)] if hasattr(_mel, "get_used_textures") else []
result = _out
