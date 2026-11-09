# Windows test checklist (RTX 4090, plugin 0.3.0)

Everything below is already verified on UE 5.8.4 Linux with a GTX 1080 (see `bench/results/`). This run checks the
Win64 build, D3D12, Live Coding and Win64 packaging. It also gets real GPU numbers. Run the steps in order from
PowerShell in the repo root. Keep each step's output (copy the console, or the log path it prints) for the report.

## 0. Prerequisites
- [ ] UE **5.8** from the Epic launcher. `UE_ROOT` defaults to `C:\Program Files\Epic Games\UE_5.8`. Set it if yours differs:
      `$env:UE_ROOT = "D:\Epic\UE_5.8"`.
- [ ] Visual Studio 2022 (or Build Tools) with **Game development with C++**, the MSVC v143 toolset and a Windows 10/11 SDK.
- [ ] git, and `uv` (`winget install astral-sh.uv`; open a new terminal afterwards).
- [ ] Optional: Blender 4.x. Without it, the `modeling-blender-crate` and `demo-arena-playable` bench tasks are skipped.
- [ ] Scripts allowed for this shell: `Set-ExecutionPolicy -Scope Process Bypass`.

```powershell
git clone https://github.com/shaundolan/unreal-engine-mcp; cd unreal-engine-mcp
git checkout feat/windows-verify          # or whatever branch the lead names
cd Python; uv sync --group dev; cd ..
```

## 1. Unit tests (no editor)
```powershell
cd Python; uv run pytest; uv run ruff check .; cd ..
```
- [ ] All pass. A failure here is a Windows path or encoding bug in the server, not an engine problem.

## 2. Build the plugin
```powershell
scripts\ue_windows.ps1 build
```
- [ ] `build ok (N warnings) -> %TEMP%\unreal-mcp\55557\build.log`. On failure, the script prints the `error C####` lines.
      Save `build.log`. The C++ was compiled on clang 20 (Linux) only, so watch for MSVC-only errors (C4456 shadowing,
      C4668 and the like, which are warnings as errors in some configurations).
- [ ] The plugin binary is `FlopperamUnrealMCP\Plugins\UnrealMCP\Binaries\Win64\UnrealEditor-UnrealMCP.dll`, with no
      `-NNNN` suffix.

## 3. Launch
```powershell
scripts\ue_windows.ps1 launch        # offscreen; use launch-gui to watch
```
- [ ] `editor <pid> ready on port 55557 after ~Ns`.
- [ ] The editor log (`%TEMP%\unreal-mcp\55557\editor.log`) shows `UnrealMCP 0.3.0 listening on 127.0.0.1:55557` and
      `NN commands registered`.
- [ ] Note the RHI the log reports (D3D12, SM6 expected).
- [ ] Not using the default port? Set `$env:UNREAL_MCP_PORT = 55561` before `launch`. Every step below reads the same
      variable. `quit` only ever closes the editor this script started on that port.

## 4. Smoke test
```powershell
cd Python; uv run python ..\scripts\smoke.py
```
- [ ] `0 failure(s)` (16 checks).

## 5. UE-Bench
```powershell
uv run python -m unreal_mcp.bench run --agent solution --label "reference, UE 5.8 Win64, RTX 4090"
uv run python -m unreal_mcp.bench run --agent none --label "baseline, UE 5.8 Win64"
```
- [ ] Reference: 20/20 (18/18 without Blender).
- [ ] Baseline: 0 passed.
- [ ] Results are in `Python\bench-results\<timestamp>\scoreboard.md`. Copy both to
      `bench/results/<date>-ue5.8-win64-rtx4090/` as `reference.md` and `baseline.md`.
- [ ] Any reference failure means the fake API disagrees with the real engine on Windows. Report the task id and its
      `failures` line.
- Optional: a real agent. Pass the prompt as a file or on stdin, not as `{prompt}`. A multi-line argument doesn't
  survive the `claude.cmd` shim.
  ```powershell
  uv run python -m unreal_mcp.bench run --agent cmd --filter scene --label "claude-code win64" `
    --agent-cmd 'claude -p --mcp-config ..\bench\mcp.bench.json --allowedTools mcp__unreal'
  ```

## 6. New in this branch: check these live
Run the following through the MCP server (an agent session), or adapt `scripts/smoke.py`.
- [ ] **pie_test input.** In any level with a pawn, e.g. the default `DefaultPawn`:
  `pie_test(steps=[{"hold":"W","duration_s":1.5}, {"look":[5,0],"duration_s":0.5}, {"press":"SpaceBar"}, {"frame_stats":300}])`.
  All steps should be `ok`. Linux also verified that W moves the pawn and that look turns the control rotation.
- [ ] **Enhanced Input action injection.** Build `input-wasd` (bench) plus a Character that binds IA_Move. Then run
  `{"axis":"/Game/UEBench/input-wasd/Input/IA_Move","value":[0,1],"duration_s":2}` and check that the pawn moved. On Linux
  the injection was accepted, but its effect was **not** observed. This is the first real check of it.
- [ ] **frame_stats on the 4090.** Record `frame_ms`, `game_ms`, `render_ms`, `rhi_ms` and `gpu_ms` for the
  `demo-arena-playable` level, offscreen and in `launch-gui`. On Linux offscreen, `render_ms`/`rhi_ms` were reported as
  `unavailable`. Check whether D3D12 fills them in. `gpu_ms` should be non-zero.
- [ ] **bp_apply with arrays.** `bp_apply` an `int[]` variable into `Array_Length`, `Array_Get` and `ForEachLoop`. The result
  should compile, and `bp_export` should show `int[]`/`int` pins, not `wildcard`. Verified on Linux. This repeats it on MSVC.
- [ ] **Live Coding (Windows only).** Edit a function body in `FlopperamUnrealMCP\Source\...` with `cpp_source`, run
  `live_coding_compile()`, and expect `compiled: true`. Then add a deliberate typo and expect `compiled: false` with the error.

## 7. Package a Win64 build
```powershell
uv run python ..\scripts\package.py --platform Win64      # or call package_project / package_status from an agent
```
- [ ] It ends with `SUCCEEDED (exit 0)` and lists `launcher: ...\Windows\FlopperamUnrealMCP.exe` first. The first cook takes a
      while: note the time.
- [ ] Run that exe. The level should load. Run it once with `-RenderOffscreen` and once windowed.
- [ ] If it fails, the job log is `FlopperamUnrealMCP\Saved\MCPPackage\<job>.log`. The command line is in `<job>.json`. It
      runs `cmd.exe /d /s /c "...RunUAT.bat BuildCookRun ..."`, so it copes with spaces in the engine path.

## 8. Animation + GAS (branch feat/anim-gas)
Verified live on Linux (`bench/results/2026-11-09-anim-gas-ue5.8.4-linux/`); on Windows check the MSVC build and rerun:
```powershell
python scripts\install_mannequin.py --engine "C:\Program Files\Epic Games\UE_5.8"   # editor closed
# rebuild (step 1): new C++ in MCPAnimAuthoring.cpp / MCPGameplayTagCommands.cpp, new deps AnimGraph,
# AnimGraphRuntime, AnimationBlueprintLibrary, GameplayTags; the sample .uproject now enables GameplayAbilities
cd Python
uv run python -m unreal_mcp.bench run --agent solution --filter "anim-*"
uv run python -m unreal_mcp.bench run --agent solution --filter "gas-*"
uv run python -m unreal_mcp.bench run --agent none --filter "anim-*"
```
Expect 4/4 passed, then 0/3 for the baseline. `unreal_status` should list `animbp_apply`, `animbp_export`,
`anim_asset_apply`, `anim_asset_export`, `gameplay_tags`. Also open `/Game/UEBench/anim-locomotion-abp/ABP_Hero` in
the editor and check the state machine looks sane (nodes laid out, no orphan pins).

## 8b. Blueprints complete, data types, audio (branch feat/bp-complete)
New C++: `MCPBlueprintAuthoring.cpp` (new bp_apply ops and node kinds), `MCPDataTypeAuthoring.cpp`, `MCPAudioAuthoring.cpp`.
New module deps: MetasoundEngine, MetasoundFrontend, MetasoundGraphCore, MetasoundEditor, AudioExtensions. The plugin now depends on
the `Metasound` plugin (on by default). Rebuild (step 1) with 0 warnings, then:
```powershell
python scripts\install_mannequin.py --first-person --engine "C:\Program Files\Epic Games\UE_5.8"   # editor closed
cd Python
uv run python -m unreal_mcp.bench run --agent solution --filter "blueprint-*"
uv run python -m unreal_mcp.bench run --agent solution --filter "data-*"
uv run python -m unreal_mcp.bench run --agent solution --filter "audio-*"
uv run python -m unreal_mcp.bench run --agent none --filter "blueprint-*"
```
Expect `blueprint-interface-interact`, `blueprint-dispatcher-door`, `blueprint-timeline-platform`, `blueprint-review-fix`,
`data-item-table` and `audio-metasound-pitch` to pass (plus the older blueprint tasks), and all of them to fail on the baseline.
`unreal_status` lists `data_types`, `data_types_export`, `metasound_apply`, `metasound_export`, `metasound_nodes`,
`sound_cue_apply`. If a task fails, the reply names the op and pin. Note what you changed so it can go back into the task.

Also check these by hand (each is one tool call):
- [ ] `bp_apply` on a fresh Actor BP with `{"kind":"operator","op":"*","type":"float"}`, `{"kind":"select","options":3,"index_type":"int","type":"float"}`,
      `{"kind":"switch","on":"string","cases":["A","B"]}`, `{"kind":"format_text","format":"{Name} has {HP}"}`,
      `{"kind":"make_struct","struct":"Vector"}`, `{"kind":"ai_move_to"}` and an `add_macro` + `{"kind":"macro"}` use. It compiles.
      `bp_export` shows them with kinds operator/select/switch/format_text/call_function/async_action/macro.
- [ ] `add_local_variable` in a pure function, plus get_var/set_var inside it: it compiles, and `bp_export` shows `local_variables` and `"local": true`.
- [ ] `data_types` struct edit: add a member to `/Game/UEBench/data-item-table/S_Item` and rerun `data_table` read. The existing rows keep their values.
- [ ] `data_types` `curve` (float/vector/color) and `curve_table` round-trip through `data_types_export`.
- [ ] `bp_review("/Game/FirstPerson/Blueprints/BP_FirstPersonCharacter")` and `bp_explain` on the template character (bench numbers below).
      Paste the `markdown` and the findings. Expect the IA_Move / IA_Look / IA_Jump input events as entry points, with no errors.
- [ ] `metasound_nodes("filter")` returns classes with pins. Build the skill's engine-loop recipe (`skills/audio/SKILL.md`) and check
      `metasound_export` shows the RPM edge. Play it in PIE on an AudioComponent and call Set Float Parameter RPM: the pitch rises (needs audio out).
- [ ] `sound_cue_apply` with 2+ imported waves, `pitch_range` [0.9,1.1]: open the cue; you see Random → Modulator → Output.

## 9. Remote mode (optional: agents on Linux, editor on Windows)
```powershell
$env:UNREAL_MCP_BIND = "0.0.0.0"; $env:UNREAL_MCP_TOKEN = "<random-secret>"; scripts\ue_windows.ps1 launch
```
- [ ] On Linux, `UNREAL_MCP_HOST=<windows-ip> UNREAL_MCP_TOKEN=<secret> uv run python ../scripts/smoke.py` passes.
- [ ] A wrong token gets `unauthorized`.
- [ ] `0.0.0.0` without a token falls back to `127.0.0.1` (Output Log error). Allow port 55557 in Windows Firewall.

## 10b. Epic native MCP server + UnrealMCPToolsets (branch feat/native-mcp)
This has only been built and unit-tested. **It has never run live**, so this is its first real check. Background is in
[EPIC_NATIVE_MCP.md](EPIC_NATIVE_MCP.md).

The sample project now enables these plugins: ModelContextProtocol, ToolsetRegistry, EditorToolset, UMGToolSet, GameplayTagsToolset and
UnrealMCPToolsets. Both plugins get synced by `sync_plugin.ps1`.
```powershell
scripts\ue_windows.ps1 build                         # expect build ok; MSVC builds UnrealMCPToolsets too
$env:UE_EXTRA_ARGS = "-ModelContextProtocolStartServer -ModelContextProtocolPort=8568"
scripts\ue_windows.ps1 launch
cd Python; uv run python ..\scripts\epic_mcp_smoke.py --url http://127.0.0.1:8568/mcp; cd ..
```
- [ ] `editor.log` contains `UnrealMCPToolsets: registered 8 toolsets (24 tools) with the ToolsetRegistry`,
      `Starting MCP server on port 8568` and `Tool search enabled: registered 3 meta-tools (...)`.
- [ ] The smoke script exits 0 and prints the following:
  - `tools/list: 3 tools (tool_search)` and `ours: [...]` with 8 `UnrealMCP.*` toolsets.
  - Our tools: `ok UnrealMCP.Editor.project_context`, `ok UnrealMCP.Scene.scene_brief`, `ok UnrealMCP.Play.frame_stats` (frame_ms present)
    and `ok UnrealMCP.Blueprint.bp_export -> error: ...` (an expected error).
  - Epic's tools: `ok ...EditorAppToolset.IsPIERunning` and `ok ...SceneTools.get_current_level`.
- [ ] Eager mode: `scripts\ue_windows.ps1 quit`, then add
      `-ini:EditorPerProjectUserSettings:[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]:bEnableToolSearch=False`
      to `UE_EXTRA_ARGS` and relaunch. The smoke script should print `tools/list: ~308 tools (eager)` and list all 24
      `UnrealMCP.<Toolset>.<tool>` names.
- [ ] Gateway: with the editor still up, run
      `$env:UNREAL_MCP_PORT=55557; $env:UNREAL_MCP_EPIC_URL="http://127.0.0.1:8568/mcp"` and start an agent session
      (`claude mcp add unreal -e ... -- uv --directory <repo>\Python run unreal_mcp_server_advanced.py`). Then:
  - `epic_list_toolsets` lists Epic's toolsets and ours.
  - `epic_call_tool(toolset_name="EditorToolset.EditorAppToolset", tool_name="CaptureViewport")` returns an **image** the model can see.
  - Claude Code direct: `claude mcp add --transport http unreal-native http://127.0.0.1:8568/mcp`, then `/mcp` shows
    `list_toolsets`, `describe_toolset` and `call_tool`.
- [ ] Report: the smoke output from both modes, any `LogToolsetRegistry` or `LogModelContextProtocol` warnings, and
      whether `bp_apply` through `call_tool` creates and compiles a Blueprint. For example: `toolset_name=UnrealMCP.Blueprint`,
      `tool_name=bp_apply`, `arguments={"blueprint":"/Game/Native/BP_T","create_parent":"Actor","operations":[{"op":"add_variable","name":"Score","type":"int"}]}`.
      Expected: `success: true` and `compile` status ok.

## 10. Finish and report
```powershell
cd ..; scripts\ue_windows.ps1 quit
```
Paste back:
- the build result and warning count;
- the RHI line;
- the smoke output;
- both bench scoreboards;
- the step 6 results (the input effect, the frame_stats JSON, the Live Coding result);
- the package time and launcher path;
- anything surprising in `editor.log`.
