# UE-Bench

Scripted Unreal Engine tasks with automatic checkers. The bench measures how much real editor work an
agent can finish through UnrealMCP, scored per domain.

Each task file in `tasks/` has these fields:

| Field | What it is |
|---|---|
| `prompt` | What the agent is asked to do. |
| `setup` | Tool calls that stage the starting state after a fresh level is created. |
| `check.scene` | `scene_assert` assertions on the editor level and assets. |
| `check.pie` | A `pie_test` run (assertions plus an optional input `steps` timeline). |
| `solution` | A reference solution as tool calls. |
| `requires` | Optional, e.g. `["blender"]`. Tasks whose requirement is missing are skipped. |

Every task runs in its own sandbox: assets go under `/Game/UEBench/<id>/` and the task gets a fresh empty level there. `{root}`, `{level}` and `{id}` are substituted throughout the task.

## Checker quality
`Python/tests/test_bench.py` runs every task against the fake editor in CI and requires three things:
- the reference solution **passes**;
- an untouched level **fails** (the checker tells success from failure);
- near-miss solutions **fail**. For example: a crate at Z=40 instead of 50, a light ring of radius 500 instead of 600, an emissive pin left unwired, or a missing floor under the PlayerStart.

Checkers use real UE Python API names only. Where the fake lacked one, the fake was extended to match UE, not the other way round.

## Running against a live editor
From `Python/`, with the editor open and the plugin running:

```bash
uv run python -m unreal_mcp.bench list
uv run python -m unreal_mcp.bench run --agent solution          # validate checkers on the real engine
uv run python -m unreal_mcp.bench run --agent none              # baseline: everything should fail
uv run python -m unreal_mcp.bench run --agent cmd --label "claude-code opus" \
  --agent-cmd 'claude -p {prompt} --mcp-config ../bench/mcp.bench.json --allowedTools mcp__unreal'
```

`bench/mcp.bench.json` starts the server from the current directory (`Python/`). The repo's `mcp.json` uses Cursor's
`${workspaceFolder}` variable. The server inherits `UNREAL_MCP_HOST`, `UNREAL_MCP_TOKEN` and the other settings from your environment.
`--filter scene` or `--filter 'material-*'` limits the run by domain or id. Results go to
`bench-results/<timestamp>/results.json` and `scoreboard.md`.

The agent command gets the prompt as `{prompt}` (one argument), as `{prompt_file}` (a path), or on stdin, so any MCP-capable agent works: Claude Code, Cursor CLI, or a local model behind an agent loop.

## v0 tasks (15)
scene ×6 (grid, light ring, cleanup, tagging, transforms, greybox arena), assets ×1, materials ×3 (graph, instance,
apply), cinematics ×1, input ×1, performance ×1, modeling ×1 (Blender → import → place), gameplay ×1 (PIE: spawn on ground).

## Adding tasks
Write `tasks/<id>.json` (the id must match the file name) and run `uv run pytest tests/test_bench.py`. If a checker needs an API that `tests/fake_unreal_api.py` lacks, add it to the fake with the **real** UE name and signature. Add a near-miss entry to `MUTATIONS` in the test whenever a checker has a subtle condition.
