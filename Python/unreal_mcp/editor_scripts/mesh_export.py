# Export a mesh asset to a temp file on the editor machine (auto_rig works on files; the MCP server fetches it with
# download_chunk when the editor is remote). ARGS: asset (StaticMesh / SkeletalMesh path), format fbx|glb
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
    first = (ARGS.get("format") or "fbx").lower()
    for fmt in (first, "glb" if first == "fbx" else "fbx"):
        path = _try(obj, fmt)
        if path:
            return {"path": path, "size": _os.path.getsize(path), "format": fmt}
    raise RuntimeError(f"export of {ARGS['asset']} failed (FBX exporter, and glTF exporter plugin)")


result = _export()
