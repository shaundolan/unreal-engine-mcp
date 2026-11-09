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

## 9b. Local 3D generation on the 4090 (branch feat/gen3d)
The generation server runs on this box and uses the GPU. The MCP server and the editor can be here or on Linux.

Licences:
- `triposr` (MIT) and `flux-schnell` (Apache-2.0) are commercial-safe.
- Hunyuan3D 2.1 is **not licensed in the EU/UK**. See `servers/gen3d/README.md`, and skip it if you are there.

1. Prerequisites: Python 3.11, plus the CUDA Toolkit 12.4 and the VS 2022 C++ build tools (already installed in step
   0).
2. Install TripoSR and the server dependencies:
   ```powershell
   cd C:\gen3d; py -3.11 -m venv venv; .\venv\Scripts\Activate.ps1
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
   pip install -r <repo>\servers\gen3d\requirements.txt
   git clone https://github.com/VAST-AI-Research/TripoSR.git; pip install -r TripoSR\requirements.txt
   $env:TRIPOSR_DIR = "C:\gen3d\TripoSR"
   huggingface-cli login     # accept the FLUX.1-schnell terms on huggingface.co first
   ```
3. Start it. The first job downloads about 2 GB of TripoSR weights and about 33 GB of FLUX weights.
   ```powershell
   $env:GEN3D_TOKEN = "<random-secret>"
   python <repo>\servers\gen3d\gen3d_server.py --backend triposr --t2i flux-schnell --host 0.0.0.0 --preload
   ```
   Note the peak VRAM in Task Manager. Expect about 6 GB for TripoSR and about 13 GB for FLUX with CPU offload.
4. Point the MCP server at it.
   - Same box: the defaults work.
   - Another box: set `UNREAL_MCP_GEN3D_URL=http://<ip>:8765` and `UNREAL_MCP_GEN3D_TOKEN`, and allow TCP 8765 in
     Windows Firewall (Private profile).
5. Check:
   - [ ] `gen3d_status` shows `triposr`, `MIT`, the GPU and its free VRAM.
   - [ ] Run `mesh_generate(prompt="a wooden barrel with iron hoops", height_cm=90, out_name="SM_Barrel",
     destination="/Game/Generated")`.
     - Expect `success`, `size_cm[2]` ≈ 90 and a textured barrel in the viewport.
     - Note `generation_s` and the total time.
   - [ ] Run `mesh_generate(image_path="<repo>/bench/assets/character_tpose.png", subject="character",
     height_cm=180, out_name="SM_Hero", collision="none", lods=0)`, then
     `auto_rig(mesh="/Game/Generated/SM_Hero", place_actor="Hero")`.
     - Needs the Mannequin first: `python scripts\install_mannequin.py`.
     - Expect the IK Rig chains to include Spine, LeftArm and RightLeg.
     - Expect `MF_Unarmed_Walk_Fwd_Hero` in `/Game/Characters/Generated/Anims`.
     - Linux reference (mock backend, live 5.8.4): the IK Rig had 11 chains (Spine, Neck, Head, Left/Right
       Leg, Foot, Clavicle, Arm). The skeleton root was clean (scale 1) because the rig ships as GLB.
     - On D3D12, check that the root bone's local scale is 1. It shows on the skeleton's reference pose:
       `skeleton.get_reference_pose().get_ref_bone_pose('root')`.
   - [ ] PIE: Hero walks in place with the Mannequin walk. Take a `viewport_capture` of it, and add a light first:
     a new level is black.
     - Note whether it walks forwards, backwards or sideways.
     - If not forwards, re-run `auto_rig` with `rotate=[0,0,180]` or `[0,0,90]` and report which worked.
   - [ ] Bench: `uv run python -m unreal_mcp.bench run --agent solution --filter "gen3d-*"` passes 2/2.
   - [ ] Optional: try other backends and record VRAM and quality for each.
     - `--backend sf3d`: only for revenue under US$1M.
     - `--backend hunyuan3d` with `backend_params={"texture": false}`: only outside the EU/UK.

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
- the gen3d results from step 9b: timings, VRAM, walk direction, and the bench result (2/2);
- anything surprising in `editor.log`.
