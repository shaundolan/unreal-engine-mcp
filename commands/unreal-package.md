---
description: Build, cook and package a standalone game build in the background and report the result
argument-hint: "[platform] [configuration] [output dir]"
allowed-tools: mcp__plugin_unrealmcp_unreal__unreal_status, mcp__plugin_unrealmcp_unreal__project_context, mcp__plugin_unrealmcp_unreal__editor_action, mcp__plugin_unrealmcp_unreal__package_project, mcp__plugin_unrealmcp_unreal__package_status, mcp__plugin_unrealmcp_unreal__editor_log
---
Package the project with UAT BuildCookRun through `package_project`. Arguments (all optional): $ARGUMENTS
(platform defaults to the editor's host: Win64, Linux or Mac; configuration defaults to Development).

1. `project_context`: check that the game default map is set. If it isn't, tell the user, since the build would open an
   empty map, and stop.
2. Packaging cooks the saved assets, not the unsaved ones. If the level or assets are dirty, ask whether to run
   `editor_action(action="save_all")` first. Don't save without asking.
3. Start `package_project` with the requested platform, configuration and output directory. It runs in the background.
4. Poll `package_status(job)` with the job id it returns about every 30 seconds until it finishes, telling the user the current stage now and then. A
   first cook can take 10 to 30 minutes.
5. On success, report the output directory and the launcher(s) it lists. On failure, report the failing stage and the
   first real errors from the log tail (not the summary lines), and the likely fix.
