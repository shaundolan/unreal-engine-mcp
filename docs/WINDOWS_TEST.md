# Windows build & test checklist (plugin 0.2)

The 0.2 C++ changes (framed protocol, command registry, execute_python, reflection, observation) were written
without an engine available and have **not been compiled yet**. Run this on the Windows UE box to verify them.

## 0. Prereqs
- UE 5.5+ installed (Epic launcher) with **Editor symbols** optional; Visual Studio 2022 with *Game development with C++*.
- `uv` (`winget install astral-sh.uv`) and git.

## 1. Build the plugin
```powershell
git clone https://github.com/shaundolan/unreal-engine-mcp; cd unreal-engine-mcp
git checkout feat/phase0-1-foundation
& "C:\Program Files\Epic Games\UE_5.5\Engine\Build\BatchFiles\Build.bat" `
  FlopperamUnrealMCPEditor Win64 Development `
  -Project="$PWD\FlopperamUnrealMCP\FlopperamUnrealMCP.uproject" -WaitMutex
```
If it fails, save the full UBT output (`Engine\Programs\UnrealBuildTool\Log.txt`) and bring it back. Fix the
errors in `UnrealMCP/` (source of truth), then `scripts\sync_plugin.ps1`.

Things to check first:
- `FImageUtils::ImageResize` overload (`EpicUnrealMCPObservationCommands.cpp`)
- `FJsonObjectConverter::*` and `FProperty::ImportText_Direct` signatures (`EpicUnrealMCPReflectionCommands.cpp`)
- `FPythonCommandEx` fields/enums (`EpicUnrealMCPPythonCommands.cpp`)

## 2. Launch
```powershell
start FlopperamUnrealMCP\FlopperamUnrealMCP.uproject
```
In the Output Log, look for: `UnrealMCP 0.2.0 listening on 127.0.0.1:55557 (token off)` and
`UnrealMCP: NN commands registered`. If you see `Python is not available`, enable *Python Editor Script Plugin*.

## 3. Smoke test (same machine)
```powershell
cd Python; uv sync; uv run python ..\scripts\smoke.py
```
Every line should say PASS. The script creates `MCP_SmokeLight` and `/Game/Blueprints/MCP_SmokeBP` and then deletes them.

## 4. Remote mode (agents on Linux, editor on Windows)
```powershell
setx UNREAL_MCP_BIND 0.0.0.0
setx UNREAL_MCP_TOKEN <random-secret>   # then restart the editor
```
On Linux: `UNREAL_MCP_HOST=<windows-ip> UNREAL_MCP_TOKEN=<secret> uv run python ../scripts/smoke.py`.
Also check that a wrong token gets `unauthorized`, and that binding `0.0.0.0` *without* a token falls back to `127.0.0.1` (Output Log error).

## 5. API index
Editor Preferences → Plugins → Python → **Developer Mode** on, restart the editor, then:
```powershell
uv run python -m unreal_mcp.api_index build "$PWD\..\FlopperamUnrealMCP\Intermediate\PythonStub\unreal.py"
uv run python -m unreal_mcp.api_index search "spawn actor from class"
```
Record the symbol count and build time. Expect tens of thousands of symbols.

## 6. UE-Bench reference run
```powershell
cd Python; uv run python -m unreal_mcp.bench run --agent solution
```
Every task should pass (modeling is skipped without Blender on the PC). A failure here means the fake API disagrees
with the real engine: note the task and the `failures` line. Then `--agent none` should fail every task.

## 7. Report back
Paste: the UBT result, the smoke output, the API index count, the bench scoreboard (`bench-results/*/scoreboard.md`), and anything surprising (timings, warnings in the log).
