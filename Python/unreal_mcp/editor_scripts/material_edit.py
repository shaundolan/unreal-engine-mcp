# Build or edit a material graph in one call.
# ARGS: path (/Game/Materials/M_X; created if missing when create=true), create, clear (delete all
#       expressions first), settings {blend_mode, shading_model, two_sided, ...}, operations [...]:
#   {op: add, id, class: "MaterialExpressionMultiply" | "Multiply", x, y, properties {..}}
#   {op: connect, from: id, from_output: "", to: id, to_input: "A"}
#   {op: output, from: id, from_output: "", property: "BaseColor" | "MP_BASE_COLOR"}
# Expression ids are local to the call. Graphs are built whole: use clear=true to rebuild.
# ruff: noqa: F821
_mel = unreal.MaterialEditingLibrary
_path = ARGS["path"]
_m = unreal.load_asset(_path) if unreal.EditorAssetLibrary.does_asset_exist(_path) else None
if _m is None:
    if not ARGS.get("create"):
        raise LookupError(f"material {_path!r} not found (pass create=true to make it)")
    _pkg, _name = _path.rsplit("/", 1)
    _m = unreal.AssetToolsHelpers.get_asset_tools().create_asset(_name.split(".")[0], _pkg, unreal.Material,
                                                                  unreal.MaterialFactoryNew())

_PROPS = {"basecolor": "MP_BASE_COLOR", "metallic": "MP_METALLIC", "specular": "MP_SPECULAR",
          "roughness": "MP_ROUGHNESS", "emissive": "MP_EMISSIVE_COLOR", "emissivecolor": "MP_EMISSIVE_COLOR",
          "opacity": "MP_OPACITY", "opacitymask": "MP_OPACITY_MASK", "normal": "MP_NORMAL",
          "ambientocclusion": "MP_AMBIENT_OCCLUSION", "worldpositionoffset": "MP_WORLD_POSITION_OFFSET"}


def _prop(name):
    key = name if name.startswith("MP_") else _PROPS.get(name.replace("_", "").replace(" ", "").lower())
    if key is None or not hasattr(unreal.MaterialProperty, key):
        raise ValueError(f"unknown material output {name!r}")
    return getattr(unreal.MaterialProperty, key)


def _expr_class(name):
    full = name if name.startswith("MaterialExpression") else "MaterialExpression" + name
    cls = getattr(unreal, full, None)
    if cls is None:
        raise LookupError(f"no expression class {full!r} (try unreal_api_search)")
    return cls


_ids = {}
_results = []
with unreal.ScopedEditorTransaction("MCP material_edit"):
    if ARGS.get("clear"):
        _mel.delete_all_material_expressions(_m)
    if ARGS.get("settings"):
        for _k, _v in ARGS["settings"].items():
            _cur = _m.get_editor_property(_k)
            if isinstance(_v, str) and hasattr(type(_cur), _v.upper()):
                _v = getattr(type(_cur), _v.upper())
            _m.set_editor_property(_k, coerce(_cur, _v))
    for _i, _op in enumerate(ARGS.get("operations") or []):
        try:
            _kind = _op.get("op")
            if _kind == "add":
                _e = _mel.create_material_expression(_m, _expr_class(_op["class"]),
                                                     int(_op.get("x", -300)), int(_op.get("y", 0)))
                for _k, _v in (_op.get("properties") or {}).items():
                    _e.set_editor_property(_k, coerce(_e.get_editor_property(_k), _v))
                if _op.get("id"):
                    _ids[_op["id"]] = _e
                _results.append({"index": _i, "ok": True, "added": class_name_of(_e)})
            elif _kind == "connect":
                _ok = _mel.connect_material_expressions(_ids[_op["from"]], _op.get("from_output", ""),
                                                        _ids[_op["to"]], _op.get("to_input", ""))
                if not _ok:
                    raise RuntimeError(f"connect failed: check pin names ({_op.get('from_output')!r} -> "
                                       f"{_op.get('to_input')!r})")
                _results.append({"index": _i, "ok": True})
            elif _kind == "output":
                if not _mel.connect_material_property(_ids[_op["from"]], _op.get("from_output", ""),
                                                      _prop(_op["property"])):
                    raise RuntimeError("connect to material output failed")
                _results.append({"index": _i, "ok": True})
            else:
                raise ValueError(f"unknown op {_kind!r}; expected add, connect, output")
        except KeyError as _e:
            _results.append({"index": _i, "ok": False, "error": f"unknown expression id {_e}"})
        except Exception as _e:
            _results.append({"index": _i, "ok": False, "error": f"{type(_e).__name__}: {_e}"})

_mel.layout_material_expressions(_m)
_mel.recompile_material(_m)
unreal.EditorAssetLibrary.save_loaded_asset(_m, only_if_is_dirty=False)
result = {"path": _m.get_path_name(), "expression_count": _mel.get_num_material_expressions(_m),
          "failed": [r for r in _results if not r["ok"]], "succeeded": sum(1 for r in _results if r["ok"])}
