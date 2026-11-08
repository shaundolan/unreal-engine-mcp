# Dependency graph walk. ARGS: asset (path or package), direction: dependencies | referencers, depth, limit
# ruff: noqa: F821
_ar = asset_registry()
_pkg = ARGS["asset"].split(".")[0]
_dir = ARGS.get("direction") or "dependencies"
if _dir not in ("dependencies", "referencers"):
    raise ValueError("direction must be 'dependencies' or 'referencers'")
_depth = max(1, int(ARGS.get("depth") or 1))
_limit = int(ARGS.get("limit") or 500)
_opts = unreal.AssetRegistryDependencyOptions(
    include_soft_package_references=True, include_hard_package_references=True,
    include_searchable_names=False, include_soft_management_references=False,
    include_hard_management_references=False)

_seen = {_pkg: 0}
_frontier = [_pkg]
_edges = []
for _level in range(1, _depth + 1):
    _next = []
    for _p in _frontier:
        if _dir == "dependencies":
            _linked = _ar.get_dependencies(unreal.Name(_p), _opts) or []
        else:
            _linked = _ar.get_referencers(unreal.Name(_p), _opts) or []
        for _l in _linked:
            _l = str(_l)
            _edges.append([_p, _l])
            if _l not in _seen and len(_seen) < _limit:
                _seen[_l] = _level
                _next.append(_l)
    _frontier = _next
    if not _frontier:
        break

result = {"root": _pkg, "direction": _dir,
          "packages": [{"package": k, "depth": d} for k, d in _seen.items() if k != _pkg],
          "edges": _edges[:_limit], "truncated": len(_seen) >= _limit}
