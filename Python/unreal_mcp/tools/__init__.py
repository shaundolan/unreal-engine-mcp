"""Tool modules. Each module defines ``register(mcp, get_conn)``.

Modules are discovered automatically: adding ``tools/foo.py`` with a ``register``
function is all it takes to ship a new tool group.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..connection import UnrealConnection

ERROR_DETAIL_KEYS = ("editor_unresponsive", "hint", "health")


def call(get_conn: Callable[[], UnrealConnection], command: str,
         params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
    """Send a command and flatten the plugin reply into a tool result.

    Success -> ``{"success": True, **result}``; failure -> ``{"success": False, "error": ...}``.
    """
    reply = get_conn().send_command(command, params or {}, timeout=timeout)
    if reply.get("status") == "error":
        out = {"success": False, "error": reply.get("error", "Unknown error")}
        # editor_unresponsive (watchdog): keep the recovery hint and the health snapshot for the agent.
        out.update({k: reply[k] for k in ERROR_DETAIL_KEYS if k in reply})
        return out
    result = reply.get("result", reply)
    if isinstance(result, dict):
        out = {k: v for k, v in result.items() if k != "success"}
        return {"success": True, **out}
    return {"success": True, "result": result}


def register_all(mcp: FastMCP, get_conn: Callable[[], UnrealConnection]) -> list[str]:
    loaded = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(module, "register"):
            module.register(mcp, get_conn)
            loaded.append(info.name)
    return loaded
