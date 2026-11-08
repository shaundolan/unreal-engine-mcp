# Compact level overview. ARGS: top_classes (int)
# ruff: noqa: F821
_actors = all_actors()
_by_class = {}
_by_folder = {}
_lo = [float("inf")] * 3
_hi = [float("-inf")] * 3
_lights = []
_cameras = []
for _a in _actors:
    _c = class_name_of(_a)
    _by_class[_c] = _by_class.get(_c, 0) + 1
    _f = str(_a.get_folder_path()) or "(root)"
    _by_folder[_f] = _by_folder.get(_f, 0) + 1
    _p = _a.get_actor_location()
    for _i, _v in enumerate((_p.x, _p.y, _p.z)):
        _lo[_i] = min(_lo[_i], _v)
        _hi[_i] = max(_hi[_i], _v)
    if "Light" in _c and "Component" not in _c:
        _lights.append(_a.get_actor_label())
    if "Camera" in _c:
        _cameras.append(_a.get_actor_label())

_world = editor_world()
_top = int(ARGS.get("top_classes") or 25)
result = {
    "level": _world.get_path_name() if _world else None,
    "actor_count": len(_actors),
    "classes": dict(sorted(_by_class.items(), key=lambda kv: -kv[1])[:_top]),
    "folders": dict(sorted(_by_folder.items(), key=lambda kv: -kv[1])[:_top]),
    "bounds": {"min": [round(x, 1) for x in _lo], "max": [round(x, 1) for x in _hi]} if _actors else None,
    "lights": _lights[:50],
    "cameras": _cameras[:50],
}
