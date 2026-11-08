# Place Niagara systems and set user parameters.
# ARGS: operations [...]
#   {op: spawn, system: "/Game/FX/NS_Fire", label, location, rotation, scale, parameters {..}, auto_activate}
#   {op: set, target: label, parameters {"User.Rate": 50, "Color": [1,0,0,1], "Offset": [0,0,100], "On": true}}
# Parameter names: with or without the "User." prefix. number->float (int if "int:" prefix on the
# key), [x,y,z]->vec3, [r,g,b,a]/{"r":..}->color, bool->bool.
# ruff: noqa: F821


def _component(actor):
    comp = actor.get_component_by_class(unreal.NiagaraComponent)
    if comp is None:
        raise LookupError(f"{actor.get_actor_label()} has no NiagaraComponent")
    return comp


def _set_param(comp, key, value):
    as_int = key.startswith("int:")
    name = key[4:] if as_int else key
    if not name.startswith("User."):
        name = "User." + name
    if isinstance(value, bool):
        comp.set_variable_bool(name, value)
    elif as_int:
        comp.set_variable_int(name, int(value))
    elif isinstance(value, (int, float)):
        comp.set_variable_float(name, float(value))
    elif isinstance(value, dict) or (isinstance(value, list) and len(value) == 4):
        comp.set_variable_linear_color(name, color(value))
    elif isinstance(value, list) and len(value) == 3:
        comp.set_variable_vec3(name, vec(value))
    elif isinstance(value, str) and value.startswith("/"):
        obj = unreal.load_asset(value)
        if obj is None:
            raise LookupError(f"asset {value!r} not found")
        comp.set_variable_object(name, obj)
    else:
        raise TypeError(f"unsupported value for {key}: {value!r}")
    return name


_results = []
with unreal.ScopedEditorTransaction("MCP niagara"):
    for _i, _op in enumerate(ARGS.get("operations") or []):
        try:
            if _op.get("op") == "spawn":
                _sys = unreal.load_asset(_op["system"])
                if _sys is None:
                    raise LookupError(f"Niagara system {_op['system']!r} not found (search_assets class NiagaraSystem)")
                _a = actor_sub().spawn_actor_from_object(_sys, vec(_op.get("location")), rot(_op.get("rotation")))
                if _a is None:
                    raise RuntimeError("spawn failed")
                if _op.get("label"):
                    _a.set_actor_label(_op["label"])
                if _op.get("scale") is not None:
                    _a.set_actor_scale3d(vec(_op["scale"]))
                _c = _component(_a)
                if "auto_activate" in _op:
                    _c.set_auto_activate(bool(_op["auto_activate"]))
                _set = [_set_param(_c, k, v) for k, v in (_op.get("parameters") or {}).items()]
                _results.append({"index": _i, "ok": True, "spawned": _a.get_actor_label(), "parameters_set": _set})
            elif _op.get("op") == "set":
                _a = require_actor(_op["target"])
                _c = _component(_a)
                _set = [_set_param(_c, k, v) for k, v in (_op.get("parameters") or {}).items()]
                _results.append({"index": _i, "ok": True, "target": _a.get_actor_label(), "parameters_set": _set})
            else:
                raise ValueError(f"unknown op {_op.get('op')!r}; expected spawn or set")
        except Exception as _e:
            _results.append({"index": _i, "ok": False, "error": f"{type(_e).__name__}: {_e}"})
result = {"succeeded": sum(1 for r in _results if r["ok"]), "failed": sum(1 for r in _results if not r["ok"]),
          "results": _results}
