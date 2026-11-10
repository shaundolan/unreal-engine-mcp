---
description: Run UE-Bench tasks against the live editor and report the scoreboard
argument-hint: "[task ids or a prefix, e.g. 'blueprint-' ; empty lists the tasks]"
allowed-tools: Bash(uv run python -m unreal_mcp.bench *), Read, mcp__plugin_unrealmcp_unreal__unreal_status
---
Run UE-Bench, the repo's scripted editor tasks with automatic checkers (see bench/README.md in the UnrealMCP repo).

Tasks requested: $ARGUMENTS

1. `unreal_status` must be connected. Note its port: the bench must use the same `UNREAL_MCP_PORT`.
2. From the repo's `Python/` directory:
   - with no arguments, run `uv run python -m unreal_mcp.bench list` and show the tasks grouped by domain, then stop;
   - otherwise run `uv run python -m unreal_mcp.bench run --agent solution --filter '<glob>'` (a glob on the task id
     or domain, e.g. `'blueprint-*'`) to check the checkers and the editor (the reference solutions should pass), or,
     when the user asks to score an agent, use `--agent cmd --agent-cmd '<template with {prompt}>' --label <name>`.
   The bench resets the level between tasks; warn the user first if the open level has unsaved work.
3. Read `bench-results/<timestamp>/scoreboard.md` and report the pass rate per domain and each failure with its reason.
