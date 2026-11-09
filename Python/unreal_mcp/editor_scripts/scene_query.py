# ARGS: class, label_contains, tag, folder, near [x,y,z], radius, box {min,max}, limit, fields, sort_by_distance
# ruff: noqa: F821
_near = vec(ARGS["near"]) if ARGS.get("near") is not None else None
_radius = ARGS.get("radius")
_box = ARGS.get("box")
_bmin = vec(_box["min"]) if _box else None
_bmax = vec(_box["max"]) if _box else None
_label = (ARGS.get("label_contains") or "").lower()
_tag = ARGS.get("tag") or ""
_folder = (ARGS.get("folder") or "").lower()
_fields = tuple(ARGS.get("fields") or ("class", "location"))
_limit = int(ARGS.get("limit") or 200)

_hits = []
_total = 0
for _a in all_actors():
    if not is_a(_a, ARGS.get("class")):
        continue
    if _label and _label not in _a.get_actor_label().lower():
        continue
    if _tag and _tag not in [str(t) for t in _a.tags]:
        continue
    if _folder and not str(_a.get_folder_path()).lower().startswith(_folder):
        continue
    _loc = _a.get_actor_location()
    _d = distance(_loc, _near) if _near is not None else None
    if _near is not None and _radius is not None and _d > float(_radius):
        continue
    if _box and not (_bmin.x <= _loc.x <= _bmax.x and _bmin.y <= _loc.y <= _bmax.y and _bmin.z <= _loc.z <= _bmax.z):
        continue
    _total += 1
    _s = actor_summary(_a, _fields)
    if _d is not None:
        _s["distance"] = round(_d, 2)
    _hits.append(_s)

if _near is not None:
    _hits.sort(key=lambda h: h["distance"])
result = {"total_matches": _total, "returned": min(_total, _limit), "truncated": _total > _limit,
          "actors": _hits[:_limit]}
