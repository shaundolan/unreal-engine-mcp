# Running the editor on Linux

Tested 2026-11-08: Unreal 5.8.4 (Linux zip from unrealengine.com/linux), Ubuntu, GTX 1080 8 GB, driver 580.
Results: smoke 16/16, UE-Bench reference 15/15, baseline 0/15 (`bench/results/2026-11-08-ue5.8.4-linux/`).

Plugin dev loop: `scripts/ue_linux.sh build|launch|quit|rebuild` (rebuild = quit over MCP, compile, relaunch, wait for ping).
Several editors can share the machine, one per `UNREAL_MCP_PORT`:
- logs and the pid file go to `/tmp/unreal-mcp/<port>/`;
- `quit` only signals the editor the script started on that port;
- `launch` and `build` refuse while the project is open in another editor.

Builds pass `-NoHotReloadFromIDE`. Without it, any other running editor makes UBT write a `-NNNN` hot-reload binary that the next
launch ignores, so the editor silently loads stale plugin code.

```bash
E=~/Downloads/Linux_Unreal_Engine_5.8.4          # wherever the zip was unpacked (77 GB)
P=$PWD/FlopperamUnrealMCP/FlopperamUnrealMCP.uproject
$E/Engine/Build/BatchFiles/Linux/Build.sh FlopperamUnrealMCPEditor Linux Development -Project="$P" -WaitMutex
$E/Engine/Binaries/Linux/UnrealEditor "$P" \
  "-ini:Engine:[/Script/PythonScriptPlugin.PythonScriptPluginSettings]:bDeveloperMode=True"
cd Python && uv run python ../scripts/smoke.py && uv run python -m unreal_mcp.bench run --agent solution
```

Notes:
- **Pre-RTX GPUs** (Pascal, e.g. GTX 10xx) have no mesh shaders, so Vulkan SM6 is skipped. The sample project
  targets SM5 as well as SM6, so the editor falls back to SM5 instead of exiting with "Vulkan device could not be created".
- **Close the editor over MCP** before rebuilding the plugin (no hot reload on Linux):
  `execute_python("import unreal; unreal.SystemLibrary.quit_editor()")`.
- **Run automation offscreen and unattended** (`scripts/ue_linux.sh launch` adds `-RenderOffscreen -unattended`;
  extra flags go in `UE_EXTRA_ARGS`). Nobody can answer a modal dialog in an offscreen editor, and without
  `-unattended` a crash opens the CrashReportClient window on the desktop (`ue_linux.sh close-crash-reporter` closes one
  left by this project's editor). With a visible window, a stale Vulkan
  swapchain ("AcquireNextImage() failed due to the outdated swapchain") can deadlock the editor's RHI, render and game threads.
  This is an engine bug in 5.8.4 Linux. `launch-gui` is for watching only.
- **Packaging** works here: `package_project()` produced a Linux build (BUILD SUCCESSFUL, 1.2 GB) that boots headless.
- **Live Coding is Windows-only.** `live_coding_compile` won't work here. Use a full rebuild instead.
- **A known 5.8.4 engine ensure** (`FAppTime ... IsInGameThread()` from `UpdateReflectionSceneData`) appears once per
  session. It's an engine bug, harmless; dismiss it.
- Rendering uses about 2–4 GB of VRAM for the sample and bench levels. Free the GPU of other models first.
