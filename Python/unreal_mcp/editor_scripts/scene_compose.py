# Declarative batch of spawn/modify/delete ops in ONE undo transaction.
# ARGS: operations [...], stop_on_error (bool)
#   spawn:  {op, class | asset, label, location, rotation, scale, tags, folder, properties,
#            pattern: {type: grid, count: [nx, ny], spacing: [dx, dy]}
#                   | {type: circle, count, radius, face_center}
#                   | {type: line, count, step: [x, y, z]}}
#   modify: {op, target, location, rotation, scale, label, tags, folder, properties, hidden}
#   delete: {op, target}
# ruff: noqa: F821


def _pattern_offsets(p):
    if not p:
        return [((0.0, 0.0, 0.0), None)]
    t = p.get("type")
    if t == "grid":
        nx, ny = p.get("count", [1, 1])
        dx, dy = p.get("spacing", [100, 100])
        return [((i * dx, j * dy, 0.0), None) for j in range(int(ny)) for i in range(int(nx))]
    if t == "circle":
        n, r = int(p.get("count", 8)), float(p.get("radius", 500))
        out = []
        for i in range(n):
            ang = 2 * _math.pi * i / n
            yaw = _math.degrees(ang) + 180.0 if p.get("face_center") else None
            out.append(((r * _math.cos(ang), r * _math.sin(ang), 0.0), yaw))
        return out
    if t == "line":
        n = int(p.get("count", 2))
        sx, sy, sz = p.get("step", [100, 0, 0])
        return [((i * sx, i * sy, i * sz), None) for i in range(n)]
    raise ValueError(f"unknown pattern type {t!r}")


def _apply_common(a, op):
    if op.get("label"):
        a.set_actor_label(op["label"])
    if op.get("scale") is not None:
        a.set_actor_scale3d(vec(op["scale"]))
    if op.get("tags") is not None:
        a.tags = [unreal.Name(t) for t in op["tags"]]
    if op.get("folder") is not None:
        a.set_folder_path(op["folder"])
    if op.get("hidden") is not None:
        a.set_is_temporarily_hidden_in_editor(bool(op["hidden"]))
    return set_props(a, op.get("properties"))


def _spawn(op):
    sub = actor_sub()
    base = vec(op.get("location"))
    base_rot = rot(op.get("rotation"))
    source = None
    if op.get("asset"):
        source = unreal.load_asset(op["asset"])
        if source is None:
            raise LookupError(f"asset {op['asset']!r} not found")
    else:
        source = resolve_class(op.get("class"))
        if source is None:
            raise LookupError(f"class {op.get('class')!r} not found")
    labels = []
    offsets = _pattern_offsets(op.get("pattern"))
    for idx, (off, yaw) in enumerate(offsets):
        loc = unreal.Vector(base.x + off[0], base.y + off[1], base.z + off[2])
        r = unreal.Rotator(roll=base_rot.roll, pitch=base_rot.pitch, yaw=yaw if yaw is not None else base_rot.yaw)
        if op.get("asset"):
            a = sub.spawn_actor_from_object(source, loc, r)
        else:
            a = sub.spawn_actor_from_class(source, loc, r)
        if a is None:
            raise RuntimeError("spawn returned None")
        one = dict(op)
        if op.get("label") and len(offsets) > 1:
            one["label"] = f"{op['label']}_{idx}"
        _apply_common(a, one)
        labels.append(a.get_actor_label())
    return {"spawned": labels}


def _modify(op):
    a = require_actor(op["target"])
    if op.get("location") is not None:
        a.set_actor_location(vec(op["location"]), False, False)
    if op.get("rotation") is not None:
        a.set_actor_rotation(rot(op["rotation"]), False)
    props = _apply_common(a, op)
    return {"modified": a.get_actor_label(), "properties_set": props}


def _delete(op):
    a = require_actor(op["target"])
    label = a.get_actor_label()
    actor_sub().destroy_actor(a)
    return {"deleted": label}


_handlers = {"spawn": _spawn, "modify": _modify, "delete": _delete}
_results = []
_ok = 0
with unreal.ScopedEditorTransaction("MCP scene_compose"):
    for _i, _op in enumerate(ARGS.get("operations") or []):
        try:
            _h = _handlers.get(_op.get("op"))
            if _h is None:
                raise ValueError(f"unknown op {_op.get('op')!r}; expected spawn, modify or delete")
            _results.append({"index": _i, "ok": True, **_h(_op)})
            _ok += 1
        except Exception as _e:
            _results.append({"index": _i, "ok": False, "error": f"{type(_e).__name__}: {_e}"})
            if ARGS.get("stop_on_error"):
                break
result = {"succeeded": _ok, "failed": len(_results) - _ok, "results": _results}
