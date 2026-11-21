# headless_check: one-call gameplay check

`headless_check` opens a map, plays it in PIE with a `pie_test` timeline and assertions, and returns a compact
verdict. It works in an offscreen or `-nullrhi` editor, for example on a build box. It runs on top of
`run_pie_test` (`Python/unreal_mcp/tools/testing.py`), so every `pie_test` step and assertion is available.

## From an agent (MCP tool)

```json
{"tool": "headless_check", "arguments": {"suite": "/abs/path/suite.json"}}
```

`suite` can be a JSON object, a JSON string, or a path on the MCP server's machine. `map_path` overrides the suite's
map.

## From a shell (ripper01, CI)

`headless_check` connects to an editor that is already running. Start one first, with any guards you want
(see [EDITOR_GUARDS.md](EDITOR_GUARDS.md)):

```sh
UE_PROJECT=/path/to/Game.uproject UE_EXTRA_ARGS="-nullrhi" UNREAL_MCP_PORT=55558 scripts/ue_linux.sh launch
cd Python && uv run python -m unreal_mcp.tools.headless ../docs/examples/headless_midtown_traffic.json --port 55558
#   (or: uv run unreal-mcp-headless-check <suite.json> --port 55558 [--map /Game/...] [--json])
UNREAL_MCP_PORT=55558 scripts/ue_linux.sh quit
```

Exit codes: `0` passed, `1` failed, `2` could not run (bad suite, no editor, `level_open` failed, PIE did not start).
Output:

```
FAIL midtown-drive-60s on /Game/Planet/Maps/NYC_Planet (74.2s, 13 steps, 4 assertions)
  counter traffic_active: -1 -> 38 (delta 39)
  counter player_speed_cm_s: 0.0 -> 12.5 (delta 12.5)
  FAIL assertion 1 drove somewhere (python): actual=false
  log error: LogNyc: ...
```

`--json` prints the full result: `passed`, `failures` (one line each), `counters` (`{start, end, delta}`),
`log_errors` (the first 10), `skipped_steps`, `steps_run`, `assertions`, `elapsed_s`.

## Suite format

```json
{
  "name": "drive",                     "map": "/Game/Maps/City",
  "discard_changes": true,             "duration_s": 2,          "start_timeout_s": 60,
  "width": 640, "height": 360,         "mode": "new_window",
  "steps":      [ ...pie_test steps: wait_s, do, until, check, press, hold, axis, look, release_all,
                  frame_stats, spawn, possess (and capture, see below)... ],
  "assertions": [ ...pie_test assertions: actor_exists, property, distance, python, no_errors,
                  log_contains, log_absent, niagara... ],
  "counters":   [ {"name": "cars", "expr": "<python in the PIE world>", "min": 5, "max": 100,
                   "min_delta": 1, "max_delta": 50} ],
  "fail_on_log_errors": false,          "ignore_log_errors": ["LogStreaming"],
  "allow_render_steps": false,          "notes": "free text"
}
```

- **map**: opened with `level_open`. Unsaved edits are discarded unless `"discard_changes": false`, in which case a
  dirty level makes `level_open` fail. Leave `map` out to play the current level.
- **counters**: sampled before the first step and again after the timeline. Use the same scope as `python`
  assertions (`world`, `actors`, `player`, `find(label)`, `unreal`). A counter never fails on its value alone, only on
  the `min`/`max`/`min_delta`/`max_delta` bounds you give it, or when its expression raises an error. Write the
  expression so it survives a missing actor, e.g. `(lambda t: t.num_active() if t else -1)(...)`.
- **capture** steps need a renderer. They are dropped and listed in `skipped_steps` unless
  `"allow_render_steps": true`. Under `-nullrhi`, the GPU numbers from `frame_stats` are meaningless, but the
  game-thread numbers still hold.
- **log errors** are always reported. They fail the check only with `"fail_on_log_errors": true`. Lines containing
  any `ignore_log_errors` substring (case-insensitive) are dropped.
- To keep state between steps, store it on the `unreal` module, e.g. `unreal._start = player.get_actor_location()`,
  and read it in a later assertion.

## Example: drive Midtown traffic for about 60 s on NYC_Planet

The example is in [examples/headless_midtown_traffic.json](examples/headless_midtown_traffic.json). It does the
following:

1. Waits for streamed Midtown, meaning both `NycTrafficSystem` and `HISM_taxi` exist.
2. Turns off the gameplay autosave and switches to a test save slot.
3. Teleports the player next to the taxi nearest `PlayerStart` and presses `E` to get in.
4. Checks that the pawn is an `NycVehicle`.
5. Holds `W` for 55 s, then brakes.
6. Asserts that the car is still being driven, has moved at least 15 m, and that the log shows no walk-graph or
   `Accessed None` errors.
7. Reports `traffic_active` (`ANycTrafficSystem::NumActive`, floor 5) and the player's speed as counters.

Names confirmed in the NYCity sources and tools:

- `/Game/Planet/Maps/NYC_Planet`, which streams `/Game/NYC/Maps/NYC_Midtown`
- `ANycTrafficSystem::NumActive()` (BlueprintPure)
- `ANycVehicle`
- `UNycGameplaySubsystem::SetAutosave` / `SetSlot`
- the taxi-entry recipe: `HISM_taxi`, `PlayerStart` and the `E` key, from `tools/gameplay_test.py`. Note that this
  file loads `NYC_Midtown` directly.

**Unconfirmed, check on the first live run:**

- Whether the `HISM_taxi` and `PlayerStart` labels resolve when Midtown is streamed under NYC_Planet.
- Whether traffic spawns under `-nullrhi`.
- The thresholds: 5 active cars and 1500 cm are guesses.
