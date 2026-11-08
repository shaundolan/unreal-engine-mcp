# Project / level game framework settings.
# ARGS: level_game_mode (class/BP path, override for the current level), default_game_mode,
#       editor_startup_map, game_default_map (/Game/Maps/X), read_only (bool)
# ruff: noqa: F821
_maps = unreal.GameMapsSettings.get_game_maps_settings()
_ws = editor_world().get_world_settings()
_changed = []


def _cls(path):
    c = resolve_class(path)
    if c is None:
        raise LookupError(f"class {path!r} not found (Blueprint classes: /Game/BP_X.BP_X_C)")
    return c


if not ARGS.get("read_only"):
    if ARGS.get("level_game_mode"):
        _ws.set_editor_property("default_game_mode", _cls(ARGS["level_game_mode"]))
        _changed.append("level_game_mode")
    if ARGS.get("default_game_mode"):
        _maps.set_editor_property("global_default_game_mode",
                                  unreal.SoftClassPath(_cls(ARGS["default_game_mode"]).get_path_name()))
        _changed.append("default_game_mode")
    for _arg, _prop in (("editor_startup_map", "editor_startup_map"), ("game_default_map", "game_default_map")):
        if ARGS.get(_arg):
            _maps.set_editor_property(_prop, unreal.SoftObjectPath(ARGS[_arg]))
            _changed.append(_arg)
    if any(c != "level_game_mode" for c in _changed):
        _maps.save_config()


def _s(v):
    return v.get_path_name() if hasattr(v, "get_path_name") else str(v) if v is not None else None


result = {
    "changed": _changed,
    "level_game_mode": _s(_ws.get_editor_property("default_game_mode")),
    "default_game_mode": _s(_maps.get_editor_property("global_default_game_mode")),
    "editor_startup_map": _s(_maps.get_editor_property("editor_startup_map")),
    "game_default_map": _s(_maps.get_editor_property("game_default_map")),
}
