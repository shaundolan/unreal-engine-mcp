# Spline actors (SplineComponent + SplineMeshComponent segments) for roads, rivers, pipes, fences.
# include: _world
# ARGS: operations [...], dry_run, stop_on_error
#   create: {op, label, points [[x,y,z],...] (world), closed, point_type curve|linear|curve_clamped|constant,
#            snap_to_ground, ground_offset, mesh, mesh_scale [y,z] | number, forward_axis x|y|z,
#            segment_length (cm, default = mesh length), material, collision (bool), replace (bool)}
#   modify: {op, target, points | append, closed, point_type, snap_to_ground, mesh, mesh_scale, ...}  rebuilds meshes
#   carve:  {op, target, landscape, width, end_width, falloff, raise, lower, layer, subdivisions}
#   delete: {op, target}
# ruff: noqa: F821
_dry = bool(ARGS.get("dry_run"))
_WORLD = unreal.SplineCoordinateSpace.WORLD
_LOCAL = unreal.SplineCoordinateSpace.LOCAL
_TYPES = {"curve": "CURVE", "linear": "LINEAR", "curve_clamped": "CURVE_CLAMPED", "constant": "CONSTANT",
          "curve_custom_tangent": "CURVE_CUSTOM_TANGENT"}
_AXES = {"x": "X", "y": "Y", "z": "Z"}


def _spline_of(actor):
    comps = actor.get_components_by_class(unreal.SplineComponent)
    if not comps:
        raise LookupError(f"{actor.get_actor_label()} has no SplineComponent")
    return comps[0]


def _points(op, actor=None):
    pts = [vec(p) for p in op.get("points") or []]
    if len(pts) < 2:
        raise ValueError("a spline needs at least 2 points")
    if op.get("snap_to_ground"):
        off = float(op.get("ground_offset", 0.0))
        ignore = [actor] if actor is not None else []
        snapped = []
        for p in pts:
            h = trace_down(p.x, p.y, p.z + 50000.0, p.z - 50000.0, ignore)
            snapped.append(unreal.Vector(p.x, p.y, (h[0].z if h else p.z) + off))
        pts = snapped
    return pts


def _mesh_length(mesh, axis):
    b = mesh.get_bounding_box()
    mn, mx = b.min, b.max
    return {"X": mx.x - mn.x, "Y": mx.y - mn.y, "Z": mx.z - mn.z}[axis]


def _build_meshes(actor, spline, op):
    removed = remove_instance_components(actor, unreal.SplineMeshComponent)
    if not op.get("mesh"):
        return {"segments": 0, "removed_segments": removed}
    mesh = unreal.load_asset(op["mesh"])
    if not isinstance(mesh, unreal.StaticMesh):
        raise LookupError(f"static mesh {op['mesh']!r} not found")
    mat = unreal.load_asset(op["material"]) if op.get("material") else None
    axis = _AXES.get(str(op.get("forward_axis", "x")).lower())
    if axis is None:
        raise ValueError("forward_axis must be x, y or z")
    seg = float(op.get("segment_length") or _mesh_length(mesh, axis) or 100.0)
    total = spline.get_spline_length()
    n = max(1, int(round(total / seg)))
    if n > 2000:
        raise ValueError(f"{n} segments; raise segment_length (spline is {total:.0f} cm)")
    sc = op.get("mesh_scale", [1.0, 1.0])
    sc = [float(sc), float(sc)] if isinstance(sc, (int, float)) else [float(sc[0]), float(sc[1])]
    step = total / n
    for k in range(n):
        d0, d1 = k * step, (k + 1) * step
        p0 = spline.get_location_at_distance_along_spline(d0, _LOCAL)
        p1 = spline.get_location_at_distance_along_spline(d1, _LOCAL)
        t0 = spline.get_tangent_at_distance_along_spline(d0, _LOCAL)
        t1 = spline.get_tangent_at_distance_along_spline(d1, _LOCAL)
        t0 = t0 * (step / max(1e-6, t0.length()))
        t1 = t1 * (step / max(1e-6, t1.length()))
        smc = add_instance_component(actor, unreal.SplineMeshComponent, f"Segment_{k}")
        smc.set_static_mesh(mesh)
        smc.set_forward_axis(getattr(unreal.SplineMeshAxis, axis), False)
        smc.set_start_and_end(p0, t0, p1, t1, False)
        smc.set_start_scale(unreal.Vector2D(sc[0], sc[1]), False)
        smc.set_end_scale(unreal.Vector2D(sc[0], sc[1]), True)
        if mat is not None:
            smc.set_material(0, mat)
        if op.get("collision") is False:
            smc.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    return {"segments": n, "segment_length": round(step, 1), "removed_segments": removed}


def _configure(spline, op, pts):
    spline.set_spline_points(pts, _WORLD, False)
    if op.get("point_type"):
        t = _TYPES.get(op["point_type"].lower())
        if t is None:
            raise ValueError(f"point_type must be one of {sorted(_TYPES)}")
        for i in range(len(pts)):
            spline.set_spline_point_type(i, getattr(unreal.SplinePointType, t), False)
    if op.get("closed") is not None:
        spline.set_closed_loop(bool(op["closed"]), False)
    spline.update_spline()


def _summary(actor, spline):
    return {"label": actor.get_actor_label(), "points": spline.get_number_of_spline_points(),
            "length": round(spline.get_spline_length(), 1), "closed": spline.is_closed_loop()}


def _create(op):
    label = op.get("label") or "Spline"
    existing = find_actor(label)
    if existing is not None and not op.get("replace"):
        raise ValueError(f"an actor labelled {label!r} exists (use modify, or replace=true)")
    pts = _points(op)
    if _dry:
        return {"label": label, "points": len(pts), "dry_run": True,
                "approx_length": round(sum(distance(pts[i], pts[i + 1]) for i in range(len(pts) - 1)), 1)}
    if existing is not None:
        actor_sub().destroy_actor(existing)
    a = spawn_empty_actor(label, pts[0])
    a.tags = [unreal.Name("mcp_spline")]
    if op.get("folder"):
        a.set_folder_path(op["folder"])
    sp = add_instance_component(a, unreal.SplineComponent, "Spline")
    _configure(sp, op, pts)
    return {**_summary(a, sp), **_build_meshes(a, sp, op)}


def _modify(op):
    a = require_actor(op["target"])
    sp = _spline_of(a)
    if op.get("append"):
        cur = [sp.get_location_at_spline_point(i, _WORLD) for i in range(sp.get_number_of_spline_points())]
        op = {**op, "points": [v3(p) for p in cur] + list(op["append"])}
    pts = _points(op, a) if op.get("points") else None
    if _dry:
        return {"label": a.get_actor_label(), "dry_run": True, "points": len(pts) if pts else None}
    if pts:
        _configure(sp, op, pts)
    elif op.get("closed") is not None:
        sp.set_closed_loop(bool(op["closed"]), True)
    mesh_op = op if op.get("mesh") else {**op, "mesh": _current_mesh(a)}
    if op.get("mesh_scale") is None and not op.get("mesh"):
        mesh_op["mesh_scale"] = _current_scale(a)
    return {**_summary(a, sp), **_build_meshes(a, sp, mesh_op)}


def _current_mesh(a):
    for c in a.get_components_by_class(unreal.SplineMeshComponent):
        m = c.get_editor_property("static_mesh")
        if m is not None:
            return m.get_path_name()
    return None


def _current_scale(a):
    for c in a.get_components_by_class(unreal.SplineMeshComponent):
        s = c.get_start_scale()
        return [s.x, s.y]
    return [1.0, 1.0]


def _layer_info(name):
    ar = asset_registry()
    flt = unreal.ARFilter(class_paths=[unreal.TopLevelAssetPath("/Script/Landscape", "LandscapeLayerInfoObject")])
    for ad in ar.get_assets(flt):
        li = ad.get_asset()
        if li is not None and str(li.get_editor_property("layer_name")) == name:
            return li
    raise LookupError(f"no LandscapeLayerInfoObject for layer {name!r} (create the layer with landscape_edit first)")


def _carve(op):
    a = require_actor(op["target"])
    sp = _spline_of(a)
    land = surface_actor(op.get("landscape", ""))
    if land is None or not isinstance(land, unreal.LandscapeProxy):
        raise LookupError("carve needs landscape (label) or exactly one landscape in the level")
    layer = _layer_info(op["layer"]) if op.get("layer") else None
    w0 = float(op.get("width", 500.0))
    w1 = float(op.get("end_width", w0))
    fo = float(op.get("falloff", 300.0))
    if _dry:
        return {"target": a.get_actor_label(), "landscape": land.get_actor_label(), "dry_run": True}
    land.editor_apply_spline(sp, w0, w1, fo, fo, 0.0, 0.0, int(op.get("subdivisions", 20)),
                             bool(op.get("raise", True)), bool(op.get("lower", True)), layer, "")
    return {"target": a.get_actor_label(), "landscape": land.get_actor_label(), "width": w0, "falloff": fo,
            "layer": op.get("layer")}


def _delete(op):
    a = require_actor(op["target"])
    label = a.get_actor_label()
    if not _dry:
        actor_sub().destroy_actor(a)
    return {"deleted": label}


_handlers = {"create": _create, "modify": _modify, "carve": _carve, "delete": _delete}
_results = []
_ok = 0
with unreal.ScopedEditorTransaction("MCP spline_edit"):
    for _i, _op in enumerate(ARGS.get("operations") or []):
        try:
            _h = _handlers.get(_op.get("op"))
            if _h is None:
                raise ValueError(f"unknown op {_op.get('op')!r}; expected create, modify, carve or delete")
            _results.append({"index": _i, "op": _op.get("op"), "ok": True, **_h(_op)})
            _ok += 1
        except Exception as _e:
            _results.append({"index": _i, "op": _op.get("op"), "ok": False, "error": f"{type(_e).__name__}: {_e}"})
            if ARGS.get("stop_on_error"):
                break
result = {"succeeded": _ok, "failed": len(_results) - _ok, "dry_run": _dry, "results": _results}
