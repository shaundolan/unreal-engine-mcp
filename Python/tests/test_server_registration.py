"""The real server module imports cleanly and registers every legacy + new tool."""

import asyncio
import filecmp
import importlib
from pathlib import Path

LEGACY_TOOLS = {
    "get_actors_in_level", "find_actors_by_name", "delete_actor", "set_actor_transform", "create_blueprint",
    "add_component_to_blueprint", "set_static_mesh_properties", "set_physics_properties", "compile_blueprint",
    "read_blueprint_content", "analyze_blueprint_graph", "get_blueprint_variable_details",
    "get_blueprint_function_details", "create_pyramid", "create_wall", "create_tower", "create_staircase",
    "construct_house", "construct_mansion", "create_arch", "spawn_physics_blueprint_actor", "create_maze",
    "get_available_materials", "apply_material_to_actor", "apply_material_to_blueprint", "get_actor_material_info",
    "set_mesh_material_color", "create_town", "create_castle_fortress", "create_suspension_bridge",
    "create_aqueduct", "add_node", "connect_nodes", "create_variable", "set_blueprint_variable_properties",
    "add_event_node", "delete_node", "set_node_property", "create_function", "add_function_input",
    "add_function_output", "delete_function", "rename_function",
}
NEW_TOOLS = {
    "execute_python", "object_describe", "property_get", "property_set", "function_call", "unreal_status",
    "project_context", "editor_action", "console_command", "editor_log", "viewport_capture",
    "unreal_api_search", "unreal_api_doc", "unreal_api_build_index",
    "scene_query", "scene_brief", "actor_inspect", "scene_compose", "level_inspect",
    "search_assets", "asset_references", "asset_manage", "asset_import",
    "material_inspect", "material_edit", "material_instance", "pie_test",
    "niagara_edit", "sequence_edit", "data_table",
    "enhanced_input", "game_setup", "cpp_source", "live_coding_compile", "performance_audit",
    "blender_status", "blender_run",
    "package_project", "package_status",
    "scene_assert", "bp_export", "bp_apply", "widget_export", "widget_apply", "bt_export", "bt_build",
    "anim_inspect", "anim_edit", "animbp_export", "animbp_apply", "gameplay_tags", "gas_edit", "gas_inspect",
}


def _server():
    return importlib.import_module("unreal_mcp_server_advanced")


def test_all_tools_registered():
    server = _server()
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert LEGACY_TOOLS <= names, LEGACY_TOOLS - names
    assert NEW_TOOLS <= names, NEW_TOOLS - names


def test_every_tool_has_a_description():
    server = _server()
    missing = [t.name for t in asyncio.run(server.mcp.list_tools()) if not (t.description or "").strip()]
    assert missing == []


def test_legacy_tool_routes_through_shared_connection(fake, monkeypatch):
    server = _server()
    from unreal_mcp import connection
    from unreal_mcp.config import Settings
    monkeypatch.setattr(connection, "_connection", connection.UnrealConnection(Settings(port=fake.port)))
    fake.on("get_actors_in_level", lambda p: {"actors": [{"name": "Floor"}]})
    assert server.get_actors_in_level()["result"]["actors"][0]["name"] == "Floor"
    connection.reset_unreal_connection()


def test_sample_project_plugin_copy_in_sync():
    """FlopperamUnrealMCP/Plugins/UnrealMCP must mirror UnrealMCP/ (run scripts/sync_plugin.sh)."""
    root = Path(__file__).resolve().parents[2]
    src, dst = root / "UnrealMCP", root / "FlopperamUnrealMCP" / "Plugins" / "UnrealMCP"

    def diff(a: Path, b: Path) -> list[str]:
        cmp = filecmp.dircmp(a, b, ignore=["Binaries", "Intermediate"])
        out = [str(a / n) for n in cmp.left_only + cmp.right_only + cmp.diff_files + cmp.funny_files]
        for sub in cmp.common_dirs:
            out += diff(a / sub, b / sub)
        # dircmp compares shallowly; re-check content of "same" files byte for byte.
        out += [str(a / n) for n in cmp.same_files if (a / n).read_bytes() != (b / n).read_bytes()]
        return out

    assert diff(src, dst) == []
