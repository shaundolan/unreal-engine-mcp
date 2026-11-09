"""Runtime configuration, read from environment variables.

UNREAL_MCP_HOST        Editor plugin host               (default 127.0.0.1)
UNREAL_MCP_PORT        Editor plugin port               (default 55557)
UNREAL_MCP_TOKEN       Shared secret; must match the plugin's UNREAL_MCP_TOKEN (default: none)
UNREAL_MCP_PROTOCOL    "framed" (plugin >= 0.2) or "legacy" (original unframed plugin)
UNREAL_MCP_LOG_DIR     Directory for the server log file (default ~/.unreal_mcp/logs)
UNREAL_MCP_LOG_LEVEL   Python logging level name     (default INFO)
UNREAL_MCP_API_INDEX   Path of the offline API index (default ~/.unreal_mcp/unreal_api.sqlite)
UNREAL_MCP_BLENDER     Blender executable for blender_run (default: "blender" on PATH)
UNREAL_MCP_WORK_DIR    Scratch dir for Blender scripts and exports (default ~/.unreal_mcp/work)
UNREAL_MCP_EPIC_URL    Epic's native editor MCP server (UE 5.8 ModelContextProtocol plugin), e.g.
                       http://127.0.0.1:8000/mcp. Set it to re-expose Epic's toolsets through this server
                       (default: unset = off). See docs/EPIC_NATIVE_MCP.md.
UNREAL_MCP_EPIC_PREFIX Name prefix for proxied Epic tools (default "epic_")
UNREAL_MCP_EPIC_MODE   "meta" (default): epic_list_toolsets / epic_describe_toolset / epic_call_tool, discovered
                       lazily; "expand": also register every Epic toolset tool as its own prefixed tool at startup
UNREAL_MCP_EPIC_TOOLSETS  expand mode: comma-separated, case-insensitive substrings of the toolsets to expand
                       (default: all except UnrealMCP.*, which this server already provides natively)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 55557
_HOME = Path(os.path.expanduser("~")) / ".unreal_mcp"


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    token: str | None = None
    protocol: str = "framed"
    log_dir: Path = field(default_factory=lambda: _HOME / "logs")
    log_level: str = "INFO"
    api_index_path: Path = field(default_factory=lambda: _HOME / "unreal_api.sqlite")
    blender_path: str = "blender"
    work_dir: Path = field(default_factory=lambda: _HOME / "work")
    epic_url: str = ""
    epic_prefix: str = "epic_"
    epic_mode: str = "meta"
    epic_toolsets: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        protocol = e.get("UNREAL_MCP_PROTOCOL", "framed").strip().lower()
        if protocol not in ("framed", "legacy"):
            raise ValueError(f"UNREAL_MCP_PROTOCOL must be 'framed' or 'legacy', got {protocol!r}")
        epic_mode = e.get("UNREAL_MCP_EPIC_MODE", "meta").strip().lower()
        if epic_mode not in ("meta", "expand"):
            raise ValueError(f"UNREAL_MCP_EPIC_MODE must be 'meta' or 'expand', got {epic_mode!r}")
        return cls(
            host=e.get("UNREAL_MCP_HOST", "127.0.0.1"),
            port=int(e.get("UNREAL_MCP_PORT", DEFAULT_PORT)),
            token=e.get("UNREAL_MCP_TOKEN") or None,
            protocol=protocol,
            log_dir=Path(e["UNREAL_MCP_LOG_DIR"]) if e.get("UNREAL_MCP_LOG_DIR") else _HOME / "logs",
            log_level=e.get("UNREAL_MCP_LOG_LEVEL", "INFO").upper(),
            api_index_path=Path(e["UNREAL_MCP_API_INDEX"]) if e.get("UNREAL_MCP_API_INDEX")
            else _HOME / "unreal_api.sqlite",
            blender_path=e.get("UNREAL_MCP_BLENDER", "blender"),
            work_dir=Path(e["UNREAL_MCP_WORK_DIR"]) if e.get("UNREAL_MCP_WORK_DIR") else _HOME / "work",
            epic_url=e.get("UNREAL_MCP_EPIC_URL", "").strip(),
            epic_prefix=e.get("UNREAL_MCP_EPIC_PREFIX", "epic_"),
            epic_mode=epic_mode,
            epic_toolsets=tuple(t.strip() for t in e.get("UNREAL_MCP_EPIC_TOOLSETS", "").split(",") if t.strip()),
        )


def configure_logging(settings: Settings) -> None:
    """Log to a file only. stdout belongs to the MCP stdio transport."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(settings.log_dir / "unreal_mcp.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
