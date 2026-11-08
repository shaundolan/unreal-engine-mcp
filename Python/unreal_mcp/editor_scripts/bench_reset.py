# UE-Bench sandbox: delete the task's assets (except the loaded map) and open a fresh empty level.
# ARGS: root (/Game/UEBench/<task>), level_name (L_<task>)
# ruff: noqa: F821
_eal = unreal.EditorAssetLibrary
_root = ARGS["root"].rstrip("/")
if not _root.startswith("/Game/UEBench/"):
    raise ValueError("bench roots must live under /Game/UEBench/")
_current = editor_world().get_path_name().split(".")[0]
_deleted = 0
for _p in _eal.list_assets(_root, recursive=True, include_folder=False):
    _pkg = _p.split(".")[0]
    if _pkg != _current and _eal.delete_asset(_pkg):
        _deleted += 1
_n = 0
while True:
    _level = f"{_root}/{ARGS['level_name']}" + (f"_{_n}" if _n else "")
    if not _eal.does_asset_exist(_level):
        break
    _n += 1
if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).new_level(_level):
    raise RuntimeError(f"could not create level {_level}")
result = {"root": _root, "level": _level, "deleted_assets": _deleted, "actor_count": len(all_actors())}
