---
description: Check the Unreal Editor connection and summarise the project, open level and selection
argument-hint: "[focus, e.g. lights or errors]"
allowed-tools: mcp__plugin_unrealmcp_unreal__unreal_status, mcp__plugin_unrealmcp_unreal__project_context, mcp__plugin_unrealmcp_unreal__scene_brief, mcp__plugin_unrealmcp_unreal__editor_log, mcp__plugin_unrealmcp_unreal__wait_for_shaders
---
Report the state of the live Unreal Editor through the UnrealMCP tools. Don't change anything.

1. Call `unreal_status`. If it can't reach the editor, say so and list the fixes: start the editor with the UnrealMCP plugin
   enabled, check that `UNREAL_MCP_PORT` matches on both sides (default 55557), and check that Python Editor Script Plugin
   is enabled. Then stop.
2. Call `project_context` and `scene_brief`, and `editor_log(min_verbosity="Warning", lines=50)`.
3. Answer in at most 12 lines: engine and plugin version, project, open level, actor count and top classes, lights and
   cameras, PIE running or not, and any recent errors or warnings worth acting on (grouped, with counts).

Focus requested by the user (may be empty): $ARGUMENTS
