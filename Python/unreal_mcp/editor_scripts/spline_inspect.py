# Spline actors: points (world), length, closed, spline-mesh segments (mesh, count), bounds.
# ARGS: target (label; '' = every actor with a SplineComponent), max_points
# ruff: noqa: F821
_W = unreal.SplineCoordinateSpace.WORLD
_max = int(ARGS.get("max_points", 200))
_out = []
_actors = [require_actor(ARGS["target"])] if ARGS.get("target") else all_actors()
for _a in _actors:
    _sps = _a.get_components_by_class(unreal.SplineComponent)
    if not _sps:
        if ARGS.get("target"):
            raise LookupError(f"{ARGS['target']} has no SplineComponent")
        continue
    _sp = _sps[0]
    _n = _sp.get_number_of_spline_points()
    _segs = _a.get_components_by_class(unreal.SplineMeshComponent)
    _meshes = {}
    for _c in _segs:
        _m = _c.get_editor_property("static_mesh")
        _k = _m.get_path_name() if _m else ""
        _meshes[_k] = _meshes.get(_k, 0) + 1
    _o, _e = _a.get_actor_bounds(False)
    _out.append({
        "label": _a.get_actor_label(), "class": class_name_of(_a), "tags": [str(t) for t in _a.tags],
        "closed": _sp.is_closed_loop(), "length": round(_sp.get_spline_length(), 1), "point_count": _n,
        "points": [v3(_sp.get_location_at_spline_point(i, _W)) for i in range(min(_n, _max))],
        "segments": len(_segs), "meshes": _meshes,
        "bounds": {"origin": v3(_o), "extent": v3(_e)}})
result = {"splines": _out, "count": len(_out)}
