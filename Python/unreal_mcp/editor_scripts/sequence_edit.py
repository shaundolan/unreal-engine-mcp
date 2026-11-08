# Create / edit a Level Sequence. ARGS: path, create, fps, duration_s, open, operations [...]
#   {op: bind, id, actor: label}                            possess a level actor
#   {op: spawn_camera, id, label, location, rotation, focal_length}   new CineCameraActor + binding
#   {op: transform_keys, binding: id, keys: [{t: seconds, location, rotation, scale}]}
#   {op: camera_cut, binding: id, start_s, end_s}
# ruff: noqa: F821
_path = ARGS["path"]
_eal = unreal.EditorAssetLibrary
_seq = unreal.load_asset(_path) if _eal.does_asset_exist(_path) else None
_created = False
if _seq is None:
    if not ARGS.get("create"):
        raise LookupError(f"sequence {_path!r} not found (pass create=true)")
    _pkg, _name = _path.rsplit("/", 1)
    _seq = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        _name.split(".")[0], _pkg, unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    _created = True

_fps = int(ARGS.get("fps") or 30)
if _created or ARGS.get("fps"):
    _seq.set_display_rate(unreal.FrameRate(_fps, 1))
if ARGS.get("duration_s") is not None:
    _seq.set_playback_start_seconds(0.0)
    _seq.set_playback_end_seconds(float(ARGS["duration_s"]))
_end_s = float(ARGS.get("duration_s") or 10.0)


def _frame(seconds):
    return unreal.FrameNumber(int(round(float(seconds) * _fps)))


def _add_track(owner, cls):
    # Sequences: add_track (5.2+) or add_master_track (older); bindings: add_track.
    for name in ("add_track", "add_master_track"):
        fn = getattr(owner, name, None)
        if fn is not None:
            return fn(cls)
    raise AttributeError(f"cannot add track to {owner}")


def _binding_id(binding):
    attempts = [
        lambda: _seq.get_portable_binding_id(_seq, binding),
        lambda: _seq.get_binding_id(binding),
        lambda: unreal.MovieSceneSequenceExtensions.get_binding_id(_seq, binding),
    ]
    last = None
    for f in attempts:
        try:
            return f()
        except Exception as e:  # API name differs across engine versions
            last = e
    raise RuntimeError(f"could not get binding id: {last}")


def _channels(section):
    fn = getattr(section, "get_all_channels", None)
    if fn is not None:
        return list(fn())
    return list(section.get_channels_by_type(unreal.MovieSceneScriptingDoubleChannel))


_bindings = {}
_results = []
for _i, _op in enumerate(ARGS.get("operations") or []):
    _kind = _op.get("op")
    try:
        if _kind == "bind":
            _bindings[_op["id"]] = _seq.add_possessable(require_actor(_op["actor"]))
            _r = {"bound": _op["actor"]}
        elif _kind == "spawn_camera":
            _cam = actor_sub().spawn_actor_from_class(unreal.CineCameraActor, vec(_op.get("location")),
                                                      rot(_op.get("rotation")))
            if _op.get("label"):
                _cam.set_actor_label(_op["label"])
            if _op.get("focal_length"):
                _cam.get_cine_camera_component().set_editor_property("current_focal_length",
                                                                     float(_op["focal_length"]))
            _bindings[_op["id"]] = _seq.add_possessable(_cam)
            _r = {"camera": _cam.get_actor_label()}
        elif _kind == "transform_keys":
            _b = _bindings[_op["binding"]]
            _track = _add_track(_b, unreal.MovieScene3DTransformTrack)
            _sec = _track.add_section()
            _sec.set_range_seconds(0.0, _end_s)
            _ch = _channels(_sec)  # loc x,y,z | rot roll,pitch,yaw | scale x,y,z
            for _k in _op.get("keys") or []:
                _f = _frame(_k["t"])
                if _k.get("location") is not None:
                    _l = vec(_k["location"])
                    for _c, _v in zip(_ch[0:3], (_l.x, _l.y, _l.z), strict=True):
                        _c.add_key(_f, _v)
                if _k.get("rotation") is not None:
                    _ro = rot(_k["rotation"])
                    for _c, _v in zip(_ch[3:6], (_ro.roll, _ro.pitch, _ro.yaw), strict=True):
                        _c.add_key(_f, _v)
                if _k.get("scale") is not None:
                    _s = vec(_k["scale"])
                    for _c, _v in zip(_ch[6:9], (_s.x, _s.y, _s.z), strict=True):
                        _c.add_key(_f, _v)
            _r = {"keys": len(_op.get("keys") or [])}
        elif _kind == "camera_cut":
            _track = _add_track(_seq, unreal.MovieSceneCameraCutTrack)
            _sec = _track.add_section()
            _sec.set_range_seconds(float(_op.get("start_s", 0.0)), float(_op.get("end_s", _end_s)))
            _sec.set_camera_binding_id(_binding_id(_bindings[_op["binding"]]))
            _r = {"cut": [_op.get("start_s", 0.0), _op.get("end_s", _end_s)]}
        else:
            raise ValueError(f"unknown op {_kind!r}")
        _results.append({"index": _i, "ok": True, **_r})
    except KeyError as _e:
        _results.append({"index": _i, "ok": False, "error": f"unknown binding id {_e} (bind it in this call)"})
    except Exception as _e:
        _results.append({"index": _i, "ok": False, "error": f"{type(_e).__name__}: {_e}"})

_eal.save_loaded_asset(_seq, only_if_is_dirty=False)
if ARGS.get("open"):
    unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(_seq)
result = {"path": _seq.get_path_name(), "created": _created, "fps": _fps,
          "succeeded": sum(1 for r in _results if r["ok"]), "failed": [r for r in _results if not r["ok"]]}
