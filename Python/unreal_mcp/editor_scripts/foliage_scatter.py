# Scatter static meshes over a landscape/surface as foliage instances (InstancedFoliageActor) or HISM.
# include: _world
# ARGS: operations [...], dry_run, foliage_type_dir
#   scatter: {op, mesh | meshes:[{mesh, weight, scale:[lo,hi]}], surface (label; '' = the only landscape),
#             area:{min:[x,y], max:[x,y]} | {center:[x,y], radius}, density (per 100 m2) | count,
#             slope_min, slope_max (deg), height_min, height_max (world z), seed, scale:[lo,hi],
#             align_to_normal (bool | max degrees), random_yaw (true), z_offset, min_distance,
#             mode: foliage|hism, label (hism actor), max_attempts (x count, default 10)}
#   remove:  {op, mesh (optional), area (optional), mode: foliage|hism, label}
#   clear:   {op}  every foliage instance + every HISM scatter actor (tag mcp_scatter)
# ruff: noqa: F821
import random as _random

_eal = unreal.EditorAssetLibrary
_ft_dir = (ARGS.get("foliage_type_dir") or "/Game/Foliage/Types").rstrip("/")
_dry = bool(ARGS.get("dry_run"))
_IFA = unreal.InstancedFoliageActor
_FISM = unreal.FoliageInstancedStaticMeshComponent


def _mesh_path(m):
    return m.get_path_name() if m is not None else ""


def _load_mesh(path):
    obj = unreal.load_asset(path)
    if obj is None:
        raise LookupError(f"mesh {path!r} not found")
    if isinstance(obj, unreal.FoliageType):
        return obj.get_editor_property("mesh"), obj
    if not isinstance(obj, unreal.StaticMesh):
        raise TypeError(f"{path!r} is a {class_name_of(obj)}, expected a StaticMesh or FoliageType")
    return obj, None


def _foliage_types():
    ar = asset_registry()
    flt = unreal.ARFilter(class_paths=[unreal.TopLevelAssetPath("/Script/Foliage", "FoliageType_InstancedStaticMesh")],
                          recursive_classes=True)
    return [ad.get_asset() for ad in ar.get_assets(flt)]


def _foliage_type_for(mesh, create):
    for ft in _foliage_types():
        if ft is not None and ft.get_editor_property("mesh") == mesh:
            return ft
    if not create:
        return None
    name = "FT_" + mesh.get_name()
    path = f"{_ft_dir}/{name}"
    if _eal.does_asset_exist(path):
        ft = unreal.load_asset(path)
    else:
        ft = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, _ft_dir, unreal.FoliageType_InstancedStaticMesh, unreal.FoliageType_InstancedStaticMeshFactory())
    ft.set_editor_property("mesh", mesh)
    _eal.save_loaded_asset(ft, only_if_is_dirty=False)
    return ft


def _ifa_components():
    out = []
    for a in all_actors():
        if isinstance(a, _IFA):
            out += [(a, c) for c in a.get_components_by_class(_FISM)]
    return out


def _instance_transforms(comp):
    out = []
    for i in range(comp.get_instance_count()):
        t = comp.get_instance_transform(i, True)
        if isinstance(t, tuple):
            t = next((x for x in t if isinstance(x, unreal.Transform)), None)
        if t is not None:
            out.append(t)
    return out


def _area(op, surf):
    a = op.get("area") or {}
    if a.get("center") is not None:
        cx, cy = float(a["center"][0]), float(a["center"][1])
        r = float(a.get("radius", 1000))
        return (cx - r, cy - r, cx + r, cy + r), (cx, cy, r)
    if a.get("min") is not None and a.get("max") is not None:
        return (float(a["min"][0]), float(a["min"][1]), float(a["max"][0]), float(a["max"][1])), None
    if surf is None:
        raise ValueError("no area given and no single landscape to scatter on; pass surface or area")
    b = bounds_xy(surf)
    return b[:4], None


def _in_area(x, y, box, circle):
    if circle is not None:
        return (x - circle[0]) ** 2 + (y - circle[1]) ** 2 <= circle[2] ** 2
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _is_surface(actor, surf):
    if surf is None or actor is None:
        return actor is not None
    if actor == surf:
        return True
    # landscape streaming proxies belong to the landscape the caller named
    if isinstance(surf, unreal.Landscape) and isinstance(actor, unreal.LandscapeProxy):
        try:
            return actor.get_landscape_actor() == surf if hasattr(actor, "get_landscape_actor") else True
        except Exception:
            return True
    return False


def _scatter_ignore():
    return [a for a in all_actors() if isinstance(a, _IFA) or "mcp_scatter" in [str(t) for t in a.tags]]


def _scatter(op):
    entries = op.get("meshes") or [{"mesh": op.get("mesh")}]
    if not entries or not entries[0].get("mesh"):
        raise ValueError("scatter needs mesh or meshes")
    meshes = []
    for e in entries:
        mesh, ft = _load_mesh(e["mesh"])
        meshes.append({"mesh": mesh, "ft": ft, "weight": float(e.get("weight", 1.0)),
                       "scale": e.get("scale") or op.get("scale") or [1.0, 1.0]})
    surf = surface_actor(op.get("surface", ""))
    box, circle = _area(op, surf)
    if surf is not None:
        b = bounds_xy(surf)
        z_top, z_bot = b[5] + 1000.0, b[4] - 1000.0
    else:
        z_top, z_bot = float(op.get("z_top", 100000.0)), float(op.get("z_bottom", -100000.0))
    area_m2 = (box[2] - box[0]) * (box[3] - box[1]) / 1e4 * (_math.pi / 4 if circle else 1.0)
    target = int(op["count"]) if op.get("count") is not None else int(round(area_m2 / 100.0 * float(op.get("density", 10))))
    if target <= 0:
        raise ValueError("nothing to place: density/count gives 0 instances")
    if target > 200000:
        raise ValueError(f"{target} instances requested; cap is 200000 per op (lower density or area)")
    rng = _random.Random(int(op.get("seed", 0)))
    smin, smax = op.get("slope_min"), op.get("slope_max")
    hmin, hmax = op.get("height_min"), op.get("height_max")
    align = op.get("align_to_normal", False)
    align_max = 90.0 if align is True else float(align) if align else 0.0
    yaw_on = op.get("random_yaw", True)
    z_off = float(op.get("z_offset", 0.0))
    min_d = float(op.get("min_distance", 0.0))
    cell = min_d if min_d > 0 else None
    grid = {}
    ignore = _scatter_ignore()
    total_w = sum(m["weight"] for m in meshes)
    placed = {i: [] for i in range(len(meshes))}
    rejected = {"miss": 0, "other_surface": 0, "slope": 0, "height": 0, "spacing": 0, "outside_area": 0}
    attempts = 0
    max_attempts = target * int(op.get("max_attempts", 10))
    n = 0
    while n < target and attempts < max_attempts:
        attempts += 1
        x, y = rng.uniform(box[0], box[2]), rng.uniform(box[1], box[3])
        if not _in_area(x, y, box, circle):
            rejected["outside_area"] += 1
            continue
        hit = trace_down(x, y, z_top, z_bot, ignore)
        if hit is None:
            rejected["miss"] += 1
            continue
        loc, normal, actor = hit
        if not _is_surface(actor, surf):
            rejected["other_surface"] += 1
            continue
        s = slope_deg(normal)
        if (smin is not None and s < float(smin)) or (smax is not None and s > float(smax)):
            rejected["slope"] += 1
            continue
        if (hmin is not None and loc.z < float(hmin)) or (hmax is not None and loc.z > float(hmax)):
            rejected["height"] += 1
            continue
        if cell:
            gx, gy = int(_math.floor(x / cell)), int(_math.floor(y / cell))
            near = [p for dx in (-1, 0, 1) for dy in (-1, 0, 1) for p in grid.get((gx + dx, gy + dy), [])]
            if any((p[0] - x) ** 2 + (p[1] - y) ** 2 < min_d * min_d for p in near):
                rejected["spacing"] += 1
                continue
            grid.setdefault((gx, gy), []).append((x, y))
        pick, acc = 0, rng.uniform(0, total_w)
        for i, m in enumerate(meshes):
            acc -= m["weight"]
            if acc <= 0:
                pick = i
                break
        lo, hi = (float(v) for v in meshes[pick]["scale"])
        sc = rng.uniform(lo, hi)
        yaw = rng.uniform(0, 360) if yaw_on else 0.0
        if align_max > 0:
            nrm = normal
            if s > align_max:  # tilt only up to align_max toward the surface normal
                k = align_max / s
                nrm = unreal.Vector(normal.x * k, normal.y * k, normal.z)
            r = unreal.MathLibrary.compose_rotators(unreal.Rotator(roll=0, pitch=0, yaw=yaw),
                                                    unreal.MathLibrary.make_rot_from_z(nrm))
        else:
            r = unreal.Rotator(roll=0, pitch=0, yaw=yaw)
        p = unreal.Vector(loc.x, loc.y, loc.z + z_off)
        placed[pick].append(unreal.Transform(p, r, unreal.Vector(sc, sc, sc)))
        n += 1
    out = {"requested": target, "placed": n, "attempts": attempts, "rejected": {k: v for k, v in rejected.items() if v},
           "area_m2": round(area_m2, 1), "surface": surf.get_actor_label() if surf else None, "per_mesh": {}}
    if n < target:
        out["warning"] = (f"placed {n}/{target}: the filters rejected most samples (see rejected); widen slope/height "
                          f"limits, lower min_distance or raise max_attempts")
    mode = op.get("mode", "foliage")
    if mode not in ("foliage", "hism"):
        raise ValueError("mode must be foliage or hism")
    for i, m in enumerate(meshes):
        ts = placed[i]
        out["per_mesh"][_mesh_path(m["mesh"])] = len(ts)
        if _dry or not ts:
            continue
        if mode == "foliage":
            ft = m["ft"] or _foliage_type_for(m["mesh"], True)
            _IFA.add_instances(editor_world(), ft, ts)
        else:
            label = op.get("label") or f"Scatter_{m['mesh'].get_name()}"
            a = find_actor(label)
            if a is None:
                a = spawn_empty_actor(label)
                a.tags = [unreal.Name("mcp_scatter")]
                a.set_folder_path("Scatter")
            comp = next((c for c in a.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
                         if c.get_editor_property("static_mesh") == m["mesh"]), None)
            if comp is None:
                comp = add_instance_component(a, unreal.HierarchicalInstancedStaticMeshComponent,
                                              f"HISM_{m['mesh'].get_name()}")
                comp.set_editor_property("static_mesh", m["mesh"])
            comp.add_instances(ts, False, True)
            out["actor"] = a.get_actor_label()
    out["mode"] = mode
    return out


def _remove(op):
    mode = op.get("mode", "foliage")
    want = _load_mesh(op["mesh"])[0] if op.get("mesh") else None
    box = circle = None
    if op.get("area"):
        box, circle = _area(op, None)
    removed = 0
    if mode == "foliage":
        by_mesh = {}
        for _a, c in _ifa_components():
            m = c.get_editor_property("static_mesh")
            if want is None or m == want:
                by_mesh.setdefault(_mesh_path(m), (m, []))[1].append(c)
        for _p, (m, comps) in by_mesh.items():
            ft = _foliage_type_for(m, False)
            if ft is None:
                raise LookupError(f"no FoliageType asset found for {_p}")
            ts = [t for c in comps for t in _instance_transforms(c)]
            keep = [] if box is None else [t for t in ts if not _in_area(t.translation.x, t.translation.y, box, circle)]
            removed += len(ts) - len(keep)
            if not _dry:
                _IFA.remove_all_instances(editor_world(), ft)
                if keep:
                    _IFA.add_instances(editor_world(), ft, keep)
    elif mode == "hism":
        actors = [require_actor(op["label"])] if op.get("label") else [
            a for a in all_actors() if "mcp_scatter" in [str(t) for t in a.tags]]
        for a in actors:
            for c in a.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent):
                if want is not None and c.get_editor_property("static_mesh") != want:
                    continue
                n = c.get_instance_count()
                if box is None:
                    removed += n
                    if not _dry:
                        c.clear_instances()
                    continue
                idx = [i for i, t in enumerate(_instance_transforms(c))
                       if _in_area(t.translation.x, t.translation.y, box, circle)]
                removed += len(idx)
                if idx and not _dry:
                    c.remove_instances(idx)
    else:
        raise ValueError("mode must be foliage or hism")
    return {"removed": removed, "mode": mode}


def _clear(op):
    a = _remove({"op": "remove", "mode": "foliage"})
    b = _remove({"op": "remove", "mode": "hism"})
    if not _dry:
        for act in [x for x in all_actors() if "mcp_scatter" in [str(t) for t in x.tags]]:
            actor_sub().destroy_actor(act)
    return {"removed": a["removed"] + b["removed"]}


_handlers = {"scatter": _scatter, "remove": _remove, "clear": _clear}
_results = []
_ok = 0
with unreal.ScopedEditorTransaction("MCP foliage_scatter"):
    for _i, _op in enumerate(ARGS.get("operations") or []):
        try:
            _h = _handlers.get(_op.get("op"))
            if _h is None:
                raise ValueError(f"unknown op {_op.get('op')!r}; expected scatter, remove or clear")
            _results.append({"index": _i, "op": _op.get("op"), "ok": True, **_h(_op)})
            _ok += 1
        except Exception as _e:
            _results.append({"index": _i, "op": _op.get("op"), "ok": False, "error": f"{type(_e).__name__}: {_e}"})
            if ARGS.get("stop_on_error"):
                break
result = {"succeeded": _ok, "failed": len(_results) - _ok, "dry_run": _dry, "results": _results}
