# Export a mesh asset to a temp file on the editor machine (auto_rig works on files; the MCP server fetches it with
# download_chunk when the editor is remote). ARGS: asset (StaticMesh / SkeletalMesh path), format fbx|glb,
# prefer_source (default true: hand back the file the asset was imported from when it still exists; it has full
# detail, while exporting a Nanite mesh writes its coarse fallback mesh)
# ruff: noqa: F821
import os as _os
import tempfile as _tmp


def _try(obj, fmt):
    path = _os.path.join(_tmp.mkdtemp(prefix="unreal_mcp_export_"), f"{obj.get_name()}.{fmt}")
    task = unreal.AssetExportTask()
    task.set_editor_property("object", obj)
    task.set_editor_property("filename", path)
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    if fmt == "fbx":
        exporter = unreal.StaticMeshExporterFBX() if isinstance(obj, unreal.StaticMesh) else unreal.SkeletalMeshExporterFBX()
    elif hasattr(unreal, "GLTFExporter"):  # glTF Exporter plugin
        exporter = None  # picked from the .glb extension
    else:
        return None
    if exporter is not None:
        task.set_editor_property("exporter", exporter)
    if unreal.Exporter.run_asset_export_task(task) and _os.path.isfile(path):
        return path
    return None


def _export():
    obj = unreal.load_asset(ARGS["asset"])
    if obj is None:
        raise LookupError(f"asset {ARGS['asset']!r} not found")
    if ARGS.get("prefer_source", True):
        aid = obj.get_editor_property("asset_import_data") if hasattr(obj, "get_editor_property") else None
        src = aid.get_first_filename() if aid is not None else ""
        if src and _os.path.isfile(src):
            return {"path": src, "size": _os.path.getsize(src), "format": _os.path.splitext(src)[1][1:].lower(),
                    "source_file": True}
    first = (ARGS.get("format") or "fbx").lower()
    nanite = None
    if isinstance(obj, unreal.StaticMesh):
        ns = obj.get_editor_property("nanite_settings")
        if ns.get_editor_property("enabled"):  # export the full mesh, not the Nanite fallback
            nanite = ns
            off = obj.get_editor_property("nanite_settings")
            off.set_editor_property("enabled", False)
            unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).set_nanite_settings(obj, off, True)
    try:
        for fmt in (first, "glb" if first == "fbx" else "fbx"):
            path = _try(obj, fmt)
            if path:
                return {"path": path, "size": _os.path.getsize(path), "format": fmt}
    finally:
        if nanite is not None:
            nanite.set_editor_property("enabled", True)
            unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).set_nanite_settings(obj, nanite, True)
    raise RuntimeError(f"export of {ARGS['asset']} failed (FBX exporter, and glTF exporter plugin)")


result = _export()
