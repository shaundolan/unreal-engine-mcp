"""Observation tools: let the agent see what it did (log + viewport)."""

from __future__ import annotations

import base64
from typing import Any

from mcp.server.fastmcp import Image

from . import call


def register(mcp, get_conn):
    @mcp.tool()
    def editor_log(lines: int = 200, filter: str = "", min_verbosity: str = "Log",
                   since_seq: int = 0, category: str = "") -> dict[str, Any]:
        """Tail the editor Output Log (kept in a ring buffer by the plugin).

        min_verbosity: Error, Warning, Display or Log. filter: case-insensitive substring.
        since_seq: only entries after this sequence number. Pass back `last_seq` from a
        previous call to see only what happened since (e.g. after a compile or PIE run).
        """
        return call(get_conn, "editor_log", {"lines": lines, "filter": filter, "min_verbosity": min_verbosity,
                                             "since_seq": since_seq, "category": category})

    @mcp.tool()
    def viewport_capture(max_width: int = 1280, source: str = "auto") -> Any:
        """Screenshot and return it as an image, so you can visually verify your work.
        source: "auto" = what the player sees while PIE runs, else the level viewport;
        "game" = the PIE viewport only; "editor" = the level viewport. max_width downsizes."""
        reply = call(get_conn, "viewport_capture", {"max_width": max_width, "source": source})
        if not reply.get("success"):
            return reply
        try:
            data = base64.b64decode(reply["data_base64"])
        except (KeyError, ValueError) as e:
            return {"success": False, "error": f"bad capture payload: {e}"}
        return Image(data=data, format="png")
