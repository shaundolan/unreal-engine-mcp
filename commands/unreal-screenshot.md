---
description: Capture the editor or PIE viewport and critique it visually
argument-hint: "[what to look for, or 'game' for the PIE view]"
allowed-tools: mcp__plugin_unrealmcp_unreal__unreal_status, mcp__plugin_unrealmcp_unreal__wait_for_shaders, mcp__plugin_unrealmcp_unreal__viewport_capture, mcp__plugin_unrealmcp_unreal__scene_brief, mcp__plugin_unrealmcp_unreal__editor_action
---
Take a screenshot of the live editor and give a visual critique. Don't edit the project.

Focus: $ARGUMENTS

1. `wait_for_shaders`, so the capture doesn't show stale or grey placeholder materials.
2. `viewport_capture` (use `source="game"` if the focus says game or PIE is running; otherwise the level viewport). If the
   user names an actor, `editor_action(action="focus_actor", actor_name=...)` first.
3. Look at the image and describe it in a few lines, then list concrete problems: missing or floating objects, wrong
   scale, unlit, black or blown-out areas, grey placeholder materials, z-fighting, clipping, UI overlap, unreadable text.
   Point out where each one is in the frame and how you'd fix it. If it looks right, say so; don't invent problems.
