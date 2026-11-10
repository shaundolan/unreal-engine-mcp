---
description: Play the open level in PIE with real input and report what happened, with assertions, a screenshot and frame times
argument-hint: "[what to test, e.g. 'walk forward 3s and pick up the coin']"
allowed-tools: mcp__plugin_unrealmcp_unreal__unreal_status, mcp__plugin_unrealmcp_unreal__project_context, mcp__plugin_unrealmcp_unreal__scene_brief, mcp__plugin_unrealmcp_unreal__scene_query, mcp__plugin_unrealmcp_unreal__level_inspect, mcp__plugin_unrealmcp_unreal__pie_test, mcp__plugin_unrealmcp_unreal__pie_input, mcp__plugin_unrealmcp_unreal__viewport_capture, mcp__plugin_unrealmcp_unreal__editor_log, mcp__plugin_unrealmcp_unreal__frame_stats, mcp__plugin_unrealmcp_unreal__wait_for_shaders
---
Playtest the open level with `pie_test`. Test the behaviour, not just that it launches. Don't edit the project.

Test request: $ARGUMENTS

1. Orient: `scene_brief` and `level_inspect` (game mode, player start). Look at the input actions with
   `scene_query`, or ask, if the request doesn't say which keys or actions to use.
2. Plan the assertions that would prove the request, then run one `pie_test` with:
   - `steps` that drive the game with **real input** (`press`, `hold`, `axis`, `look` on keys such as `W` / `SpaceBar`, or
     on Enhanced Input actions), using `until` where the game needs time to react;
   - assertions on properties, distances and Python expressions, plus `{"type":"log","no_errors":true}`;
   - `{"frame_stats": 300}` as the last step, so the timings come from the running game.
   With no request, walk forward for 2 seconds, jump, look around, and assert that the player still exists above kill Z
   and that the log has no errors.
3. Capture the game view with `viewport_capture(source="game")` during the run, or straight after it with `stop=false`
   and then stop it, and describe what you see.
4. Report a table (check, expected, actual, PASS/FAIL), the frame-time summary (avg / p95 / p99 frame, game, GPU ms; render
   and RHI times can read as unavailable offscreen on Linux), any log errors, and what to fix for each failure.
