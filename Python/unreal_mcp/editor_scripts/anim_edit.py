# Animation asset edits that Python reaches: sequence settings, curves, BlendSpaces, IK Rigs,
# IK Retargeters and batch retargeting. (Montages and notifies go through the C++ anim_asset_apply.)
# ARGS: operations [{op, ...}]; each op is its own undo step. Result: {results: [{index, op, ok, ...}]}
# ruff: noqa: F821
_lib = unreal.AnimationLibrary
_eal = unreal.EditorAssetLibrary
_tools = unreal.AssetToolsHelpers.get_asset_tools()


def _load(path, kind=None):
    if not path:
        raise ValueError("missing asset path")
    obj = unreal.load_asset(path)
    if obj is None:
        raise LookupError(f"asset {path!r} not found")
    if kind is not None and not isinstance(obj, kind):
        raise TypeError(f"{path!r} is a {obj.get_class().get_name()}, not a {kind.__name__}")
    return obj


def _skeleton_of(path):
    obj = _load(path)
    if isinstance(obj, unreal.Skeleton):
        return obj, None
    if isinstance(obj, unreal.SkeletalMesh):
        return obj.get_editor_property("skeleton"), obj
    if hasattr(obj, "get_skeleton"):
        return obj.get_skeleton(), None
    raise TypeError(f"{path!r} has no skeleton")


def _split(path):
    pkg, name = path.rsplit("/", 1)
    return pkg, name.split(".")[0]


def _create(path, cls, factory):
    if _eal.does_asset_exist(path):
        raise FileExistsError(f"{path} already exists")
    pkg, name = _split(path)
    asset = _tools.create_asset(name, pkg, cls, factory)
    if asset is None:
        raise RuntimeError(f"could not create {path}")
    return asset


def _enum(enum_cls, value):
    member = getattr(enum_cls, str(value).upper().replace(" ", "_"), None)
    if member is None:
        raise ValueError(f"{value!r} is not one of {[m for m in dir(enum_cls) if m.isupper()]}")
    return member


def _save(asset):
    if not _eal.save_loaded_asset(asset, only_if_is_dirty=False):
        raise OSError(f"could not save {asset.get_path_name()} (read-only file? see editor_log)")


def op_set_sequence(op):
    seq = _load(op.get("asset"), unreal.AnimSequence)
    done = {}
    if "rate_scale" in op:
        _lib.set_rate_scale(seq, float(op["rate_scale"]))
        done["rate_scale"] = _lib.get_rate_scale(seq)
    if "root_motion" in op:
        _lib.set_root_motion_enabled(seq, bool(op["root_motion"]))
        done["root_motion"] = _lib.is_root_motion_enabled(seq)
    if "root_lock" in op:
        _lib.set_root_motion_lock_type(seq, _enum(unreal.RootMotionRootLock, op["root_lock"]))
        done["root_lock"] = str(op["root_lock"])
    if "force_root_lock" in op:
        _lib.set_is_root_motion_lock_forced(seq, bool(op["force_root_lock"]))
        done["force_root_lock"] = bool(op["force_root_lock"])
    if "interpolation" in op:
        _lib.set_animation_interpolation_type(seq, _enum(unreal.AnimInterpolationType, op["interpolation"]))
        done["interpolation"] = str(op["interpolation"])
    if "loop" in op:  # sequences loop by default in players; the asset flag is bLoop on AnimSequence
        seq.set_editor_property("loop", bool(op["loop"]))
        done["loop"] = bool(op["loop"])
    if not done:
        raise ValueError("set_sequence needs rate_scale, root_motion, root_lock, force_root_lock, interpolation or loop")
    _save(seq)
    return {"asset": seq.get_path_name(), "set": done}


def op_add_curve(op):
    seq = _load(op.get("asset"), unreal.AnimSequenceBase)
    name = op.get("name")
    if not name:
        raise ValueError("add_curve needs name")
    if not _lib.does_curve_exist(seq, name, unreal.RawCurveTrackTypes.RCT_FLOAT):
        _lib.add_curve(seq, name, unreal.RawCurveTrackTypes.RCT_FLOAT, bool(op.get("metadata", False)))
    keys = op.get("keys") or []  # [[time, value], ...]
    if keys:
        _lib.add_float_curve_keys(seq, name, [float(k[0]) for k in keys], [float(k[1]) for k in keys])
    _save(seq)
    return {"asset": seq.get_path_name(), "curve": name, "keys": len(keys)}


def _axis(bs, index, spec):
    params = list(bs.get_editor_property("blend_parameters"))
    p = params[index]
    if "name" in spec:
        p.set_editor_property("display_name", str(spec["name"]))
    if "min" in spec:
        p.set_editor_property("min", float(spec["min"]))
    if "max" in spec:
        p.set_editor_property("max", float(spec["max"]))
    if "grid" in spec:
        p.set_editor_property("grid_num", int(spec["grid"]))
    if "snap" in spec:
        p.set_editor_property("snap_to_grid", bool(spec["snap"]))
    if "wrap" in spec:
        p.set_editor_property("wrap_input", bool(spec["wrap"]))
    params[index] = p
    bs.set_editor_property("blend_parameters", params)


def _samples(bs, samples, replace):
    current = [] if replace else list(bs.get_editor_property("sample_data"))
    for s in samples:
        anim = _load(s.get("animation"), unreal.AnimSequence)
        sample = unreal.BlendSample()
        sample.set_editor_property("animation", anim)
        sample.set_editor_property("sample_value", unreal.Vector(float(s.get("x", 0)), float(s.get("y", 0)), 0.0))
        if "rate_scale" in s:
            sample.set_editor_property("rate_scale", float(s["rate_scale"]))
        current.append(sample)
    bs.set_editor_property("sample_data", current)
    return len(current)


def _blendspace_summary(bs):
    axes = []
    is_1d = isinstance(bs, unreal.BlendSpace1D)
    for i, p in enumerate(list(bs.get_editor_property("blend_parameters"))[: 1 if is_1d else 2]):
        axes.append({"axis": "xy"[i], "name": str(p.get_editor_property("display_name")),
                     "min": p.get_editor_property("min"), "max": p.get_editor_property("max"),
                     "grid": p.get_editor_property("grid_num")})
    samples = []
    for s in bs.get_editor_property("sample_data"):
        v = s.get_editor_property("sample_value")
        a = s.get_editor_property("animation")
        samples.append({"animation": a.get_path_name() if a else None, "x": round(v.x, 3), "y": round(v.y, 3)})
    return {"path": bs.get_path_name(), "class": bs.get_class().get_name(), "dimensions": 1 if is_1d else 2,
            "axes": axes, "samples": samples}


def op_create_blendspace(op):
    skeleton, mesh = _skeleton_of(op.get("skeleton") or "")
    dims = int(op.get("dimensions") or (len(op.get("axes") or []) or 1))
    if dims == 1:
        factory, cls = unreal.BlendSpaceFactory1D(), unreal.BlendSpace1D
    elif dims == 2:
        factory, cls = unreal.BlendSpaceFactoryNew(), unreal.BlendSpace
    else:
        raise ValueError("dimensions must be 1 or 2")
    factory.set_editor_property("target_skeleton", skeleton)
    if mesh is not None:
        factory.set_editor_property("preview_skeletal_mesh", mesh)
    bs = _create(op["asset"], cls, factory)
    return _edit_blendspace(bs, op, replace=True)


def _edit_blendspace(bs, op, replace):
    for i, spec in enumerate(op.get("axes") or []):
        _axis(bs, i, spec)
    if op.get("samples") is not None:
        _samples(bs, op["samples"], replace or bool(op.get("replace_samples")))
    _save(bs)
    return _blendspace_summary(bs)


def op_set_blendspace(op):
    return _edit_blendspace(_load(op.get("asset"), unreal.BlendSpace), op, replace=False)


def op_create_ik_rig(op):
    mesh = _load(op.get("mesh"), unreal.SkeletalMesh)
    rig = _create(op["asset"], unreal.IKRigDefinition, unreal.IKRigDefinitionFactory())
    ctrl = unreal.IKRigController.get_controller(rig)
    ctrl.set_skeletal_mesh(mesh)
    out = {"path": rig.get_path_name(), "mesh": mesh.get_path_name()}
    if op.get("auto", True):
        out["auto_retarget_definition"] = bool(ctrl.apply_auto_generated_retarget_definition())
        if op.get("auto_fbik", False):
            out["auto_fbik"] = bool(ctrl.apply_auto_fbik())
    for c in op.get("chains") or []:
        ctrl.add_retarget_chain(c["name"], c["start"], c["end"], c.get("goal", ""))
    if op.get("retarget_root"):
        ctrl.set_retarget_root(op["retarget_root"])
    out["retarget_root"] = str(ctrl.get_retarget_root())
    out["chains"] = [str(c.get_editor_property("chain_name")) for c in ctrl.get_retarget_chains()]
    _save(rig)
    return out


def op_create_retargeter(op):
    src = _load(op.get("source_rig"), unreal.IKRigDefinition)
    dst = _load(op.get("target_rig"), unreal.IKRigDefinition)
    rtg = _create(op["asset"], unreal.IKRetargeter, unreal.IKRetargetFactory())
    ctrl = unreal.IKRetargeterController.get_controller(rtg)
    ctrl.set_ik_rig(unreal.RetargetSourceOrTarget.SOURCE, src)
    ctrl.set_ik_rig(unreal.RetargetSourceOrTarget.TARGET, dst)
    if ctrl.get_num_retarget_ops() == 0:
        ctrl.add_default_ops()
    ctrl.assign_ik_rig_to_all_ops(unreal.RetargetSourceOrTarget.SOURCE, src)
    ctrl.assign_ik_rig_to_all_ops(unreal.RetargetSourceOrTarget.TARGET, dst)
    if op.get("auto_map", True):
        ctrl.auto_map_chains(_enum(unreal.AutoMapChainType, op.get("map_type", "fuzzy")), True)
    if op.get("auto_align", False):
        ctrl.auto_align_all_bones(unreal.RetargetSourceOrTarget.TARGET)
    _save(rtg)
    return {"path": rtg.get_path_name(), "source_rig": src.get_path_name(), "target_rig": dst.get_path_name(),
            "ops": ctrl.get_num_retarget_ops()}


def op_retarget(op):
    rtg = _load(op.get("retargeter"), unreal.IKRetargeter)
    src_mesh = _load(op.get("source_mesh"), unreal.SkeletalMesh)
    dst_mesh = _load(op.get("target_mesh"), unreal.SkeletalMesh)
    assets = []
    for p in op.get("assets") or []:
        ad = _eal.find_asset_data(p)
        if ad is None or not ad.is_valid():
            raise LookupError(f"asset {p!r} not found")
        assets.append(ad)
    if not assets:
        raise ValueError("retarget needs assets (animation sequences, montages, blendspaces or AnimBPs)")
    made = unreal.IKRetargetBatchOperation.duplicate_and_retarget(
        assets, src_mesh, dst_mesh, rtg, op.get("search", ""), op.get("replace", ""), op.get("prefix", ""),
        op.get("suffix", "" if op.get("target_path") else "_Retargeted"), op.get("target_path", ""),
        False, bool(op.get("include_referenced", True)), bool(op.get("overwrite", False)))
    return {"created": [f"{a.package_name}.{a.asset_name}" for a in made]}


_OPS = {
    "set_sequence": op_set_sequence,
    "add_curve": op_add_curve,
    "create_blendspace": op_create_blendspace,
    "set_blendspace": op_set_blendspace,
    "create_ik_rig": op_create_ik_rig,
    "create_retargeter": op_create_retargeter,
    "retarget": op_retarget,
}

_results = []
for _i, _op in enumerate(ARGS.get("operations") or []):
    _kind = _op.get("op")
    _rec = {"index": _op.get("_index", _i), "op": _kind}
    try:
        if _kind not in _OPS:
            raise ValueError(f"unknown op {_kind!r}")
        with unreal.ScopedEditorTransaction(f"MCP anim_edit {_kind}"):
            _rec.update(_OPS[_kind](_op))
        _rec["ok"] = True
    except Exception as _e:
        _rec.update(ok=False, error=f"{type(_e).__name__}: {_e}")
    _results.append(_rec)
result = {"results": _results}
