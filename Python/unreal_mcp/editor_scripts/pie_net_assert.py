# Replication assertions across the worlds of a multi-client PIE session (one editor process). Each is evaluated
# once; pie_test re-runs the pending ones until they pass or their within_s runs out.
# ARGS: assertions [...]
#   {type: replicated, label? | class?, source?="server", clients?="all" | [1, 2] | ["client:1"], min?=1}
#       every matching actor of the source world has a copy (same net id) in each client world
#   {type: replicated_property, target, property, op?="approx", tolerance?=1.0, source?="server", clients?}
#       the copy's value on each client matches the source value; property "location" | "rotation" | "velocity" read
#       the actor transform / velocity, anything else is a property path ("a.b.c") as in pie_assert
# target may be "@player" (player 0's pawn in the source world).
# ruff: noqa: F821
_lib = mcp_lib()
if _lib is None or not hasattr(_lib, "get_pie_worlds"):
    raise RuntimeError("replication assertions need the updated UnrealMCP plugin (MCPScriptLibrary.GetPieWorlds)")
_worlds = {}
for _w in _lib.get_pie_worlds():
    _worlds[str(_lib.get_pie_world_target(_w))] = _w
_all_clients = sorted([t for t in _worlds if t.startswith("client:")], key=lambda t: int(t.split(":")[1]))


def _client_targets(spec):
    if spec in (None, "all", ""):
        return list(_all_clients)
    if not isinstance(spec, (list, tuple)):
        spec = [spec]
    return [str(s) if str(s).startswith(("client", "server", "standalone")) else f"client:{int(s)}" for s in spec]


def _world(target):
    w = _worlds.get(target)
    if w is None:
        raise LookupError(f"no PIE world {target!r} (worlds: {sorted(_worlds)})")
    return w


def _actors(w):
    return list(unreal.GameplayStatics.get_all_actors_of_class(w, unreal.Actor))


def _named(x, label):
    want = str(label).lower()
    return x.get_actor_label().lower() == want or x.get_name().lower() == want


def _sources(w, a):
    label = a.get("target") or a.get("label")
    if label == "@player":
        p = unreal.GameplayStatics.get_player_pawn(w, 0)
        return [p] if p is not None else []
    return [x for x in _actors(w) if (not label or _named(x, label)) and is_a(x, a.get("class"))]


def _plain(v):
    if hasattr(v, "to_tuple"):
        return [round(float(x), 4) for x in v.to_tuple()]
    if hasattr(v, "get_path_name"):
        return v.get_path_name()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _value(actor, prop):
    if prop == "location":
        return _plain(actor.get_actor_location())
    if prop == "rotation":
        return _plain(actor.get_actor_rotation())
    if prop == "velocity":
        return _plain(actor.get_velocity())
    obj = actor
    for part in prop.split("."):
        obj = obj.get_editor_property(part)
        if obj is None:
            break
    return _plain(obj)


def _close(x, y, op, tol):
    if op == "==":
        return x == y
    if isinstance(x, list) and isinstance(y, list):
        return len(x) == len(y) and all(_close(a, b, op, tol) for a, b in zip(x, y, strict=True))
    if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
        return abs(float(x) - float(y)) <= tol
    return x == y


def _match(src, rep):
    sid = _lib.get_net_id(src)
    if sid and sid == _lib.get_net_id(rep):
        return "net_id"
    return "name" if src.get_name() == rep.get_name() else "class"


_out = []
for _i, _a in enumerate(ARGS.get("assertions") or []):
    _t = _a.get("type")
    _rec = {"index": _i, "type": _t}
    try:
        _src_t = _a.get("source") or "server"
        _src = _world(_src_t)
        _targets = [t for t in _client_targets(_a.get("clients")) if t != _src_t]
        if not _targets:
            raise LookupError(f"no client worlds to compare with (worlds: {sorted(_worlds)})")
        _found = _sources(_src, _a)
        if _t == "replicated":
            _missing, _copies = [], {}
            for _x in _found:
                for _c in _targets:
                    _r = _lib.find_replica(_x, _world(_c))
                    if _r is None:
                        _missing.append(f"{_x.get_actor_label()} -> {_c}")
                    else:
                        _copies.setdefault(_x.get_actor_label(), {})[_c] = f"{_r.get_name()} ({_match(_x, _r)})"
            _rec["actual"] = {"on_" + _src_t: len(_found), "replicas": _copies}
            if _missing:
                _rec["missing"] = _missing[:20]
            _rec["passed"] = len(_found) >= int(_a.get("min", 1)) and not _missing
        elif _t == "replicated_property":
            if len(_found) != 1:
                raise LookupError(f"target {_a.get('target')!r} matches {len(_found)} actors in {_src_t} (want 1)")
            _x = _found[0]
            _prop = _a["property"]
            _sv = _value(_x, _prop)
            _vals, _ok = {_src_t: _sv}, True
            _op, _tol = _a.get("op", "approx"), float(_a.get("tolerance", 1.0))
            for _c in _targets:
                _r = _lib.find_replica(_x, _world(_c))
                _vals[_c] = None if _r is None else _value(_r, _prop)
                _ok = _ok and _r is not None and _close(_vals[_c], _sv, _op, _tol)
            _rec["actual"] = _vals
            _rec["passed"] = bool(_ok)
        else:
            raise ValueError(f"unknown replication assertion type {_t!r}")
    except Exception as _e:
        _rec["passed"] = False
        _rec["error"] = f"{type(_e).__name__}: {_e}"
    if _a.get("name"):
        _rec["name"] = _a["name"]
    _out.append(_rec)
result = {"assertions": _out, "worlds": sorted(_worlds)}
