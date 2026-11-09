"""Content Browser tools: search, dependency graph, batched management, import."""

from __future__ import annotations

import base64
import ipaddress
import socket
from pathlib import Path
from typing import Any

from .python_exec import run_editor_script, run_python

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
CHUNK_BYTES = 8 * 1024 * 1024  # raw bytes per upload call (about 11 MB of base64 per request)


def editor_is_local(get_conn) -> bool:
    """True when the editor runs on this machine, so the importer can read our files directly."""
    try:
        host = get_conn().settings.host
    except Exception:
        return False
    if host.lower() in ("localhost", socket.gethostname().lower()):
        return True
    try:
        return ipaddress.ip_address(socket.gethostbyname(host)).is_loopback
    except (OSError, ValueError):
        return False


def upload_file(get_conn, path: Path) -> dict[str, Any]:
    """Copy a local file to a temp file on the editor machine in CHUNK_BYTES pieces, one small script per piece
    (never the whole file as one base64 literal). Returns {success, path (on the editor machine), size}."""
    size = path.stat().st_size
    dest, written = "", 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_BYTES)
            r = run_editor_script(get_conn, "upload_chunk", {
                "name": path.name, "dest": dest, "append": bool(dest),
                "data_b64": base64.b64encode(chunk).decode()}, timeout_s=300)
            if not r.get("success"):
                return r
            dest, written = r["path"], r["size"]
            if len(chunk) < CHUNK_BYTES:
                break
    if written != size:
        return {"success": False, "error": f"upload of {path} wrote {written} of {size} bytes"}
    return {"success": True, "path": dest, "size": size}


def collect_all_garbage(get_conn) -> dict[str, Any]:
    """CPython's cyclic GC, then the engine's, in a call of its own so no script reference is alive."""
    return run_python(get_conn, "import gc\ngc.collect()\nunreal.SystemLibrary.collect_garbage()\nresult = True",
                      timeout_s=300, collect=True)


def import_assets(get_conn, files: list[str] | None = None, local_files: list[str] | None = None,
                  destination: str = "/Game/Imported", replace_existing: bool = False, save: bool = True,
                  destination_name: str = "", transfer: str = "auto") -> dict[str, Any]:
    """Import editor-machine files and/or files from this machine (shared with blender_run).

    transfer (for local_files): "auto" hands the paths straight to the importer when the editor is on this
    machine and uploads them in chunks otherwise; "path" / "upload" force one or the other."""
    if transfer not in ("auto", "path", "upload"):
        return {"success": False, "error": "transfer must be auto, path or upload"}
    editor_files = list(files or [])
    local = [Path(f).expanduser().resolve() for f in local_files or []]
    for p in local:
        if not p.is_file():
            return {"success": False, "error": f"{p} not found on the MCP server machine"}
        if p.stat().st_size > MAX_UPLOAD_BYTES:
            return {"success": False, "error": f"{p} is larger than {MAX_UPLOAD_BYTES} bytes"}
    if not (editor_files or local):
        return {"success": False, "error": "pass files (editor machine) or local_files (this machine)"}
    direct = transfer == "path" or (transfer == "auto" and editor_is_local(get_conn))
    cleanup: list[str] = []
    for p in local:
        if direct:
            editor_files.append(str(p))
            continue
        up = upload_file(get_conn, p)
        if not up.get("success"):
            return up
        editor_files.append(up["path"])
        cleanup.append(up["path"])
    if replace_existing:
        # Re-importing over live meshes/materials frees UObjects. A Python wrapper still pointing at one (kept in a
        # reference cycle by an earlier script) crashed the editor later in CPython's GC (_PyWeakref_ClearRef).
        # Collect both heaps while those objects are still valid, and again after the import.
        collect_all_garbage(get_conn)
    r = run_editor_script(get_conn, "asset_import", {
        "files": editor_files, "destination": destination, "replace_existing": replace_existing, "save": save,
        "destination_name": destination_name, "cleanup": cleanup}, timeout_s=900, collect=True)
    if replace_existing:
        collect_all_garbage(get_conn)
    if r.get("success") and local:
        r["transfer"] = "path" if direct else "upload"
    return r


def register(mcp, get_conn):
    @mcp.tool()
    def search_assets(query: str = "", class_name: str = "", path: str = "/Game", recursive: bool = True,
                      limit: int = 100) -> dict[str, Any]:
        """Search the Content Browser. query: case-insensitive substring of the asset name.
        class_name: exact asset class, e.g. StaticMesh, Material, MaterialInstanceConstant,
        Texture2D, Blueprint, NiagaraSystem, SoundWave, World. Returns full object paths
        (/Game/Dir/Asset.Asset) usable by every other tool. Engine content: path="/Engine"."""
        return run_editor_script(get_conn, "search_assets", {
            "query": query, "class": class_name, "path": path, "recursive": recursive, "limit": limit})

    @mcp.tool()
    def asset_references(asset: str, direction: str = "dependencies", depth: int = 1,
                         limit: int = 500) -> dict[str, Any]:
        """Package dependency graph. direction="dependencies": what this asset pulls in;
        direction="referencers": what uses it (check before deleting or renaming).
        depth > 1 walks transitively."""
        return run_editor_script(get_conn, "asset_references", {
            "asset": asset, "direction": direction, "depth": depth, "limit": limit})

    @mcp.tool()
    def asset_manage(operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Batched asset operations, each reported separately:
          {"op":"duplicate","source":"/Game/A","destination":"/Game/B"}
          {"op":"rename","source":...,"destination":...}  (fixes up references)
          {"op":"delete","path":...}  {"op":"save","path":...}  {"op":"save_all_dirty"}
          {"op":"make_folder","path":"/Game/Props"}  {"op":"exists","path":...}
          {"op":"set_properties","path":"/Game/SM_Rock","properties":{"lod_group":"SmallProp"}}
        Use asset_references(direction="referencers") before delete."""
        return run_editor_script(get_conn, "asset_manage", {"operations": operations}, timeout_s=300)

    @mcp.tool()
    def asset_import(files: list[str] | None = None, local_files: list[str] | None = None,
                     destination: str = "/Game/Imported", replace_existing: bool = False,
                     save: bool = True, destination_name: str = "", transfer: str = "auto") -> dict[str, Any]:
        """Import FBX, glTF/GLB, OBJ, textures (PNG/TGA/EXR/JPG), audio (WAV) and more.

        files: paths on the EDITOR machine.
        local_files: paths on the machine running this MCP server. When the editor runs on the
                     same machine they go straight to the importer; otherwise they are copied
                     over in 8 MB chunks (transfer="auto"; force "path" or "upload").
        replace_existing: re-import over existing assets. Python and engine garbage are
                     collected before and after, so no stale Python wrapper can crash the editor.
        Imports are automated (no dialogs). Returns the created asset paths per file; a file
        that imported nothing is listed in failed and makes success false.
        """
        return import_assets(get_conn, files=files, local_files=local_files, destination=destination,
                             replace_existing=replace_existing, save=save, destination_name=destination_name,
                             transfer=transfer)
