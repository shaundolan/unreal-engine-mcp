# Enhanced Input assets. ARGS: folder (/Game/Input), actions [{name, value_type}],
#   contexts [{name, mappings: [{action, key, modifiers?: ["Negate","SwizzleAxis"]}]}]
# value_type: Boolean | Axis1D | Axis2D | Axis3D. Keys: names from unreal.Keys / FKey ("W", "SpaceBar",
# "Gamepad_LeftX", "LeftMouseButton", "Mouse2D"...).
# ruff: noqa: F821
_folder = (ARGS.get("folder") or "/Game/Input").rstrip("/")
_eal = unreal.EditorAssetLibrary
_tools = unreal.AssetToolsHelpers.get_asset_tools()
_VT = {"boolean": "BOOLEAN", "bool": "BOOLEAN", "axis1d": "AXIS1D", "axis2d": "AXIS2D", "axis3d": "AXIS3D"}


def _load_or_create(name, cls, factory_cls):
    path = f"{_folder}/{name}"
    if _eal.does_asset_exist(path):
        return unreal.load_asset(path), False
    return _tools.create_asset(name, _folder, cls, factory_cls()), True


_actions = {}
_report = {"actions": [], "contexts": []}
for _a in ARGS.get("actions") or []:
    _ia, _new = _load_or_create(_a["name"], unreal.InputAction, unreal.InputAction_Factory)
    _vt = _VT.get(str(_a.get("value_type", "Boolean")).lower())
    if _vt is None:
        raise ValueError(f"bad value_type {_a.get('value_type')!r}")
    _ia.set_editor_property("value_type", getattr(unreal.InputActionValueType, _vt))
    _eal.save_loaded_asset(_ia, only_if_is_dirty=False)
    _actions[_a["name"]] = _ia
    _report["actions"].append({"path": _ia.get_path_name(), "created": _new, "value_type": _vt})

def _maps_get(imc):
    """UE 5.6+ keeps mappings in default_key_mappings.mappings; the top-level `mappings` is legacy."""
    try:
        data = imc.get_editor_property("default_key_mappings")
        return list(data.get_editor_property("mappings")), data
    except Exception:
        return list(imc.get_editor_property("mappings")), None


def _maps_set(imc, maps, data):
    if data is not None:
        data.set_editor_property("mappings", maps)
        imc.set_editor_property("default_key_mappings", data)
    else:
        imc.set_editor_property("mappings", maps)


for _c in ARGS.get("contexts") or []:
    _imc, _new = _load_or_create(_c["name"], unreal.InputMappingContext, unreal.InputMappingContext_Factory)
    if _c.get("replace", True):
        _imc.unmap_all()
    _maps = []
    for _m in _c.get("mappings") or []:
        _ia = _actions.get(_m["action"]) or unreal.load_asset(
            _m["action"] if _m["action"].startswith("/") else f"{_folder}/{_m['action']}")
        if _ia is None:
            raise LookupError(f"input action {_m['action']!r} not found")
        _key = unreal.Key()
        _key.set_editor_property("key_name", _m["key"])
        _mapping = _imc.map_key(_ia, _key)
        _mods = []
        for _mod in _m.get("modifiers") or []:
            _cls = getattr(unreal, "InputModifier" + _mod, None)
            if _cls is None:
                raise LookupError(f"no modifier InputModifier{_mod}")
            _mods.append(unreal.new_object(_cls, _imc))
        if _mods:
            # map_key returns a copy; write modifiers back through the mappings array.
            _all, _data = _maps_get(_imc)
            if not _all:
                raise RuntimeError("map_key added no mapping (unknown key name?)")
            _last = _all[-1]
            _last.set_editor_property("modifiers", _mods)
            _all[-1] = _last
            _maps_set(_imc, _all, _data)
        _maps.append(f"{_m['key']} -> {_ia.get_name()}" + (f" [{', '.join(_m['modifiers'])}]" if _mods else ""))
    _eal.save_loaded_asset(_imc, only_if_is_dirty=False)
    _report["contexts"].append({"path": _imc.get_path_name(), "created": _new, "mappings": _maps})
result = _report
