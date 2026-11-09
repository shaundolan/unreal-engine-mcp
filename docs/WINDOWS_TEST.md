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

## 9. Remote mode (optional: agents on Linux, editor on Windows)
```powershell
$env:UNREAL_MCP_BIND = "0.0.0.0"; $env:UNREAL_MCP_TOKEN = "<random-secret>"; scripts\ue_windows.ps1 launch
```
- [ ] On Linux, `UNREAL_MCP_HOST=<windows-ip> UNREAL_MCP_TOKEN=<secret> uv run python ../scripts/smoke.py` passes.
- [ ] A wrong token gets `unauthorized`.
- [ ] `0.0.0.0` without a token falls back to `127.0.0.1` (Output Log error). Allow port 55557 in Windows Firewall.

## 10. Agent panel and agent pack (branch feat/agent-experience)
None of this has run live yet. It compiles on Linux and the runner is unit-tested against the fake plugin. Rebuild
first (step 2): there's new C++ under `Source/UnrealMCP/Private/Agent/` and new module dependencies (WebSockets,
ContentBrowser, WorkspaceMenuStructure, ApplicationCore). Use only the mock provider unless you mean to spend money.

**a. Scripted end-to-end check (no model):**
```powershell
cd Python; uv sync --group dev; uv run python ..\scripts\agent_panel_live.py
```
Expect every line to be `[PASS]` and `0 failure(s)`. In order, it checks that:
- the tab spawner `UnrealMCPAgent` is registered and the tab opens;
- the runner's Python dir resolves;
- the panel starts the runner itself (`uv ... -m unreal_mcp.agent_runner --port 55600`) and connects within 300 s (the
  first start installs the venv);
- `hello` lists 4 providers and the specialists;
- a mock turn spawns `Agent_Cube`, with two ok tool rows and streamed text;
- an `asset_manage` delete raises a confirmation, and when it's denied the call doesn't run;
- "Undo last agent turn" removes `Agent_Cube_B` and keeps `Agent_Cube`, then refuses to undo the same turn again;
- `stop_runner` stops the process and the tab closes.

If the connection step fails, the FAIL line includes the runner's output. Check for port 55600 already in use, uv
missing from the editor's PATH, or a firewall prompt.

**b. By hand in the visible editor (the part nothing has checked yet):**
- [ ] Window → Tools → **UnrealMCP Agent** opens a dockable tab. It docks, undocks, and reopens after an editor restart.
- [ ] Editor Preferences → Plugins → **UnrealMCP Agent** shows Runner Port, Python Dir, Uv Executable, Extra Runner Args,
  Auto Start, Default Provider / Model / Specialist, Send Editor Context and Confirm Mode.
- [ ] With Python Dir set to a wrong path, the status line explains how to configure it.
- [ ] Put `--mock-script <repo>\scripts\agent_mock_demo.json` in Extra Runner Args and press **Start runner**. The status goes Starting → Connected, and the Agent combo lists general plus the 7
  specialists.
- [ ] Select two actors and send "hello" with provider `mock`. The transcript shows your message, streamed text, and tool
  rows that expand to show pretty-printed arguments and the result; the status line reads Thinking / Running tool / Done.
  `agent_panel context` (or the runner log) shows both selected actors and the Content Browser selection.
- [ ] A confirmation row's **Allow** and **Deny** buttons work and grey out after one click. **Stop** cancels a long mock
  script (one step per `delay`).
- [ ] **Undo last agent turn** is greyed out with a tooltip reason when there's nothing to undo, and works after a turn
  that spawned something. After a manual edit made after the turn, it refuses.
- [ ] Enter sends and Shift+Enter adds a new line. The transcript scrolls to the end. A long transcript (200 rows) stays
  responsive.
- [ ] Stop runner, then close the editor: no `uv` or `python` process is left behind (Task Manager).

**c. Real providers (optional, costs money or needs a local model):**
- [ ] `anthropic`: set `ANTHROPIC_API_KEY`, `cd Python; uv sync --extra anthropic`, start the editor from that shell.
  Ask for "add a point light above the selected actor". Expect streamed text, a `scene_compose` row, and a
  `viewport_capture` the model comments on.
- [ ] `openai`: `llama-server --jinja -m <model with tool calling> --port 8080`, provider `openai`. Same request.
- [ ] `claude_code`: `claude` logged in, provider `claude_code`. Expect tool rows from `mcp__unreal__*`. A destructive
  request ("delete /Game/Foo") raises a confirmation through `--permission-prompt-tool`. A second message continues the
  same conversation.
- [ ] Epic native: enable Experimental → Model Context Protocol, add `--native-mcp` to Extra Runner Args, and use
  `claude_code`. The runner log shows `unreal-native` connected, and native tools can be called.

**d. Claude Code agent pack:**
```powershell
claude plugin validate .            # from the repo root
claude --plugin-dir .               # then, inside: /mcp, /agents, /unrealmcp:unreal-status
```
- [ ] `/mcp` shows `plugin:unrealmcp:unreal` connected (and `unreal-native` connected if Epic's plugin is on, failed
  otherwise).
- [ ] `/agents` lists the 7 `unrealmcp:unreal-*` subagents.
- [ ] `/unrealmcp:unreal-status` reports the project, level and recent warnings.
- [ ] `/unrealmcp:unreal-screenshot` returns a capture and a critique.
- [ ] `/unrealmcp:unreal-playtest walk forward 2s and jump` runs `pie_test` with input steps and prints a results table.
- [ ] Ask "use the unreal-verifier agent to check that the level has a PlayerStart and a light". The subagent runs with only
  its listed tools.
- [ ] Resources: in any MCP client, `unreal://project`, `unreal://level/summary` and `unreal://selection` return live
  JSON. The prompts `unreal-status` … `unreal-bench` are listed.

## 11. Finish and report
```powershell
cd ..; scripts\ue_windows.ps1 quit
```
Paste back:
- the build result and warning count;
- the RHI line;
- the smoke output;
- both bench scoreboards;
- the step 6 results (the input effect, the frame_stats JSON, the Live Coding result);
- the step 10 results (the agent_panel_live.py output, and any failed checkbox);
- the package time and launcher path;
- anything surprising in `editor.log`.
