# Batched asset operations. ARGS: operations [...]
#   {op: duplicate, source, destination}   {op: rename, source, destination}
#   {op: delete, path}                     {op: save, path}   {op: save_all_dirty}
#   {op: make_folder, path}                {op: exists, path}
#   {op: set_properties, path, properties} (editor properties on the asset, then saved)
# ruff: noqa: F821
_eal = unreal.EditorAssetLibrary
_results = []
for _i, _op in enumerate(ARGS.get("operations") or []):
    _kind = _op.get("op")
    try:
        if _kind == "duplicate":
            _obj = _eal.duplicate_asset(_op["source"], _op["destination"])
            _r = {"created": _obj.get_path_name() if _obj else None}
            if _obj is None:
                raise RuntimeError("duplicate failed (destination exists?)")
        elif _kind == "rename":
            if not _eal.rename_asset(_op["source"], _op["destination"]):
                raise RuntimeError("rename failed")
            _r = {"renamed": _op["destination"]}
        elif _kind == "delete":
            if not _eal.delete_asset(_op["path"]):
                raise RuntimeError("delete failed (still referenced?)")
            _r = {"deleted": _op["path"]}
            # A force-deleted object can linger in memory and block a later import / create at the same path.
            _rel = release_stale_path(_op["path"])
            if _rel:
                _r["released_stale"] = _rel
        elif _kind == "save":
            _r = {"saved": bool(_eal.save_asset(_op["path"], only_if_is_dirty=False))}
        elif _kind == "save_all_dirty":
            _r = {"saved": bool(_eal.save_directory("/Game", only_if_is_dirty=True, recursive=True))}
        elif _kind == "make_folder":
            _r = {"created": bool(_eal.make_directory(_op["path"]))}
        elif _kind == "exists":
            _r = {"exists": bool(_eal.does_asset_exist(_op["path"]))}
        elif _kind == "set_properties":
            _obj = unreal.load_asset(_op["path"])
            if _obj is None:
                raise LookupError(f"asset {_op['path']!r} not found")
            _done = set_props(_obj, _op.get("properties"))
            _eal.save_loaded_asset(_obj, only_if_is_dirty=False)
            _r = {"properties_set": _done}
        else:
            raise ValueError(f"unknown op {_kind!r}")
        _results.append({"index": _i, "ok": True, **_r})
    except Exception as _e:
        _results.append({"index": _i, "ok": False, "error": f"{type(_e).__name__}: {_e}"})
result = {"succeeded": sum(1 for r in _results if r["ok"]),
          "failed": sum(1 for r in _results if not r["ok"]), "results": _results}
