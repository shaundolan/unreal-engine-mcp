import asyncio
import importlib

from unreal_mcp.tools.skills import load_skills


def test_skills_load_with_descriptions():
    skills = load_skills()
    assert {"unreal-mcp-overview", "blueprint-authoring", "game-development", "3d-assets"} <= set(skills)
    assert all(s["description"] and s["text"].startswith("---") for s in skills.values())


def test_skills_served_as_resources_and_prompts():
    server = importlib.import_module("unreal_mcp_server_advanced")
    uris = {str(r.uri) for r in asyncio.run(server.mcp.list_resources())}
    assert {"skill://index", "skill://blueprint-authoring", "skill://game-development"} <= uris
    prompts = {p.name for p in asyncio.run(server.mcp.list_prompts())}
    assert "blueprint-authoring" in prompts
    body = asyncio.run(server.mcp.read_resource("skill://blueprint-authoring"))
    text = body[0].content if isinstance(body, list | tuple) else str(body)
    assert "bp_apply" in str(text)
    index = asyncio.run(server.mcp.read_resource("skill://index"))
    assert "game-development" in str(index)
