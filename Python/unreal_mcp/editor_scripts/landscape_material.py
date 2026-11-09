# Landscape material with weight-blended paint layers (one LandscapeLayerBlend input per layer).
# ARGS: path (/Game/.../M_Terrain), layers [{name, color [r,g,b], texture (optional /Game path), roughness}],
#       create (None = only when missing, True = rebuild, False = must exist), roughness (default), tiling (cm, textures)
# Returns {path, layers, created}. An existing material is used as-is with the layer names passed in.
# ruff: noqa: F821
_mel = unreal.MaterialEditingLibrary
_eal = unreal.EditorAssetLibrary
_path = ARGS["path"].split(".")[0]
_layers = [lay if isinstance(lay, dict) else {"name": str(lay)} for lay in ARGS.get("layers") or []]
_exists = _eal.does_asset_exist(_path)
_create = ARGS.get("create")
if _create is False and not _exists:
    raise LookupError(f"material {_path!r} not found")
_build = (not _exists) or bool(_create)
_names = []
if _build:
    if not _layers:
        raise ValueError("building a landscape material needs layers [{name, color}]")
    if _exists:
        _m = unreal.load_asset(_path)
        _mel.delete_all_material_expressions(_m)
    else:
        _pkg, _name = _path.rsplit("/", 1)
        _m = unreal.AssetToolsHelpers.get_asset_tools().create_asset(_name, _pkg, unreal.Material,
                                                                      unreal.MaterialFactoryNew())
    _blend = _mel.create_material_expression(_m, unreal.MaterialExpressionLandscapeLayerBlend, -400, 0)
    _inputs = []
    for _lay in _layers:
        _bi = unreal.LayerBlendInput()
        _bi.set_editor_property("layer_name", _lay["name"])
        _bi.set_editor_property("blend_type", unreal.LandscapeLayerBlendType.LB_WEIGHT_BLEND)
        _c = _lay.get("color") or [0.5, 0.5, 0.5]
        _bi.set_editor_property("const_layer_input", unreal.Vector(float(_c[0]), float(_c[1]), float(_c[2])))
        _bi.set_editor_property("preview_weight", 1.0 if not _inputs else 0.0)
        _inputs.append(_bi)
        _names.append(_lay["name"])
    _blend.set_editor_property("layers", _inputs)
    for _n, _lay in enumerate(_layers):
        if _lay.get("texture"):
            _tex = unreal.load_asset(_lay["texture"])
            if _tex is None:
                raise LookupError(f"texture {_lay['texture']!r} not found")
            _ts = _mel.create_material_expression(_m, unreal.MaterialExpressionTextureSample, -900, _n * 250)
            _ts.set_editor_property("texture", _tex)
            if ARGS.get("tiling"):
                _lc = _mel.create_material_expression(_m, unreal.MaterialExpressionLandscapeLayerCoords, -1200, _n * 250)
                _lc.set_editor_property("mapping_scale", float(ARGS["tiling"]) / 100.0)
                _mel.connect_material_expressions(_lc, "", _ts, "UVs")
            if not _mel.connect_material_expressions(_ts, "RGB", _blend, f"Layer {_lay['name']}") and \
                    not _mel.connect_material_expressions(_ts, "RGB", _blend, _lay["name"]):
                raise RuntimeError(f"could not connect texture to layer input {_lay['name']!r}")
    _mel.connect_material_property(_blend, "", unreal.MaterialProperty.MP_BASE_COLOR)
    _rough = _mel.create_material_expression(_m, unreal.MaterialExpressionConstant, -400, 300)
    _rough.set_editor_property("r", float(ARGS.get("roughness", 0.9)))
    _mel.connect_material_property(_rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    _mel.recompile_material(_m)
    _eal.save_loaded_asset(_m, only_if_is_dirty=False)
else:
    _m = unreal.load_asset(_path)
    if not isinstance(_m, unreal.MaterialInterface):
        raise TypeError(f"{_path!r} is not a material")
    # Existing material: use the layer names the caller passed (paint ops add any others they name).
    _names = [lay["name"] for lay in _layers]
result = {"path": _m.get_path_name(), "layers": _names, "created": _build}
