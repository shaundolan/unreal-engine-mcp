# Simple collision and collision profile on static mesh assets (and optionally on placed components).
# ARGS: meshes [paths], folder (all static meshes under it), recursive, collision: box | sphere | capsule |
#       kdop10x | kdop10y | kdop10z | kdop18 | kdop26 | convex | complex_as_simple | simple_and_complex |
#       use_default | none, replace (remove existing simple shapes first, default true),
#       convex {hull_count, max_hull_verts, hull_precision}, profile ("BlockAll", "NoCollision", ...),
#       actors [labels: set profile on their static mesh components too], save
# ruff: noqa: F821
_SHAPES = {"box": "BOX", "sphere": "SPHERE", "capsule": "CAPSULE", "kdop10x": "NDOP10_X", "kdop10y": "NDOP10_Y",
           "kdop10z": "NDOP10_Z", "kdop18": "NDOP18", "kdop26": "NDOP26"}
_TRACE = {"complex_as_simple": "CTF_USE_COMPLEX_AS_SIMPLE", "simple_and_complex": "CTF_USE_SIMPLE_AND_COMPLEX",
          "simple_as_complex": "CTF_USE_SIMPLE_AS_COMPLEX", "use_default": "CTF_USE_DEFAULT"}


def _meshes():
    paths = list(ARGS.get("meshes") or [])
    if ARGS.get("folder"):
        for p in unreal.EditorAssetLibrary.list_assets(ARGS["folder"], recursive=bool(ARGS.get("recursive", True))):
            ad = unreal.EditorAssetLibrary.find_asset_data(p)
            if str(ad.asset_class_path.asset_name) == "StaticMesh":
                paths.append(str(p).split(".")[0])
    return paths


def _one(path, sub, kind, profile):
    mesh = unreal.load_asset(path)
    if not isinstance(mesh, unreal.StaticMesh):
        raise LookupError(f"static mesh {path!r} not found")
    if kind:
        if kind == "none" or (ARGS.get("replace", True) and (kind in _SHAPES or kind == "convex")):
            sub.remove_collisions(mesh)
        if kind in _SHAPES:
            if sub.add_simple_collisions(mesh, getattr(unreal.ScriptCollisionShapeType, _SHAPES[kind])) < 0:
                raise RuntimeError("add_simple_collisions failed")
        elif kind == "convex":
            c = ARGS.get("convex") or {}
            if not sub.set_convex_decomposition_collisions(mesh, int(c.get("hull_count", 4)),
                                                           int(c.get("max_hull_verts", 16)),
                                                           int(c.get("hull_precision", 100000))):
                raise RuntimeError("set_convex_decomposition_collisions failed")
        elif kind not in _TRACE and kind != "none":
            raise ValueError(f"unknown collision {kind!r}; expected {sorted([*_SHAPES, *_TRACE, 'convex', 'none'])}")
    bs = mesh.get_editor_property("body_setup")
    if bs is not None:
        if kind in _TRACE:
            bs.set_editor_property("collision_trace_flag", getattr(unreal.CollisionTraceFlag, _TRACE[kind]))
        elif kind in _SHAPES or kind == "convex":
            # simple shapes are only used for queries when the trace flag is not complex-as-simple
            if enum_name(bs.get_editor_property("collision_trace_flag")) == "CTF_USE_COMPLEX_AS_SIMPLE":
                bs.set_editor_property("collision_trace_flag", unreal.CollisionTraceFlag.CTF_USE_DEFAULT)
        if profile:
            bi = bs.get_editor_property("default_instance")
            bi.set_editor_property("collision_profile_name", profile)
            bs.set_editor_property("default_instance", bi)
    if ARGS.get("save", True):
        unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)
    return {"mesh": mesh.get_path_name(), "simple_shapes": sub.get_simple_collision_count(mesh),
            "convex_hulls": sub.get_convex_collision_count(mesh),
            "trace": enum_name(bs.get_editor_property("collision_trace_flag")) if bs else None,
            "profile": str(bs.get_editor_property("default_instance").get_editor_property("collision_profile_name"))
            if bs else None}


def _run():
    sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    kind = (ARGS.get("collision") or "").lower() or None
    profile = ARGS.get("profile") or None
    out, errors = [], {}
    for p in _meshes():
        try:
            out.append(_one(p, sub, kind, profile))
        except Exception as e:
            errors[p] = f"{type(e).__name__}: {e}"
    actors = []
    for label in ARGS.get("actors") or []:
        try:
            a = require_actor(label)
            comps = a.get_components_by_class(unreal.StaticMeshComponent)
            for c in comps:
                if profile:
                    c.set_collision_profile_name(profile)
            actors.append({"actor": a.get_actor_label(), "components": len(comps)})
        except Exception as e:
            errors[label] = f"{type(e).__name__}: {e}"
    r = {"meshes": out, "actors": actors, "errors": errors}
    if not out and not actors and not errors:
        errors["args"] = "nothing to do: pass meshes, folder or actors"
    if errors:
        r["success"] = False
        r["error"] = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:6])
    return r


with unreal.ScopedEditorTransaction("MCP mesh_collision"):
    result = _run()
