# Nerve build plan for UnrealMCP (draft, needs Shaun's sign-off)

Nothing here exists in Nerve yet. Creating the project and agents needs approval.

## Project
- slug `unreal-agent` → repo `shaundolan/unreal-engine-mcp`
- Verification: Python via `uv run pytest` (CodeBuild-compatible, no engine needed). Editor-side changes also need the
  **Windows UE runner** (`docs/WINDOWS_TEST.md`, CI job `plugin-build`, labels `[self-hosted, windows, unreal]`).
- ATDD: every card's acceptance criteria link to a test. That's a pytest against `tests/fake_unreal.py` /
  `tests/fake_unreal_api.py` for the server/script logic, plus a `scripts/smoke.py` check or a `pie_test`
  assertion set for live behaviour.

## New agents

### Game Developer (UE): proposed id `gamedev`, model claude-sonnet (Opus for planning)
Owns: plugin C++ (`UnrealMCP/`), Blueprint and gameplay tools, `pie_test`, the `game-development` skill.
Rules:
- Edit `UnrealMCP/` only, then run `scripts/sync_plugin.sh`. The sample-project copy is generated.
- Every editor-side tool ships with a script test against the fake API **and** a smoke/pie_test check on the UE runner.
- No change counts as done while the UE runner build is red.
- Demos: plays the "prompt → playable" role with the tools it builds (dogfooding).

### Technical Artist / 3D: proposed id `techart`, model claude-sonnet
Owns: materials, Niagara, sequencer, landscape/foliage, animation, lighting tools and skills, and the free asset pipeline:
- Blender headless (`blender -b -P script.py`) on the Linux box for modeling, UVs and LODs, exported as FBX/GLB, then `asset_import(local_files=...)`
- optional local text/image→3D endpoint (configurable; the GTX 1080 limits model size)
- `performance_audit` budgets per demo level

### Existing agents
| Agent | Work |
|---|---|
| Forge / Bridge | Python server, protocol, tool modules, API index |
| Probe / Weld | test authoring, UE-Bench task checkers |
| Velocity | performance_audit + PIE frame timing |
| Cipher | review of every change touching `execute_python`, `cpp_source`, token/bind logic (RCE surface) |
| Quill | README, skills, launch post (after demo 1, with Shaun's sign-off) |
| Cadence | card grooming against docs/ROADMAP.md |

## First cards (in order)
1. **UE runner**: register the Windows PC as a self-hosted runner, set `UE_RUNNER_ENABLED=true`, get `plugin-build` green. *(needs Shaun at the PC)*
2. Run `docs/WINDOWS_TEST.md`; fix compile and smoke failures. *(gamedev)*
3. Build the API index from the real stub and record the symbol count. Use it to fix fake-API drift in `tests/fake_unreal_api.py`. *(Forge)*
4. Run every editor script once against the live editor, through a pie_test/smoke harness per tool. Fix any API mismatches the fake hid. *(gamedev + techart)*
5. ~~Blueprint v2~~: done (`bp_export` / `bp_apply`: atomic, dry run, compile errors, auto-wired by-ref literals). *(gamedev)*
6. ~~UMG widget tree authoring~~: done (`widget_export` / `widget_apply`). *(gamedev)*
7. ~~Behavior Tree / Blackboard authoring~~: done (`bt_export` / `bt_build`; verified: a guard walks its route in PIE). *(gamedev)*
8. ~~Blender bridge tool~~: done (`blender_run`/`blender_status`, `3d-assets` skill). Next: skeletal/animation export recipes. *(techart)*
9. ~~UE-Bench v0~~: done (15 tasks, oracle + near-miss tests in CI). Next: v1 to 60 tasks, adding Blueprint/UMG/BT once the C++ lands. *(Probe)*
10. Demo 1 "prompt → playable" recording: Nerve Live Flow plus an editor timelapse. *(gamedev + techart, Quill writes it up)*
