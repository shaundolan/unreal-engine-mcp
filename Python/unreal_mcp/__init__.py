"""UnrealMCP: local, open MCP bridge to a live Unreal Editor.

Package layout:
    config      -- environment-driven settings (host, port, token, protocol, logging)
    protocol    -- length-prefixed JSON framing shared with the C++ plugin
    connection  -- UnrealConnection: persistent, framed, thread-safe client
    api_index   -- offline Unreal Python API index (SQLite FTS5) built from unreal.py stubs
    tools       -- MCP tool modules registered onto the FastMCP server
"""

__version__ = "0.2.0"
