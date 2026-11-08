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
        if hasattr(_maps, "save_config"):
            _maps.save_config()
        else:
            # UE 5.8 Python has no SaveConfig: write the keys into the project's DefaultEngine.ini
            # section, which is what saving the settings object does.
            import os as _os
            import re as _re
            _ini = _os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_config_dir()), "DefaultEngine.ini")
            _sec = "[/Script/EngineSettings.GameMapsSettings]"
            _vals = {}
            if "default_game_mode" in _changed:
                _vals["GlobalDefaultGameMode"] = _cls(ARGS["default_game_mode"]).get_path_name()
            for _arg, _key in (("editor_startup_map", "EditorStartupMap"), ("game_default_map", "GameDefaultMap")):
                if ARGS.get(_arg):
                    _p = ARGS[_arg]
                    _vals[_key] = _p if "." in _p else f"{_p}.{_p.rsplit('/', 1)[-1]}"
            _text = open(_ini, encoding="utf-8").read() if _os.path.exists(_ini) else ""
            if _sec not in _text:
                _text = _text.rstrip("\n") + f"\n\n{_sec}\n"
            _head, _rest = _text.split(_sec, 1)
            _body, _sep, _tail = _rest.partition("\n[")
            for _k, _v in _vals.items():
                if _re.search(rf"^{_k}=", _body, _re.M):
                    _body = _re.sub(rf"^{_k}=.*$", f"{_k}={_v}", _body, flags=_re.M)
                else:
                    _body = _body.rstrip("\n") + f"\n{_k}={_v}\n"
            with open(_ini, "w", encoding="utf-8") as _f:
                _f.write(_head + _sec + _body + (_sep + _tail if _sep else ""))
            _changed.append("ini:" + _ini)


def _s(v):
    return v.get_path_name() if hasattr(v, "get_path_name") else str(v) if v is not None else None


result = {
    "changed": _changed,
    "level_game_mode": _s(_ws.get_editor_property("default_game_mode")),
    "default_game_mode": _s(_maps.get_editor_property("global_default_game_mode")),
    "editor_startup_map": _s(_maps.get_editor_property("editor_startup_map")),
    "game_default_map": _s(_maps.get_editor_property("game_default_map")),
}
