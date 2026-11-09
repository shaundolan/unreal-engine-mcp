# PCG graph structure and/or PCG volumes (graph, generated, spawned instance counts).
# include: _pcg
# ARGS: graph (path, optional), volume (label, optional; '' = every PCGVolume / PCG component actor)
# ruff: noqa: F821
result = {}
if ARGS.get("graph"):
    _g = unreal.load_asset(ARGS["graph"].split(".")[0])
    if _g is None:
        raise LookupError(f"PCG graph {ARGS['graph']!r} not found")
    result["graph"] = describe_graph(_g)
_vols = []
for _a in all_actors():
    if ARGS.get("volume") and _a.get_actor_label().lower() != ARGS["volume"].lower():
        continue
    if isinstance(_a, unreal.PCGVolume):
        _vols.append(pcg_volume_info(_a))
if ARGS.get("volume") and not _vols:
    raise LookupError(f"no PCG volume labelled {ARGS['volume']!r}")
result["volumes"] = _vols
