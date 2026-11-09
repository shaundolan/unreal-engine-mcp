import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_unreal import FakeUnreal  # noqa: E402

from unreal_mcp.config import Settings  # noqa: E402
from unreal_mcp.connection import UnrealConnection  # noqa: E402


@pytest.fixture
def fake():
    server = FakeUnreal()
    yield server
    server.close()


@pytest.fixture
def conn(fake):
    c = UnrealConnection(Settings(port=fake.port))
    c.MAX_CONNECT_ATTEMPTS = 1
    yield c
    c.disconnect()


class ToolHost:
    """Minimal stand-in for FastMCP: collects @mcp.tool() functions by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def tools(conn, tmp_path):
    from unreal_mcp.tools import register_all
    conn.settings = Settings(port=conn.settings.port, api_index_path=tmp_path / "api.sqlite")
    host = ToolHost()
    register_all(host, lambda: conn)
    return host.tools


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("UNREAL_MCP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("UNREAL_MCP_API_INDEX", str(tmp_path / "api.sqlite"))
    for k in ("UNREAL_MCP_PORT", "UNREAL_MCP_TOKEN", "UNREAL_MCP_PROTOCOL"):
        monkeypatch.delenv(k, raising=False)
    yield
