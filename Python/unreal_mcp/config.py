"""Runtime configuration, read from environment variables.

UNREAL_MCP_HOST        Editor plugin host               (default 127.0.0.1)
UNREAL_MCP_PORT        Editor plugin port               (default 55557)
UNREAL_MCP_TOKEN       Shared secret; must match the plugin's UNREAL_MCP_TOKEN (default: none)
UNREAL_MCP_PROTOCOL    "framed" (plugin >= 0.2) or "legacy" (original unframed plugin)
UNREAL_MCP_LOG_DIR     Directory for the server log file (default ~/.unreal_mcp/logs)
UNREAL_MCP_LOG_LEVEL   Python logging level name     (default INFO)
UNREAL_MCP_API_INDEX   Path of the offline API index (default ~/.unreal_mcp/unreal_api.sqlite)
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

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        protocol = e.get("UNREAL_MCP_PROTOCOL", "framed").strip().lower()
        if protocol not in ("framed", "legacy"):
            raise ValueError(f"UNREAL_MCP_PROTOCOL must be 'framed' or 'legacy', got {protocol!r}")
        return cls(
            host=e.get("UNREAL_MCP_HOST", "127.0.0.1"),
            port=int(e.get("UNREAL_MCP_PORT", DEFAULT_PORT)),
            token=e.get("UNREAL_MCP_TOKEN") or None,
            protocol=protocol,
            log_dir=Path(e["UNREAL_MCP_LOG_DIR"]) if e.get("UNREAL_MCP_LOG_DIR") else _HOME / "logs",
            log_level=e.get("UNREAL_MCP_LOG_LEVEL", "INFO").upper(),
            api_index_path=Path(e["UNREAL_MCP_API_INDEX"]) if e.get("UNREAL_MCP_API_INDEX")
            else _HOME / "unreal_api.sqlite",
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
