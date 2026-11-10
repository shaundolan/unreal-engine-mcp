"""The Claude Code plugin (repo root): manifest, marketplace, subagents and commands reference real tools."""

import asyncio
import importlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PREFIX = "mcp__plugin_unrealmcp_unreal__"
# niagara_apply lands on a sibling branch; epic_* exist only when UNREAL_MCP_EPIC_URL is set
UPCOMING = {"niagara_apply", "epic_list_toolsets", "epic_describe_toolset", "epic_call_tool"}


def server_tools() -> set[str]:
    server = importlib.import_module("unreal_mcp_server_advanced")
    return {t.name for t in asyncio.run(server.mcp.list_tools())}


def frontmatter(path: Path) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert m, path
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def test_manifest_and_marketplace():
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert plugin["name"] == "unrealmcp" == market["plugins"][0]["name"]
    assert market["plugins"][0]["source"] == "./"
    unreal = plugin["mcpServers"]["unreal"]
    assert "${CLAUDE_PLUGIN_ROOT}/Python" in unreal["args"] and "unreal_mcp_server_advanced.py" in unreal["args"]
    assert (REPO / "Python" / "unreal_mcp_server_advanced.py").exists()
    assert plugin["mcpServers"]["unreal-native"]["type"] == "http"


def test_subagents_reference_real_tools():
    tools = server_tools()
    names = set()
    for f in sorted((REPO / "agents").glob("*.md")):
        fm = frontmatter(f)
        assert fm["name"] == f.stem and fm["description"] and fm.get("model") == "inherit"
        names.add(fm["name"])
        listed = [t.strip() for t in fm["tools"].split(",")]
        mcp = [t[len(PREFIX):] for t in listed if t.startswith(PREFIX)]
        assert mcp and not (set(mcp) - tools - UPCOMING), (f.name, set(mcp) - tools - UPCOMING)
        assert "mcp__plugin_unrealmcp_unreal-native__*" in listed
    assert names == {"unreal-blueprint", "unreal-python-batch", "unreal-level-designer", "unreal-vfx",
                     "unreal-verifier", "unreal-tech-artist", "unreal-animator"}


def test_commands_reference_real_tools():
    tools = server_tools()
    names = set()
    for f in sorted((REPO / "commands").glob("*.md")):
        fm = frontmatter(f)
        names.add(f.stem)
        assert fm["description"] and "$ARGUMENTS" in f.read_text()
        allowed = [t.strip() for t in fm["allowed-tools"].split(",")]
        mcp = {t[len(PREFIX):] for t in allowed if t.startswith(PREFIX)}
        assert mcp and mcp <= tools, (f.name, mcp - tools)
    assert names == {"unreal-status", "unreal-playtest", "unreal-screenshot", "unreal-package", "unreal-bench"}
