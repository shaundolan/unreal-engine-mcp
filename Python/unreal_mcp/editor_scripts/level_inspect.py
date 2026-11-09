# Persistent level, sublevels / streaming, World Partition, world settings. ARGS: (none)
# ruff: noqa: F821
_w = editor_world()
if _w is None:
    raise RuntimeError("no editor world")
_out = {"world": _w.get_path_name()}

_levels = []
try:
    for _lvl in unreal.EditorLevelUtils.get_levels(_w):
        _levels.append(_lvl.get_outer().get_path_name() if _lvl.get_outer() else _lvl.get_path_name())
except Exception as _e:
    _levels = [f"<unavailable: {_e}>"]
_out["loaded_levels"] = _levels

_streaming = []
try:
    for _s in _w.get_editor_property("streaming_levels") or []:
        _streaming.append({"package": str(_s.get_editor_property("world_asset")),
                           "class": class_name_of(_s)})
except Exception:
    pass
_out["streaming_levels"] = _streaming

_ws = _w.get_world_settings()
_settings = {}
for _k in ("kill_z", "world_gravity_z", "global_gravity_set", "default_game_mode", "enable_world_bounds_checks"):
    try:
        _v = _ws.get_editor_property(_k)
        _settings[_k] = _v.get_path_name() if hasattr(_v, "get_path_name") else _v
    except Exception:
        pass
_out["world_settings"] = _settings
try:
    _out["world_partition"] = _ws.get_editor_property("world_partition") is not None
except Exception:
    _out["world_partition"] = None
_out["actor_count"] = len(all_actors())
result = _out
