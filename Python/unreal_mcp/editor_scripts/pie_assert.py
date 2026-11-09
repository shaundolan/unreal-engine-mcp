# Evaluate assertions in the running PIE world (world="pie", default) or the editor level (world="editor").
# ARGS: world, exec (optional code run first, with the same scope as python assertions; e.g. "player.jump()"),
# assertions [...]
#   {type: actor_exists, label?, class?, min?=1, max?}
#   {type: property, target, property ("a.b.c"), op, value, tolerance?}
#   {type: distance, a, b | point [x,y,z], op, value}
#   {type: python, expr}   evaluated with `world`, `actors`, `find(label)`, `player`, `asset(path)`, `dist(a, b)` in scope
#   {type: asset_exists, path, class?}      {type: asset_property, path, property, op, value}
# target/a/b may be "@player" for player 0's pawn. ops: == != < <= > >= approx
# ruff: noqa: F821
_where = ARGS.get("world", "pie")
if _where == "editor":
    _world = editor_world()
    _actors = all_actors()
    _player = None
else:
    _where = "PIE"
    _world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if _world is None:
        raise RuntimeError("PIE is not running (no game world)")
    _actors = list(unreal.GameplayStatics.get_all_actors_of_class(_world, unreal.Actor))
    _player = unreal.GameplayStatics.get_player_pawn(_world, 0)


def _find(label):
    if label == "@player":
        if _player is None:
            raise LookupError("@player only exists in PIE")
        return _player
    want = str(label).lower()
    for a in _actors:
        if a.get_actor_label().lower() == want or a.get_name().lower() == want:
            return a
    return None


def _plain(v):
    if hasattr(v, "to_tuple"):
        return [round(float(x), 4) for x in v.to_tuple()]
    if hasattr(v, "get_path_name"):
        return v.get_path_name()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _cmp(actual, op, expected, tol):
    if op == "approx":
        if isinstance(actual, list):
            return len(actual) == len(expected) and all(abs(a - e) <= tol for a, e in zip(actual, expected, strict=True))
        return abs(float(actual) - float(expected)) <= tol
    ops = {"==": lambda a, e: a == e, "!=": lambda a, e: a != e, "<": lambda a, e: a < e,
           "<=": lambda a, e: a <= e, ">": lambda a, e: a > e, ">=": lambda a, e: a >= e}
    if op not in ops:
        raise ValueError(f"unknown op {op!r}")
    return ops[op](actual, expected)


def _get_path(obj, path):
    for part in path.split("."):
        obj = obj.get_editor_property(part)
        if obj is None:
            break
    return obj


def _asset(path):
    a = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if a is None:
        raise LookupError(f"asset {path!r} not found")
    return a


def _loc(x):
    if isinstance(x, str):
        a = _find(x)
        if a is None:
            raise LookupError(f"actor {x!r} not in {_where} world")
        return a.get_actor_location()
    if isinstance(x, (list, tuple)):
        return vec(x)
    return x.get_actor_location() if hasattr(x, "get_actor_location") else x


def _dist(a, b):
    """Distance between actors (objects or labels), points [x,y,z] or Vectors."""
    return distance(_loc(a), _loc(b))


_scope = {"unreal": unreal, "dist": _dist, "world": _world, "actors": _actors, "find": _find, "player": _player, "vec": vec,
          "asset": _asset}
if ARGS.get("exec"):
    exec(compile(ARGS["exec"], "<pie_step>", "exec"), _scope)

_out = []
for _i, _a in enumerate(ARGS.get("assertions") or []):
    _t = _a.get("type")
    _rec = {"index": _i, "type": _t}
    try:
        if _t == "actor_exists":
            _m = [x for x in _actors
                  if (not _a.get("label") or x.get_actor_label().lower() == _a["label"].lower())
                  and is_a(x, _a.get("class"))]
            _rec["actual"] = len(_m)
            _rec["passed"] = len(_m) >= int(_a.get("min", 1)) and (_a.get("max") is None or len(_m) <= int(_a["max"]))
        elif _t == "property":
            _obj = _find(_a["target"])
            if _obj is None:
                raise LookupError(f"actor {_a['target']!r} not in {_where} world")
            _v = _plain(_get_path(_obj, _a["property"]))
            _rec["actual"] = _v
            _rec["passed"] = bool(_cmp(_v, _a.get("op", "=="), _a.get("value"), float(_a.get("tolerance", 0.01))))
        elif _t == "distance":
            _pa = _find(_a["a"])
            if _pa is None:
                raise LookupError(f"actor {_a['a']!r} not in {_where} world")
            _pb = vec(_a["point"]) if _a.get("point") is not None else None
            if _pb is None:
                _ob = _find(_a["b"])
                if _ob is None:
                    raise LookupError(f"actor {_a['b']!r} not in {_where} world")
                _pb = _ob.get_actor_location()
            _d = distance(_pa.get_actor_location(), _pb)
            _rec["actual"] = round(_d, 2)
            _rec["passed"] = bool(_cmp(_d, _a.get("op", "<="), float(_a["value"]), float(_a.get("tolerance", 0.01))))
        elif _t == "asset_exists":
            _ok = bool(unreal.EditorAssetLibrary.does_asset_exist(_a["path"]))
            if _ok and _a.get("class"):
                _obj = unreal.load_asset(_a["path"])
                _rec["actual"] = class_name_of(_obj)
                _ok = is_a(_obj, _a["class"])
            else:
                _rec["actual"] = _ok
            _rec["passed"] = _ok
        elif _t == "asset_property":
            _v = _plain(_get_path(_asset(_a["path"]), _a["property"]))
            _rec["actual"] = _v
            _rec["passed"] = bool(_cmp(_v, _a.get("op", "=="), _a.get("value"), float(_a.get("tolerance", 0.01))))
        elif _t == "python":
            _v = eval(_a["expr"], _scope)
            _rec["actual"] = _plain(_v)
            _rec["passed"] = bool(_v)
        else:
            raise ValueError(f"unknown assertion type {_t!r}")
    except Exception as _e:
        _rec["passed"] = False
        _rec["error"] = f"{type(_e).__name__}: {_e}"
    if _a.get("name"):
        _rec["name"] = _a["name"]
    _out.append(_rec)
result = {"assertions": _out, "actor_count": len(_actors),
          "player": _player.get_actor_label() if _player else None}
