# Import files (FBX, GLB/glTF, OBJ, PNG/TGA/EXR, WAV, ...) from the EDITOR machine's disk.
# ARGS: files [paths on the editor machine], destination (/Game/...), replace_existing, save,
#       destination_name (single file only), cleanup [temp files to delete afterwards (chunked uploads)]
# Everything runs inside a function and only strings leave it: no wrapper of an imported or replaced object stays
# alive in the script's globals (stale wrappers of objects a re-import freed crashed CPython's GC).
# ruff: noqa: F821
import os as _os


def _import():
    files = list(ARGS.get("files") or [])
    dest = ARGS.get("destination") or "/Game/Imported"
    replace = bool(ARGS.get("replace_existing", False))
    for f in files:
        if not _os.path.isfile(f):
            raise FileNotFoundError(f"{f} does not exist on the editor machine")
    tasks, existing, released = [], {}, {}
    for f in files:
        t = unreal.AssetImportTask()
        t.set_editor_property("filename", f)
        t.set_editor_property("destination_path", dest)
        t.set_editor_property("automated", True)  # no option dialogs
        t.set_editor_property("replace_existing", replace)
        t.set_editor_property("save", bool(ARGS.get("save", True)))
        name = _os.path.splitext(_os.path.basename(f))[0]
        if ARGS.get("destination_name") and len(files) == 1:
            t.set_editor_property("destination_name", ARGS["destination_name"])
            name = ARGS["destination_name"]
        target = f"{dest}/{sanitize_asset_name(name)}"
        existing[f] = unreal.EditorAssetLibrary.does_asset_exist(target)
        if not existing[f]:
            # A force-deleted asset at this path may still be in memory: the importer would find it and refuse.
            released[f] = release_stale_path(target)
        tasks.append(t)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
    out = []
    for f, t in zip(files, tasks, strict=True):
        paths = [str(p) for p in (t.get_editor_property("imported_object_paths") or [])]
        o = {"file": f, "imported": paths, "ok": bool(paths)}
        if existing[f]:
            o["replaced" if replace else "existed"] = True
        if released.get(f):
            o["released_stale"] = released[f]
        if not paths and existing[f] and not replace:
            o["error"] = "an asset with this name already exists; pass replace_existing=true"
        out.append(o)
    tasks.clear()
    return out


def _cleanup():
    for f in ARGS.get("cleanup") or []:
        try:
            _os.remove(f)
            _os.rmdir(_os.path.dirname(f))
        except OSError:
            pass


try:
    _out = _import()
finally:
    _cleanup()
result = {"imports": _out, "failed": [o["file"] for o in _out if not o["ok"]]}
if result["failed"]:
    result["success"] = False
    result["error"] = "nothing imported from: " + ", ".join(result["failed"])
del _out
