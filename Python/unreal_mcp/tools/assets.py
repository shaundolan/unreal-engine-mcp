"""Content Browser tools: search, dependency graph, batched management, import."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .python_exec import run_editor_script

MAX_UPLOAD_BYTES = 150 * 1024 * 1024  # stays well under the 256 MB frame limit after base64


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
                     save: bool = True, destination_name: str = "") -> dict[str, Any]:
        """Import FBX, glTF/GLB, OBJ, textures (PNG/TGA/EXR/JPG), audio (WAV) and more.

        files: paths on the EDITOR machine.
        local_files: paths on the machine running this MCP server; uploaded through the
                     connection, so this works when the editor is remote (e.g. a mesh just
                     exported from Blender on another box).
        Imports are automated (no dialogs). Returns the created asset paths per file.
        """
        blobs = []
        for f in local_files or []:
            p = Path(f).expanduser()
            if not p.is_file():
                return {"success": False, "error": f"{p} not found on the MCP server machine"}
            if p.stat().st_size > MAX_UPLOAD_BYTES:
                return {"success": False, "error": f"{p} is larger than {MAX_UPLOAD_BYTES} bytes"}
            blobs.append({"name": p.name, "data_b64": base64.b64encode(p.read_bytes()).decode()})
        if not (files or blobs):
            return {"success": False, "error": "pass files (editor machine) or local_files (this machine)"}
        return run_editor_script(get_conn, "asset_import", {
            "files": files or [], "blobs": blobs, "destination": destination,
            "replace_existing": replace_existing, "save": save, "destination_name": destination_name},
            timeout_s=600)
