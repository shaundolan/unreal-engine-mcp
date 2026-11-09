# Read / write C++ under the project's Source/ and Plugins/*/Source on the EDITOR machine.
# ARGS: action: list | read | write, path (relative to the project dir), content (write), pattern (list)
# ruff: noqa: F821
import fnmatch as _fn
import os as _os

_root = _os.path.realpath(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
_ALLOWED_TOP = ("Source", "Plugins")
_EXT = (".h", ".hpp", ".cpp", ".inl", ".cs", ".ini", ".uplugin", ".uproject")


def _resolve(rel):
    full = _os.path.realpath(_os.path.join(_root, rel))
    try:
        inside = _os.path.relpath(full, _root)
    except ValueError:  # Windows: another drive
        inside = ".."
    # Windows paths are case-insensitive ("source/x.cpp" is Source/x.cpp)
    top = _os.path.normcase(inside.split(_os.sep)[0])
    if inside.startswith("..") or top not in {_os.path.normcase(t) for t in _ALLOWED_TOP + ("Config",)}:
        raise PermissionError(f"{rel!r} is outside Source/, Plugins/ or Config/")
    return full


_action = ARGS.get("action") or "list"
if _action == "list":
    _pat = ARGS.get("pattern") or "*"
    _files = []
    for _top in _ALLOWED_TOP:
        for _dp, _dn, _fs in _os.walk(_os.path.join(_root, _top)):
            _dn[:] = [d for d in _dn if d not in ("Binaries", "Intermediate")]
            for _f in _fs:
                _rel = _os.path.relpath(_os.path.join(_dp, _f), _root).replace(_os.sep, "/")
                if _f.endswith(_EXT) and _fn.fnmatch(_rel, _pat):
                    _files.append(_rel)
    result = {"project_dir": _root, "files": sorted(_files)[:2000]}
elif _action == "read":
    with open(_resolve(ARGS["path"]), encoding="utf-8", errors="replace") as _fh:
        _text = _fh.read()
    result = {"path": ARGS["path"], "content": _text, "lines": _text.count("\n") + 1}
elif _action == "write":
    _full = _resolve(ARGS["path"])
    if not _full.endswith(_EXT):
        raise PermissionError(f"refusing to write {ARGS['path']!r}: extension not in {_EXT}")
    _os.makedirs(_os.path.dirname(_full), exist_ok=True)
    _existed = _os.path.exists(_full)
    with open(_full, "w", encoding="utf-8", newline="\n") as _fh:
        _fh.write(ARGS["content"])
    result = {"path": ARGS["path"], "created": not _existed, "bytes": len(ARGS["content"].encode("utf-8"))}
else:
    raise ValueError("action must be list, read or write")
