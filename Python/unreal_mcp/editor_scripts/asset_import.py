# Import files (FBX, GLB/glTF, OBJ, PNG/TGA/EXR, WAV, ...) from the EDITOR machine's disk.
# ARGS: files [paths on the editor machine], blobs [{name, data_b64}] (files uploaded from the MCP
#       server machine), destination (/Game/...), replace_existing, save, destination_name (single file only)
# ruff: noqa: F821
import base64 as _b64
import os as _os
import tempfile as _tmp

_tasks = []
_files = list(ARGS.get("files") or [])
if ARGS.get("blobs"):
    _dir = _tmp.mkdtemp(prefix="unreal_mcp_import_")
    for _b in ARGS["blobs"]:
        _dst = _os.path.join(_dir, _os.path.basename(_b["name"]))
        with open(_dst, "wb") as _fh:
            _fh.write(_b64.b64decode(_b["data_b64"]))
        _files.append(_dst)
for _f in _files:
    if not _os.path.isfile(_f):
        raise FileNotFoundError(f"{_f} does not exist on the editor machine")
    _t = unreal.AssetImportTask()
    _t.set_editor_property("filename", _f)
    _t.set_editor_property("destination_path", ARGS.get("destination") or "/Game/Imported")
    _t.set_editor_property("automated", True)  # no option dialogs
    _t.set_editor_property("replace_existing", bool(ARGS.get("replace_existing", False)))
    _t.set_editor_property("save", bool(ARGS.get("save", True)))
    if ARGS.get("destination_name") and len(_files) == 1:
        _t.set_editor_property("destination_name", ARGS["destination_name"])
    _tasks.append(_t)

unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(_tasks)
_out = []
for _f, _t in zip(_files, _tasks, strict=True):
    _paths = [str(p) for p in (_t.get_editor_property("imported_object_paths") or [])]
    _out.append({"file": _f, "imported": _paths, "ok": bool(_paths)})
result = {"imports": _out, "failed": [o["file"] for o in _out if not o["ok"]]}
