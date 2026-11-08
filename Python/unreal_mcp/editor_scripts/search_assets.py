# Content Browser search. ARGS: query (substring of name), class (e.g. StaticMesh), path (default /Game),
#                              recursive, limit
# ruff: noqa: F821
_ar = asset_registry()
_path = ARGS.get("path") or "/Game"
_q = (ARGS.get("query") or "").lower()
_cls = (ARGS.get("class") or "").lower()
_limit = int(ARGS.get("limit") or 100)

_found = []
_total = 0
for _ad in _ar.get_assets_by_path(_path, recursive=bool(ARGS.get("recursive", True))):
    _s = asset_summary(_ad)
    if _q and _q not in _s["name"].lower():
        continue
    if _cls and _s["class"].lower() != _cls:
        continue
    _total += 1
    if len(_found) < _limit:
        _found.append(_s)
result = {"total_matches": _total, "truncated": _total > _limit, "assets": _found}
