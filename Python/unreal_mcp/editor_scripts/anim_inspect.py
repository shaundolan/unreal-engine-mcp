# Read animation assets: list a folder (sequences, montages, blendspaces, AnimBPs, skeletons, IK rigs,
# retargeters, filtered by skeleton) or describe one asset in detail.
# ARGS: asset (path) | folder (path, recursive), skeleton (filter), class_filter, limit
# ruff: noqa: F821
_lib = unreal.AnimationLibrary
_KINDS = ("AnimSequence", "AnimMontage", "AnimComposite", "BlendSpace", "BlendSpace1D", "AimOffsetBlendSpace",
          "AimOffsetBlendSpace1D", "AnimBlueprint", "Skeleton", "SkeletalMesh", "IKRigDefinition", "IKRetargeter",
          "PoseAsset")


def _path(o):
    return o.get_path_name() if o is not None else None


def _skeleton(o):
    try:
        if isinstance(o, unreal.SkeletalMesh):
            return o.get_editor_property("skeleton")
        if hasattr(o, "get_skeleton"):
            return o.get_skeleton()
        return o.get_editor_property("skeleton")
    except Exception:
        return None


def _notifies(seq):
    out = []
    for ev in _lib.get_animation_notify_events(seq):
        rec = {"name": str(ev.get_editor_property("notify_name")),
               "time": round(_lib.get_anim_notify_event_trigger_time(ev), 4)}
        n = ev.get_editor_property("notify")
        s = ev.get_editor_property("notify_state_class")
        if n is not None:
            rec["class"] = n.get_class().get_name()
        if s is not None:
            rec["class"] = s.get_class().get_name()
            rec["duration"] = round(_lib.get_anim_notify_event_duration(ev), 4)
        out.append(rec)
    return out


def _sequence(seq):
    out = {"length": round(_lib.get_sequence_length(seq), 4), "rate_scale": _lib.get_rate_scale(seq),
           "notifies": _notifies(seq),
           "curves": [str(c) for c in _lib.get_animation_curve_names(seq, unreal.RawCurveTrackTypes.RCT_FLOAT)]}
    if isinstance(seq, unreal.AnimSequence):
        out.update(frames=_lib.get_num_frames(seq), root_motion=_lib.is_root_motion_enabled(seq),
                   root_lock=enum_name(_lib.get_root_motion_lock_type(seq)),
                   force_root_lock=_lib.is_root_motion_lock_forced(seq))
    return out


def _montage(m):
    out = _sequence(m)
    slots = []
    for t in m.get_editor_property("slot_anim_tracks"):
        segs = []
        for s in t.get_editor_property("anim_track").get_editor_property("anim_segments"):
            segs.append({"sequence": _path(s.get_editor_property("anim_reference")),
                         "start_pos": round(s.get_editor_property("start_pos"), 4),
                         "play_rate": s.get_editor_property("anim_play_rate")})
        slots.append({"slot": str(t.get_editor_property("slot_name")), "segments": segs})
    out["slots"] = slots
    out["sections"] = [str(m.get_section_name(i)) for i in range(m.get_num_sections())]
    return out


def _blendspace(bs):
    is_1d = isinstance(bs, unreal.BlendSpace1D)
    axes = []
    for i, p in enumerate(list(bs.get_editor_property("blend_parameters"))[: 1 if is_1d else 2]):
        axes.append({"axis": "xy"[i], "name": str(p.get_editor_property("display_name")),
                     "min": p.get_editor_property("min"), "max": p.get_editor_property("max"),
                     "grid": p.get_editor_property("grid_num")})
    samples = []
    for s in bs.get_editor_property("sample_data"):
        v = s.get_editor_property("sample_value")
        samples.append({"animation": _path(s.get_editor_property("animation")), "x": round(v.x, 3), "y": round(v.y, 3)})
    return {"dimensions": 1 if is_1d else 2, "axes": axes, "samples": samples}


def _ik_rig(rig):
    ctrl = unreal.IKRigController.get_controller(rig)
    chains = []
    for c in ctrl.get_retarget_chains():
        chains.append({"name": str(c.get_editor_property("chain_name")),
                       "start": str(c.get_editor_property("start_bone").get_editor_property("bone_name")),
                       "end": str(c.get_editor_property("end_bone").get_editor_property("bone_name"))})
    return {"mesh": _path(ctrl.get_skeletal_mesh()), "retarget_root": str(ctrl.get_retarget_root()),
            "chains": chains, "solvers": ctrl.get_num_solvers()}


def _retargeter(rtg):
    ctrl = unreal.IKRetargeterController.get_controller(rtg)
    return {"source_rig": _path(ctrl.get_ik_rig(unreal.RetargetSourceOrTarget.SOURCE)),
            "target_rig": _path(ctrl.get_ik_rig(unreal.RetargetSourceOrTarget.TARGET)),
            "ops": ctrl.get_num_retarget_ops()}


def _describe(obj):
    cls = obj.get_class().get_name()
    out = {"path": obj.get_path_name(), "class": cls, "skeleton": _path(_skeleton(obj))}
    if isinstance(obj, unreal.AnimMontage):
        out.update(_montage(obj))
    elif isinstance(obj, unreal.AnimSequenceBase):
        out.update(_sequence(obj))
    elif isinstance(obj, unreal.BlendSpace):
        out.update(_blendspace(obj))
    elif isinstance(obj, unreal.IKRigDefinition):
        out.update(_ik_rig(obj))
    elif isinstance(obj, unreal.IKRetargeter):
        out.update(_retargeter(obj))
    elif isinstance(obj, unreal.Skeleton):
        out["curves"] = [str(c) for c in _lib.get_curve_meta_data_names(obj)]
        out["preview_mesh"] = _path(obj.get_preview_mesh()) if hasattr(obj, "get_preview_mesh") else None
    elif isinstance(obj, unreal.AnimBlueprint):
        out["skeleton"] = _path(obj.get_editor_property("target_skeleton"))
        out["hint"] = "animbp_export reads the AnimGraph (state machines, states, transitions)"
    return out


if ARGS.get("asset"):
    _obj = unreal.load_asset(ARGS["asset"])
    if _obj is None:
        raise LookupError(f"asset {ARGS['asset']!r} not found")
    result = _describe(_obj)
else:
    _folder = ARGS.get("folder") or "/Game"
    _want = (ARGS.get("class_filter") or "").lower()
    _skel = (ARGS.get("skeleton") or "").split(".")[0]
    _limit = int(ARGS.get("limit") or 200)
    _items = []
    for _ad in asset_registry().get_assets_by_path(_folder, recursive=True):
        _s = asset_summary(_ad)
        if _s["class"] not in _KINDS or (_want and _s["class"].lower() != _want):
            continue
        if _skel:
            _o = unreal.load_asset(_s["path"])
            _sk = _skeleton(_o) if _o is not None else None
            if _sk is None or _sk.get_path_name().split(".")[0] != _skel:
                continue
        _items.append(_s)
        if len(_items) >= _limit:
            break
    result = {"folder": _folder, "count": len(_items), "assets": _items}
