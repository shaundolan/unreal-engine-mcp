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

## 8b. Niagara authoring (branch feat/niagara-authoring)
New C++ in `MCPNiagaraAuthoring.cpp` (commands `niagara_export`, `niagara_apply`, `niagara_particles`), new deps
`Niagara`, `NiagaraCore`, `NiagaraEditor`, and the plugin now depends on the Niagara plugin. Rebuild (step 2), expect
**0 warnings** (Linux: 0). Launch, then `unreal_status` must list the three commands.

What was already proven live on Linux (GTX 1080, before the policy to test on the 4090 only): creating a system from
every emitter template (13/14: BlowingParticles hit the old 125 s game-thread limit while compiling; `timeout_s` 590
is now sent), exporting it (stages including `event:0` handlers, inputs with constant / linked / dynamic / data modes,
renderers with bindings and materials, user parameters), and `add_module` of all 57 modules in `MODULES` onto a Minimal
emitter. These were **not** run live and are the checklist:

```powershell
cd Python
$env:UNREAL_MCP_PORT = "55557"
uv run python ..\scripts\niagara_live_check.py
```
The script prints `PASS` / `FAIL` per line (the names below) and ends with `N/N passed`. Expected, all PASS:
- [ ] **create from empty + add_emitter + inputs + renderer + user param**: one call creates `/Game/NiagaraLive/NS_Ops` with
      User.SpawnRate / User.Tint, a Fountain emitter Flames, SpawnRate linked to User.SpawnRate, InitializeParticle
      `Lifetime Mode` = "Direct Set" (an enum set **by display name**), Lifetime 1.25, Color linked to User.Tint, Sprite Size
      Mode "Uniform" with a `random` [8, 20], CurlNoiseForce inserted at index 2 with two inputs, a Scale RGB `curve`, a light
      renderer with RadiusScale 2 and fixed bounds. `compile.ok` true.
- [ ] **link readback**, **enum by display name + local value**, **random dynamic input** (mode `dynamic` with
      Minimum/Maximum children), **curve via FromCurve** (Scale RGB mode `dynamic`, VectorFromCurve, a curve child with 2
      keys), **module inserted at index 2**, **light renderer + bounds**, **user params**.
- [ ] **atomic failure rolls back**: remove Drag, then a bad input name. The error names op 1 and lists the module's inputs;
      `rolled_back` true. **state unchanged after rollback**: Drag is still there (export identical).
- [ ] **dry run** of remove_emitter: success, `rolled_back` true. **state unchanged after dry run**.
- [ ] **compile error is structured + rolled back**: a broken HLSL `expression` on Drag gives `success` false and
      `compile.errors[]` with emitter `Flames`, the script and a message. **state unchanged after compile error**.
      (If the compile instead passes, report it: the expression's syntax error should fail translation.)
- [ ] **module order after move** (`move_module` Drag to 0 relinks the stack graph; also open the system in the Niagara
      editor and check the stack shows Drag first, with no errors), **disabled module**, **emitter order**
      (`move_emitter`), **event handler stage** (Sparks gets `event:0` with source_emitter Flames, spawn_number 3, and
      ReceiveDeathEvent in it), **set parameters module** (`add_parameter` Emitter.Heat in emitter_spawn).
- [ ] Undo: after the script, Ctrl+Z in the editor once undoes the last `niagara_apply` as one step.

Then the bench (6 new `vfx-*` tasks; they also run in CI against the fake):
```powershell
uv run python -m unreal_mcp.bench run --agent solution --filter vfx
uv run python -m unreal_mcp.bench run --agent none --filter vfx
```
Expect **6/6** then **0/6**. Per task, what the checker needs live:
- [ ] `vfx-campfire`: three CPU emitters; in PIE `niagara_particles` reports at least 20 / 5 / 5 particles for Flames / Embers / Smoke.
- [ ] `vfx-sparks-timer`: uses `EmitterState` "Loop Behavior" = **"Infinite"** (an enum display name not yet seen live; if it
      errors, the error lists the valid options, so fix the task's solution). PIE: max at least 30 and `runs` at least 2 over
      16 samples at 0.2 s (an empty emitter between bursts).
- [ ] `vfx-rain-collision`: CPU Collision against the Floor cube. PIE `metric: min_z` of the live particles is at least -25.
      Also try removing the Collision module: min_z should drop far below 0 (proves the check measures collision).
- [ ] `vfx-steam-vent`: `ScaleSpriteSize` "Uniform Curve Sprite Scale" takes `curve` keys on a curve DI directly; PIE max_z at least 150.
- [ ] `vfx-fireworks`: `add_event_handler` + ReceiveDeathEvent; PIE Burst max at least 50 within 4 s.
- [ ] `vfx-edit-fire`: the setup step itself is a `niagara_apply`; the solution doubles Velocity Speed and turns Color blue.
- [ ] Look at each placed system in the viewport (`viewport_capture`) and check it looks like its name. Paste the images.

Note: Epic's experimental `NiagaraToolsets` plugin (disabled by default) wraps the same `UNiagaraExternalEditUtilities`
API that `niagara_apply` uses for add/remove emitter, user variables, system creation and compile/stack-issue state.
It doesn't need to be enabled.

## 9. Remote mode (optional: agents on Linux, editor on Windows)
```powershell
$env:UNREAL_MCP_BIND = "0.0.0.0"; $env:UNREAL_MCP_TOKEN = "<random-secret>"; scripts\ue_windows.ps1 launch
```
- [ ] On Linux, `UNREAL_MCP_HOST=<windows-ip> UNREAL_MCP_TOKEN=<secret> uv run python ../scripts/smoke.py` passes.
- [ ] A wrong token gets `unauthorized`.
- [ ] `0.0.0.0` without a token falls back to `127.0.0.1` (Output Log error). Allow port 55557 in Windows Firewall.

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
