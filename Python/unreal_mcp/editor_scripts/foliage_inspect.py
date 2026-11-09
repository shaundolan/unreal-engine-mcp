# Foliage + HISM scatter summary per mesh: count, z range, scale range, bounds; optional slope sampling.
# include: _world
# ARGS: mesh (optional filter), sample_slopes (bool), max_samples (int)
# ruff: noqa: F821
import random as _random

_want = ARGS.get("mesh") or ""
_sample = bool(ARGS.get("sample_slopes"))
_max = int(ARGS.get("max_samples", 2000))
_groups = {}
_ignore = []
for _a in all_actors():
    _tags = [str(t) for t in _a.tags]
    if isinstance(_a, unreal.InstancedFoliageActor):
        _kind, _comps = "foliage", _a.get_components_by_class(unreal.FoliageInstancedStaticMeshComponent)
    elif "mcp_scatter" in _tags:
        _kind, _comps = "hism", _a.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
    else:
        continue
    _ignore.append(_a)
    for _c in _comps:
        _m = _c.get_editor_property("static_mesh")
        _p = _m.get_path_name() if _m else ""
        if _want and _want.split(".")[0] not in _p:
            continue
        _g = _groups.setdefault((_kind, _p), {"mode": _kind, "mesh": _p, "count": 0, "actors": set(), "ts": []})
        _g["actors"].add(_a.get_actor_label())
        for _i in range(_c.get_instance_count()):
            _t = _c.get_instance_transform(_i, True)
            if isinstance(_t, tuple):
                _t = next((x for x in _t if isinstance(x, unreal.Transform)), None)
            if _t is not None:
                _g["ts"].append(_t)
        _g["count"] = len(_g["ts"])

_out = []
for _g in _groups.values():
    _ts = _g.pop("ts")
    _g["actors"] = sorted(_g["actors"])
    if _ts:
        _xs = [t.translation.x for t in _ts]
        _ys = [t.translation.y for t in _ts]
        _zs = [t.translation.z for t in _ts]
        _sc = [t.scale3d.z for t in _ts]
        _g["z"] = {"min": round(min(_zs), 1), "max": round(max(_zs), 1)}
        _g["scale"] = {"min": round(min(_sc), 3), "max": round(max(_sc), 3)}
        _g["bounds_xy"] = {"min": [round(min(_xs), 1), round(min(_ys), 1)], "max": [round(max(_xs), 1), round(max(_ys), 1)]}
        _tilts = [abs(t.rotation.rotator().pitch) + abs(t.rotation.rotator().roll) for t in _ts[:_max]]
        _g["tilted_fraction"] = round(sum(1 for v in _tilts if v > 0.5) / len(_tilts), 3)
        if _sample:
            _pick = _ts if len(_ts) <= _max else _random.Random(0).sample(_ts, _max)
            _sl = []
            _miss = 0
            for _t in _pick:
                _h = trace_down(_t.translation.x, _t.translation.y, _t.translation.z + 500.0, _t.translation.z - 500.0, _ignore)
                if _h is None:
                    _miss += 1
                    continue
                _sl.append(slope_deg(_h[1]))
            if _sl:
                _sl.sort()
                _g["slope_deg"] = {"mean": round(sum(_sl) / len(_sl), 2), "p90": round(_sl[int(len(_sl) * 0.9)], 2),
                                   "max": round(_sl[-1], 2), "samples": len(_sl), "no_ground": _miss}
    _out.append(_g)
result = {"groups": _out, "total": sum(g["count"] for g in _out)}
