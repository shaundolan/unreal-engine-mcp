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
    def viewport_capture(max_width: int = 1280, source: str = "auto", width: int = 0, height: int = 0) -> Any:
        """Screenshot and return it as an image, so you can visually verify your work.
        source: "auto" = what the player sees while PIE runs, else the level viewport;
        "pie" (or "game") = the PIE viewport only; "editor" = the level viewport. max_width downsizes.
        width/height (PIE only): render the PIE viewport at exactly this size first (it stays that
        size for the session). Use it in offscreen editors, where the PIE window size is unreliable;
        HighResShot / take_high_res_screenshot capture the editor view there, not the game."""
        reply = capture_png(get_conn, max_width, source, width, height)
        if not reply.get("success"):
            return reply
        return Image(data=reply["png"], format="png")


def capture_png(get_conn, max_width: int = 1280, source: str = "auto", width: int = 0,
                height: int = 0) -> dict[str, Any]:
    """viewport_capture -> {success, png (bytes), width, height}."""
    params: dict[str, Any] = {"max_width": max(max_width, width) if width else max_width, "source": source}
    if width and height:
        params.update(width=width, height=height)
    reply = call(get_conn, "viewport_capture", params)
    if not reply.get("success"):
        return reply
    try:
        png = base64.b64decode(reply["data_base64"])
    except (KeyError, ValueError) as e:
        return {"success": False, "error": f"bad capture payload: {e}"}
    return {"success": True, "png": png, "width": reply.get("width"), "height": reply.get("height")}
