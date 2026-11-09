"""Offline Unreal Python API lookup tools (backed by unreal_mcp.api_index)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..api_index import ApiIndex, build_index
from ..config import Settings
from . import call

_cache: dict[str, ApiIndex] = {}


def _index(settings: Settings) -> ApiIndex:
    key = str(settings.api_index_path)
    if key not in _cache:
        _cache[key] = ApiIndex(settings.api_index_path)
    return _cache[key]


def register(mcp, get_conn):
    def settings() -> Settings:
        return get_conn().settings

    @mcp.tool()
    def unreal_api_search(query: str, limit: int = 20, kind: str = "") -> dict[str, Any]:
        """Search the Unreal Python API offline (classes, functions, methods, editor properties).

        Use before writing execute_python code to get exact names and signatures.
        query: natural words or identifiers, e.g. "spawn actor from class", "MaterialEditingLibrary".
        kind: optional filter: class, function, method, property, editor_property.
        """
        try:
            results = _index(settings()).search(query, limit=limit, kind=kind or None)
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "count": len(results), "results": results}

    @mcp.tool()
    def unreal_api_doc(symbol: str) -> dict[str, Any]:
        """Full documentation for one API symbol, e.g. "EditorActorSubsystem" or
        "EditorActorSubsystem.spawn_actor_from_class". Classes include their member list."""
        try:
            doc = _index(settings()).doc(symbol)
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        if doc is None:
            return {"success": False, "error": f"symbol {symbol!r} not found; try unreal_api_search"}
        return {"success": True, **doc}

    @mcp.tool()
    def unreal_api_build_index(stub_path: str = "") -> dict[str, Any]:
        """(Re)build the offline API index from the editor's generated unreal.py stub.

        stub_path: path to unreal.py. If empty, asks the editor (project_context) where its
        stub lives; that works when the MCP server and editor share a filesystem.
        The stub exists only after enabling Python Developer Mode and restarting the editor.
        """
        path = stub_path
        if not path:
            ctx = call(get_conn, "project_context")
            if not ctx.get("success"):
                return {"success": False, "error": f"could not ask editor for stub path: {ctx.get('error')}"}
            path = ctx.get("python_stub_path", "")
        if not path or not Path(path).exists():
            return {"success": False, "error": f"stub not found at {path!r}. Enable Editor Preferences > "
                    "Plugins > Python > Developer Mode, restart the editor, then retry."}
        out = settings().api_index_path
        n = build_index(Path(path), out)
        _cache.pop(str(out), None)
        return {"success": True, "symbols": n, "index_path": str(out), "source": path}
