# After importing a generated / auto-rigged mesh: name the mesh asset, generate LODs, report what landed.
# ARGS: imported [object paths from asset_import], destination, name (asset name wanted), kind static|skeletal,
#       lods [fraction of triangles kept per extra LOD, e.g. [0.5, 0.25, 0.125]], nanite (static only), save
# The importer names meshes after the file's node/mesh, not always after the file; this renames to `name`.
# Only strings leave the function (re-import safety: no wrappers kept in globals).
# ruff: noqa: F821


def _bounds(mesh):
    b = mesh.get_bounds()
    e = b.box_extent
    o = b.origin
    return {"size_cm": [round(2 * e.x, 2), round(2 * e.y, 2), round(2 * e.z, 2)],
            "min_cm": [round(o.x - e.x, 2), round(o.y - e.y, 2), round(o.z - e.z, 2)]}


def _rename(obj, dest, name):
    path = obj.get_path_name().split(".")[0]
    want = f"{dest}/{name}"
    if path == want:
        return path
    if unreal.EditorAssetLibrary.does_asset_exist(want):
        unreal.EditorAssetLibrary.delete_asset(want)
    if not unreal.EditorAssetLibrary.rename_asset(path, want):
        raise RuntimeError(f"could not rename {path} to {want}")
    return want


def _finalize():
    dest = ARGS.get("destination", "/Game/Generated").rstrip("/")
    name = ARGS["name"]
    want_cls = unreal.SkeletalMesh if ARGS.get("kind") == "skeletal" else unreal.StaticMesh
    loaded = [(p, unreal.load_asset(p)) for p in ARGS.get("imported") or []]
    meshes = [(p, o) for p, o in loaded if isinstance(o, want_cls)]
    if not meshes:
        kinds = sorted({type(o).__name__ for _, o in loaded if o is not None})
        raise LookupError(f"no {want_cls.__name__} among the imported assets ({kinds}); "
                          + ("the file has no skin/armature" if want_cls is unreal.SkeletalMesh else ""))
    out = {"others": [p for p, o in loaded if all(o is not m for _, m in meshes)]}
    mesh = meshes[0][1]
    out["mesh"] = _rename(mesh, dest, name)
    mesh = unreal.load_asset(out["mesh"])
    out["class"] = type(mesh).__name__
    out.update(_bounds(mesh))
    lods = [float(x) for x in ARGS.get("lods") or []]
    if want_cls is unreal.StaticMesh:
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        if lods:
            opts = unreal.StaticMeshReductionOptions()
            opts.set_editor_property("auto_compute_lod_screen_size", True)
            settings = []
            for frac in [1.0, *lods]:
                s = unreal.StaticMeshReductionSettings()
                s.set_editor_property("percent_triangles", max(0.01, min(1.0, frac)))
                settings.append(s)
            opts.set_editor_property("reduction_settings", settings)
            out["lod_count"] = int(sub.set_lods(mesh, opts))
        else:
            out["lod_count"] = int(sub.get_lod_count(mesh))
        if ARGS.get("nanite") is not None:
            ns = mesh.get_editor_property("nanite_settings")
            ns.set_editor_property("enabled", bool(ARGS["nanite"]))
            sub.set_nanite_settings(mesh, ns, apply_changes=True)
        ns = mesh.get_editor_property("nanite_settings")
        out["nanite"] = bool(ns.get_editor_property("enabled"))
        # with Nanite on, LOD0 render data is the Nanite fallback mesh (coarser than the source)
        out["fallback_triangles" if out["nanite"] else "triangles_lod0"] = int(mesh.get_num_triangles(0))
        out["materials"] = [str(m.get_editor_property("material_interface").get_path_name())
                            if m.get_editor_property("material_interface") else None
                            for m in mesh.get_editor_property("static_materials")]
    else:
        sk = mesh.get_editor_property("skeleton")
        out["skeleton"] = sk.get_path_name().split(".")[0] if sk else None
        pa = mesh.get_editor_property("physics_asset")
        out["physics_asset"] = pa.get_path_name().split(".")[0] if pa else None
        if lods:
            out["lods_regenerated"] = bool(unreal.SkeletalMeshEditorSubsystem.regenerate_lod(mesh, len(lods) + 1))
        out["materials"] = [str(m.get_editor_property("material_interface").get_path_name())
                            if m.get_editor_property("material_interface") else None
                            for m in mesh.get_editor_property("materials")]
    if ARGS.get("save", True):
        unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)
    return out


result = _finalize()
