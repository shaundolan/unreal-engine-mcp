# Spline actors (SplineComponent + SplineMeshComponent segments) for roads, rivers, pipes, fences.
# include: _world, _road
# ARGS: operations [...], dry_run, stop_on_error
#   create: {op, label, points [[x,y,z],...] (world), closed, point_type curve|linear|curve_clamped|constant,
#            snap_to_ground, snap_to landscape|any, ground_offset, mesh, mesh_scale [y,z] | number, forward_axis x|y|z,
#            segment_length (cm, default = mesh length), material, collision (bool), replace (bool)}
#   modify: {op, target, points | append, closed, point_type, snap_to_ground, mesh, mesh_scale, ...}  rebuilds meshes
#   carve:  {op, target, landscape, width, end_width, falloff, raise, lower, layer, subdivisions}
#   delete: {op, target}
#   road (create / modify option): {width, clearance, step, max_bend_deg, top_offset, carve, falloff}: the points are
#            the centreline (x, y); the deck follows the shared road rules (_road.py): one continuous profile at least
#            `clearance` over the ground under its whole width, other roads ignored (they meet at a junction, never
#            stacked), one mesh segment per station; carve: the landscape pulled to MARGIN under the deck's top
#   junction: {op, roads [A, B], radius, pad_mesh, pad_thickness, material}: ONE pad at ONE height where two roads
#            cross, both eased into it, their own segments stopped at its edge
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
        only_land = op.get("snap_to", "landscape") == "landscape" and bool(landscapes())
        snapped = []
        for p in pts:
            ignore = [actor] if actor is not None else []
            h = None
            for _ in range(8):  # skip props/markers in the way unless snap_to="any"
                h = trace_down(p.x, p.y, p.z + 50000.0, p.z - 50000.0, ignore)
                if h is None or not only_land or isinstance(h[2], unreal.LandscapeProxy):
                    break
                ignore.append(h[2])
                h = None
            snapped.append(unreal.Vector(p.x, p.y, (h[0].z if h else p.z) + off))
        pts = snapped
    return pts


def _road_opts(op):
    r = op.get("road")
    if not r:
        return None
    r = dict(r) if isinstance(r, dict) else {}
    r.setdefault("width", 800.0)
    r.setdefault("clearance", CLEAR)
    r.setdefault("step", STEP)
    r.setdefault("max_bend_deg", MAX_BEND_DEG)
    if r.get("top_offset") is None:
        # the deck's top over the spline: a centred mesh (the engine cube) is half its scaled height above it
        sc = op.get("mesh_scale", [1.0, 1.0])
        sz = float(sc) if isinstance(sc, (int, float)) else float(sc[1])
        mesh = unreal.load_asset(op["mesh"]) if op.get("mesh") else None
        if mesh is not None:
            b = mesh.get_bounding_box()
            r["top_offset"] = float(b.max.z) * sz
        else:
            r["top_offset"] = 0.0
    return r


def _road_ground(op, ignore):
    """ground(x, y) for the road rules: the landscape (or any surface with snap_to "any"), never another road."""
    only_land = op.get("snap_to", "landscape") == "landscape" and bool(landscapes())
    roads = [a for a in all_actors() if "mcp_spline" in [str(t) for t in (a.tags or [])]]
    skip = list(ignore) + roads

    def ground(x, y):
        ig = list(skip)
        for _ in range(8):
            h = trace_down(x, y, 1.0e6, -1.0e6, ig)
            if h is None:
                return None
            if not only_land or isinstance(h[2], unreal.LandscapeProxy):
                return h[0].z
            ig.append(h[2])
        return None
    return ground


def _road_points(op, r, actor=None):
    """The road's deck tops (shared rules) and the spline points under them (top - top_offset)."""
    xy = [(float(p[0]), float(p[1])) for p in op.get("points") or []]
    if len(xy) < 2:
        raise ValueError("a road needs at least 2 points")
    tops = road_points(xy, _road_ground(op, [actor] if actor is not None else []), float(r["width"]), float(r["step"]),
                       float(r["clearance"]), float(r["max_bend_deg"]))
    off = float(r["top_offset"])
    return [unreal.Vector(x, y, z - off) for x, y, z in tops], tops


def _road_tags(a, r):
    keep = [t for t in (a.tags or []) if not str(t).startswith("mcp_road")]
    a.tags = keep + [unreal.Name("mcp_road"), unreal.Name(f"mcp_road_width={float(r['width']):.1f}"),
                     unreal.Name(f"mcp_road_top={float(r['top_offset']):.2f}")]


def _road_tag(a, key, default):
    for t in a.tags or []:
        t = str(t)
        if t.startswith(key + "="):
            return float(t.split("=", 1)[1])
    return default


def _carve_under(a, sp, r, land):
    """The landscape pulled to MARGIN under the deck's top along the road (a temporary spline at that height)."""
    n = sp.get_number_of_spline_points()
    off = float(r["top_offset"]) + MARGIN
    pts = [sp.get_location_at_spline_point(i, _WORLD) for i in range(n)]
    tmp = spawn_empty_actor("MCP_RoadCarve_tmp", pts[0])
    try:
        tsp = add_instance_component(tmp, unreal.SplineComponent, "Spline")
        tsp.set_spline_points([unreal.Vector(p.x, p.y, p.z + float(r["top_offset"]) - off) for p in pts], _WORLD, False)
        tsp.update_spline()
        w = float(r["width"])
        fo = float(r.get("falloff", 300.0))
        land.editor_apply_spline(tsp, w, w, fo, fo, 0.0, 0.0, 20, True, True, None, "")
    finally:
        actor_sub().destroy_actor(tmp)


def _mesh_length(mesh, axis):
    b = mesh.get_bounding_box()
    mn, mx = b.min, b.max
    return {"X": mx.x - mn.x, "Y": mx.y - mn.y, "Z": mx.z - mn.z}[axis]


def _build_meshes(actor, spline, op, skip=()):
    """skip: (d0, d1) spline distances left without a segment (a junction pad covers them)."""
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
    built = 0
    for k in range(n):
        d0, d1 = k * step, (k + 1) * step
        if any(a <= (d0 + d1) * 0.5 <= b for a, b in skip):
            continue
        built += 1
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
    return {"segments": built, "segment_length": round(step, 1), "removed_segments": removed}


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
    r = _road_opts(op)
    tops = None
    if r is not None:
        pts, tops = _road_points(op, r, existing)
        op = {**op, "segment_length": op.get("segment_length") or float(r["step"])}
    else:
        pts = _points(op)
    if _dry:
        out = {"label": label, "points": len(pts), "dry_run": True,
               "approx_length": round(sum(distance(pts[i], pts[i + 1]) for i in range(len(pts) - 1)), 1)}
        if tops is not None:
            out["faults"] = len(surface_faults(tops))
        return out
    if existing is not None:
        actor_sub().destroy_actor(existing)
    a = spawn_empty_actor(label, pts[0])
    a.tags = [unreal.Name("mcp_spline")]
    if op.get("folder"):
        a.set_folder_path(op["folder"])
    sp = add_instance_component(a, unreal.SplineComponent, "Spline")
    _configure(sp, op, pts)
    out = {**_summary(a, sp), **_build_meshes(a, sp, op)}
    if r is not None:
        _road_tags(a, r)
        out["road"] = {"stations": len(tops), "faults": len(surface_faults(tops)), "width": r["width"]}
        if r.get("carve"):
            land = surface_actor(r["carve"] if isinstance(r["carve"], str) else "")
            if land is not None and isinstance(land, unreal.LandscapeProxy):
                _carve_under(a, sp, r, land)
                out["road"]["carved"] = land.get_actor_label()
    return out


def _modify(op):
    a = require_actor(op["target"])
    sp = _spline_of(a)
    if op.get("append"):
        cur = [sp.get_location_at_spline_point(i, _WORLD) for i in range(sp.get_number_of_spline_points())]
        op = {**op, "points": [v3(p) for p in cur] + list(op["append"])}
    r = _road_opts(op)
    if r is not None and op.get("points"):
        pts, _tops = _road_points(op, r, a)
        op = {**op, "segment_length": op.get("segment_length") or float(r["step"])}
    else:
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
    out = {**_summary(a, sp), **_build_meshes(a, sp, mesh_op)}
    if r is not None:
        _road_tags(a, r)
    return out


def _junction(op):
    """Two roads that cross: ONE pad at ONE height, both eased into it, their own segments stopped at its edge."""
    names = op.get("roads") or []
    if len(names) != 2:
        raise ValueError("junction needs roads: [A, B]")
    acts = [require_actor(n) for n in names]
    sps = [_spline_of(x) for x in acts]
    tops, offs, widths = [], [], []
    for x, sp in zip(acts, sps):
        off = _road_tag(x, "mcp_road_top", 0.0)
        offs.append(off)
        widths.append(_road_tag(x, "mcp_road_width", 800.0))
        n = sp.get_number_of_spline_points()
        tops.append([(p.x, p.y, p.z + off) for p in (sp.get_location_at_spline_point(i, _WORLD) for i in range(n))])
    xs = crossings(tops[0], tops[1])
    if not xs:
        raise ValueError(f"{names[0]} and {names[1]} do not cross")
    # the pad reaches as far as the two carriageways overlap (a skewed crossing overlaps further)
    ia, _ta, ib, _tb, _x, _y = xs[0]
    ax, ay = tops[0][ia + 1][0] - tops[0][ia][0], tops[0][ia + 1][1] - tops[0][ia][1]
    bx, by = tops[1][ib + 1][0] - tops[1][ib][0], tops[1][ib + 1][1] - tops[1][ib][1]
    sin = abs(ax * by - ay * bx) / max(1e-6, _math.hypot(ax, ay) * _math.hypot(bx, by))
    radius = float(op.get("radius") or (0.5 * max(widths) / max(0.3, sin) + 50.0))
    a2, b2, pads = junction(tops[0], tops[1], radius, float(op.get("blend", BLEND)))
    if _dry:
        return {"roads": names, "pads": len(pads), "radius": round(radius, 1), "dry_run": True}
    for x, sp, prof, off, key in ((acts[0], sps[0], a2, offs[0], "sa"), (acts[1], sps[1], b2, offs[1], "sb")):
        sp.set_spline_points([unreal.Vector(px, py, pz - off) for px, py, pz in prof], _WORLD, False)
        sp.update_spline()
        skip = [(p[key] - p["radius"], p[key] + p["radius"]) for p in pads]
        mesh = _current_mesh(x)
        _build_meshes(x, sp, {"mesh": mesh, "mesh_scale": _current_scale(x), "segment_length": STEP}, skip)
    pad_mesh = unreal.load_asset(op.get("pad_mesh", "/Engine/BasicShapes/Cylinder.Cylinder"))
    thick = float(op.get("pad_thickness", 20.0))
    mat = unreal.load_asset(op["material"]) if op.get("material") else None
    out_pads = []
    for k, p in enumerate(pads):
        label = f"JunctionPad_{names[0]}_{names[1]}_{k}"
        old = find_actor(label)
        if old is not None:
            actor_sub().destroy_actor(old)
        pa = actor_sub().spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(p["x"], p["y"], p["z"] - thick * 0.5))
        pa.set_actor_label(label)
        pa.tags = [unreal.Name("mcp_spline"), unreal.Name("mcp_road_pad")]
        smc = pa.static_mesh_component
        smc.set_static_mesh(pad_mesh)
        if mat is not None:
            smc.set_material(0, mat)
        pa.set_actor_scale3d(unreal.Vector(2.0 * p["radius"] / 100.0, 2.0 * p["radius"] / 100.0, thick / 100.0))
        out_pads.append({"label": label, "z_top": round(p["z"], 1), "radius": round(p["radius"], 1)})
    return {"roads": names, "pads": out_pads}


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


_handlers = {"create": _create, "modify": _modify, "carve": _carve, "delete": _delete, "junction": _junction}
_results = []
_ok = 0
with unreal.ScopedEditorTransaction("MCP spline_edit"):
    for _i, _op in enumerate(ARGS.get("operations") or []):
        try:
            _h = _handlers.get(_op.get("op"))
            if _h is None:
                raise ValueError(f"unknown op {_op.get('op')!r}; expected create, modify, carve, junction or delete")
            _results.append({"index": _i, "op": _op.get("op"), "ok": True, **_h(_op)})
            _ok += 1
        except Exception as _e:
            _results.append({"index": _i, "op": _op.get("op"), "ok": False, "error": f"{type(_e).__name__}: {_e}"})
            if ARGS.get("stop_on_error"):
                break
result = {"succeeded": _ok, "failed": len(_results) - _ok, "dry_run": _dry, "results": _results}
