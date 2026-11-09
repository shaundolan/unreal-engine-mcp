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
    release_stale_path(_path)  # a force-deleted material left in memory would make create_asset fail
    _m = unreal.AssetToolsHelpers.get_asset_tools().create_asset(_name.split(".")[0], _pkg, unreal.Material,
                                                                  unreal.MaterialFactoryNew())
    if _m is None:
        raise RuntimeError(f"could not create material {_path!r} (see editor_log)")

_PROPS = {"basecolor": "MP_BASE_COLOR", "metallic": "MP_METALLIC", "specular": "MP_SPECULAR",
          "roughness": "MP_ROUGHNESS", "emissive": "MP_EMISSIVE_COLOR", "emissivecolor": "MP_EMISSIVE_COLOR",
          "opacity": "MP_OPACITY", "opacitymask": "MP_OPACITY_MASK", "normal": "MP_NORMAL",
          "ambientocclusion": "MP_AMBIENT_OCCLUSION", "ao": "MP_AMBIENT_OCCLUSION",
          "worldpositionoffset": "MP_WORLD_POSITION_OFFSET", "wpo": "MP_WORLD_POSITION_OFFSET",
          "subsurfacecolor": "MP_SUBSURFACE_COLOR", "subsurface": "MP_SUBSURFACE_COLOR",
          "refraction": "MP_REFRACTION", "anisotropy": "MP_ANISOTROPY", "tangent": "MP_TANGENT",
          "pixeldepthoffset": "MP_PIXEL_DEPTH_OFFSET", "shadingmodel": "MP_SHADING_MODEL",
          "customdata0": "MP_CUSTOM_DATA0", "clearcoat": "MP_CUSTOM_DATA0",
          "customdata1": "MP_CUSTOM_DATA1", "clearcoatroughness": "MP_CUSTOM_DATA1",
          "frontmaterial": "MP_FRONT_MATERIAL", "substrate": "MP_FRONT_MATERIAL",
          "materialattributes": "MP_MATERIAL_ATTRIBUTES"}

# Engine textures matching each sampler type: a parameter with no texture compiles against DefaultTexture (sRGB
# colour), which fails for Normal / Linear / Masks / Grayscale samplers ("Sampler type is Normal, should be Color").
_SAMPLER_DEFAULTS = {
    "SAMPLERTYPE_COLOR": "/Engine/EngineResources/DefaultTexture",
    "SAMPLERTYPE_LINEAR_COLOR": "/Engine/EngineMaterials/BaseFlattenLinearColor",
    "SAMPLERTYPE_NORMAL": "/Engine/EngineMaterials/DefaultNormal",
    "SAMPLERTYPE_GRAYSCALE": "/Engine/EngineMaterials/DefaultCalibrationGrayscale",
    "SAMPLERTYPE_LINEAR_GRAYSCALE": "/Engine/EngineMaterials/BaseFlattenGrayscaleMap",
    "SAMPLERTYPE_MASKS": "/Engine/EngineMaterials/DefaultDiffuse_TC_Masks",
}

# Arrays of structs on expressions, by property name: the element struct to build from dicts (or names).
_STRUCT_ARRAYS = {"inputs": ("CustomInput", "input_name"), "additional_outputs": ("CustomOutput", "output_name"),
                  "additional_defines": ("CustomDefine", "define_name")}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _prop(name):
    """The Python MaterialProperty for an output name, or None when Python cannot see it: UE marks CustomData0/1
    (ClearCoat, ClearCoatRoughness), PixelDepthOffset, ShadingModel, Displacement, SurfaceThickness and
    CustomizedUVs as hidden enum entries, so they are missing from unreal.MaterialProperty."""
    key = name if name.startswith("MP_") else _PROPS.get(_norm(name))
    if key is None:  # CamelCase -> MP_CAMEL_CASE for anything not aliased
        key = "MP_" + re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()
    return getattr(unreal.MaterialProperty, key, None)


# Outputs UE hides from Python's MaterialProperty (normalized names, aliases included).
_HIDDEN = {"customdata0", "customdata1", "clearcoat", "clearcoatroughness", "pixeldepthoffset", "pdo", "shadingmodel",
           "displacement", "surfacethickness", "frontmaterial", "substrate",
           *(f"customizeduv{i}" for i in range(8)), *(f"customizeduvs{i}" for i in range(8))}

_OUTPUTS_HELP = ("BaseColor, Metallic, Specular, Roughness, Anisotropy, EmissiveColor, Opacity, OpacityMask, Normal, "
                 "Tangent, WorldPositionOffset, SubsurfaceColor, ClearCoat, ClearCoatRoughness, AmbientOcclusion, "
                 "Refraction, PixelDepthOffset, ShadingModel, Displacement, SurfaceThickness, CustomizedUV0..7, "
                 "FrontMaterial (Substrate), MaterialAttributes")


def _connect_output(src, out, name):
    prop = _prop(name)
    if prop is not None:
        if not _mel.connect_material_property(src, out, prop):
            raise RuntimeError(f"connect to material output {name!r} failed (outputs {list(_mel.get_material_expression_output_names(src))})")
        return
    lib = mcp_lib()
    if lib is None:
        key = _norm(name[3:] if name.upper().startswith("MP_") else name)
        if key not in _HIDDEN:
            raise ValueError(f"unknown material output {name!r} ({_OUTPUTS_HELP})")
        raise ValueError(f"material output {name!r} is hidden from Python (UMETA(Hidden)); ClearCoat and the other "
                         "hidden outputs need the UnrealMCP plugin's MCPScriptLibrary: update the plugin, or route it "
                         "through MakeMaterialAttributes")
    err = str(lib.connect_material_output(src, out, name))
    if err:
        raise RuntimeError(err)


def _expr_class(name):
    full = name if name.startswith("MaterialExpression") else "MaterialExpression" + name
    cls = getattr(unreal, full, None)
    if cls is None:
        raise LookupError(f"no expression class {full!r} (try unreal_api_search)")
    return cls


def _pin(names, want, kind):
    """Match a pin by exact name, case/space-insensitive name, or a unique partial match: Noise's position input
    is called "World Position", so "Position" finds it."""
    names = [str(n) for n in names]
    want = "" if want is None else str(want)
    if want in names or (want == "" and names):
        return want
    w = _norm(want)
    hits = [n for n in names if _norm(n) == w] or [n for n in names if w and (_norm(n).endswith(w) or w in _norm(n))]
    if len(hits) == 1:
        return hits[0]
    raise LookupError(f"no {kind} pin {want!r}; available: {names}" if not hits else
                      f"{kind} pin {want!r} is ambiguous: {hits}")


def _connect(src, out, dst, inp):
    out_names = list(_mel.get_material_expression_output_names(src))
    in_names = list(_mel.get_material_expression_input_names(dst))
    o = _pin(out_names, out, "output") if out_names and out not in ("", None) else (out or "")
    i = _pin(in_names, inp, "input") if in_names else (inp or "")
    if not _mel.connect_material_expressions(src, o, dst, i):
        raise RuntimeError(f"connect failed ({out!r} -> {inp!r}); outputs {out_names}, inputs {in_names}")


def _struct_array(cls_name, key_field, value):
    cls = getattr(unreal, cls_name)
    out = []
    for item in value:
        s = cls()
        if isinstance(item, str):
            item = {key_field: item}
        for k, v in item.items():
            k = {"name": key_field, "type": "output_type"}.get(k, k)
            cur = s.get_editor_property(k)
            if k == "output_type" and isinstance(v, str) and not v.upper().startswith("CMOT_"):
                v = "CMOT_" + v.upper()  # "float1" -> CMOT_FLOAT1
            s.set_editor_property(k, coerce(cur, v))
        out.append(s)
    return out


def _set_expr_prop(e, k, v):
    cur = e.get_editor_property(k)
    if k in _STRUCT_ARRAYS and isinstance(v, list):
        v = _struct_array(*_STRUCT_ARRAYS[k], v)
    elif (isinstance(v, list) and v and isinstance(v[0], dict) and hasattr(cur, "__len__")
          and not isinstance(cur, str) and len(cur)):
        v = _struct_array(type(cur[0]).__name__, "name", v)  # any other array of structs
    else:
        v = coerce(cur, v)
    e.set_editor_property(k, v)


def _default_texture(e, props):
    """Give a texture parameter without a texture an engine default that matches its sampler type."""
    if "texture" in props:
        return None
    try:
        cur = e.get_editor_property("texture")
        st = enum_name(e.get_editor_property("sampler_type"))
    except Exception:  # not a texture expression
        return None
    if st == "SAMPLERTYPE_COLOR" or (cur is not None and "/EngineResources/DefaultTexture" not in cur.get_path_name()):
        return None
    path = _SAMPLER_DEFAULTS.get(st)
    tex = unreal.load_asset(path) if path else None
    if tex is None:
        return None
    sampler = e.get_editor_property("sampler_type")
    e.set_editor_property("texture", tex)
    e.set_editor_property("sampler_type", sampler)  # setting a texture re-derives the sampler type; keep the asked one
    return tex.get_path_name()


def _clear(m):
    """delete_all_material_expressions deletes while iterating the live expression array, so each pass removes only
    every other node (8 nodes -> 3 left) and rebuilds stacked duplicate parameters. Repeat until empty."""
    for _ in range(64):
        n = _mel.get_num_material_expressions(m)
        if n == 0:
            return 0
        _mel.delete_all_material_expressions(m)
        if _mel.get_num_material_expressions(m) >= n:
            break
    return _mel.get_num_material_expressions(m)


_ids = {}
_results = []
_left = 0
with unreal.ScopedEditorTransaction("MCP material_edit"):
    if ARGS.get("clear"):
        _left = _clear(_m)
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
                _props = _op.get("properties") or {}
                for _k, _v in _props.items():
                    _set_expr_prop(_e, _k, _v)
                if _op.get("id"):
                    _ids[_op["id"]] = _e
                _r = {"index": _i, "ok": True, "added": class_name_of(_e)}
                _tex = _default_texture(_e, _props)
                if _tex:
                    _r["default_texture"] = _tex
                _results.append(_r)
            elif _kind == "connect":
                _connect(_ids[_op["from"]], _op.get("from_output", ""), _ids[_op["to"]], _op.get("to_input", ""))
                _results.append({"index": _i, "ok": True})
            elif _kind == "output":
                _src = _ids[_op["from"]]
                _outs = list(_mel.get_material_expression_output_names(_src))
                _o = _op.get("from_output", "")
                _o = _pin(_outs, _o, "output") if _outs and _o else (_o or "")
                _connect_output(_src, _o, _op["property"])
                _results.append({"index": _i, "ok": True})
            else:
                raise ValueError(f"unknown op {_kind!r}; expected add, connect, output")
        except KeyError as _e:
            _results.append({"index": _i, "ok": False, "error": f"unknown expression id {_e}"})
        except Exception as _e:
            _results.append({"index": _i, "ok": False, "error": f"{type(_e).__name__}: {_e}"})

def _reparent_children(m):
    """Instances whose parent is this path but not this object: the material was deleted and recreated, which
    nulls (force delete) or strands (dead object) their parent. Point them at the live material again."""
    pkg = m.get_path_name().split(".")[0]
    opts = unreal.AssetRegistryDependencyOptions(include_soft_package_references=False, include_hard_package_references=True,
                                                 include_searchable_names=False, include_soft_management_references=False,
                                                 include_hard_management_references=False)
    fixed = []
    for ref in asset_registry().get_referencers(pkg, opts) or []:
        ref = str(ref)
        if not unreal.EditorAssetLibrary.does_asset_exist(ref):
            continue
        if str(unreal.EditorAssetLibrary.find_asset_data(ref).asset_class_path.asset_name) != "MaterialInstanceConstant":
            continue
        mi = unreal.load_asset(ref)
        cur = mi.get_editor_property("parent")
        if cur is m:
            continue
        if cur is not None and unreal.SystemLibrary.is_valid(cur) and cur.get_path_name().split(".")[0] != pkg:
            continue  # parented to something else now
        _mel.set_material_instance_parent(mi, m)
        _mel.update_material_instance(mi)
        unreal.EditorAssetLibrary.save_loaded_asset(mi, only_if_is_dirty=False)
        fixed.append(mi.get_path_name())
    return fixed


_mel.layout_material_expressions(_m)
_mel.recompile_material(_m)
unreal.EditorAssetLibrary.save_loaded_asset(_m, only_if_is_dirty=False)
_reparented = _reparent_children(_m) if (ARGS.get("create") or ARGS.get("clear")) else []
_failed = [r for r in _results if not r["ok"]]
result = {"path": _m.get_path_name(), "expression_count": _mel.get_num_material_expressions(_m),
          "failed": _failed, "succeeded": sum(1 for r in _results if r["ok"])}
if _left:
    result["clear_left"] = _left
if _reparented:
    result["reparented_instances"] = _reparented
if _failed or _left:
    result["success"] = False
    result["error"] = "; ".join([f"op {r['index']}: {r['error']}" for r in _failed][:6]
                                + ([f"clear left {_left} expressions"] if _left else []))
_defaults = {str(r["index"]): r["default_texture"] for r in _results if r.get("default_texture")}
if _defaults:
    result["default_textures"] = _defaults
del _m, _ids, _results
