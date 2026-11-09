# Roadmap

Goal: a local, nearly free agent platform for Unreal that can do any task a human does in the editor,
**measured** by a public benchmark rather than claimed.

Status as of plugin 0.3.0. "Live" means verified against a real editor (UE 5.8.4 Linux, GTX 1080), not only against the fake
plugin. The Windows/D3D12 run is pending: [WINDOWS_TEST.md](WINDOWS_TEST.md).

| Phase | Status | Contents |
|---|---|---|
| 0: Foundations | ✅ live (Linux) · ⏳ Win64 compile | Framed protocol, multi-client, command registry, safe-point command queue, timeouts, token, tests against the fake plugin, per-port dev scripts (`ue_linux.sh`, `ue_windows.ps1`) |
| 1: Universal layer | ✅ live | `execute_python`, reflection (describe/get/set/call), editor log, viewport capture, project context, console, editor actions, offline API index |
| 2: Domain tools | ✅ most done, see gaps | Scene, assets, materials, level, Niagara placement, sequencer, DataTables, Enhanced Input, game setup, C++ source + Live Coding, Blender modeling. **Blueprint v2** (`bp_export` / `bp_apply`, atomic and compiled, wildcard array pins resolved), **UMG** (`widget_export` / `widget_apply`), **AI/BT** (`bt_export` / `bt_build`), **animation** (`animbp_apply` state machines/transitions, montages, notifies, BlendSpaces, IK retarget), **GAS** (`gas_edit` effects/abilities/grants, `gameplay_tags`), **world building** (`landscape_edit`, `foliage_scatter`, `pcg_graph`, `spline_edit`; live 4/4) |
| 3: Verification & autonomy | ✅ core done, see gaps | `pie_test` (assertions, `do`/`until`/`check` timeline, **real input**: `press`/`hold`/`axis`/`look` on keys or Enhanced Input actions, `frame_stats`, `spawn` with properties set before BeginPlay, `possess`, `capture` at a fixed PIE size, NavMesh repair reported as `navmesh_repaired`), `editor_health` with an `editor_unresponsive` watchdog, `scene_assert`, static `performance_audit`, **packaging** (`package_project` / `package_status`, background UAT BuildCookRun; Linux build live, Win64 pending) |
| 4: Agent experience | 🚧 | Per-domain skills, served as MCP resources (`skill://`) and prompts. Planned: level/selection resources, an in-editor chat panel, local text/image→3D import |
| 5: UE-Bench | 🚧 v0: 20 tasks | 20 tasks, reference 20/20 live and baseline 0/20 (`bench/results/`). Target: ~60 real-workflow tasks, pass rate per domain published each release, scored real-agent runs |

## Remaining gaps
- **Windows:** the plugin has not been compiled with MSVC yet. Live Coding, D3D12 frame timings and Win64 packaging are unverified (WINDOWS_TEST.md).
- **Input:** Enhanced Input action injection is accepted live, but its effect on a bound pawn hasn't been observed yet. Slate/UMG
  UI automation (clicking buttons, typing into widgets) is not done.
- **Frame timings:** render- and RHI-thread times read 0 offscreen on Linux and are reported as `unavailable`. There is no Unreal Insights
  / `stat` capture yet.
- **Animation:** linked anim layers, Control Rig nodes, `AnimNotify_<Name>` events in AnimBPs; generic AnimGraph nodes untested.
- **VFX / audio:** Niagara module graphs (only placement and User parameters today), MetaSounds.
- **World:** World Partition / data layers; landscape streaming proxies and edit layers beyond the first.
- **Gameplay:** GAS ability tasks, Gameplay Cues and custom calculations (AttributeSets are generated C++), multiplayer/replication testing (multi-client PIE).
- **Blueprints:** interfaces, event dispatchers bound across Blueprints, timelines, struct/enum asset authoring, promotable
  operator nodes (typed math functions work).
- **Packaging:** only Win64 and Linux hosts. No console/mobile targets, no signing or upload.
- **Source control** (Perforce/git through the editor) is not integrated.

## Adding a tool group
Most domain tools are thin Python modules in `Python/unreal_mcp/tools/` that send editor-side scripts from `editor_scripts/`. They
need no C++ rebuild. C++ is only for things Python can't reach: graph pins, input injection, frame timings, landscape height/weight IO, Slate.
PCG graph authoring turned out to be fully reachable from Python (`PCGGraph.add_node_of_type` / `add_edge`).

Create `Python/unreal_mcp/tools/<name>.py` with `register(mcp, get_conn)`. It's picked up automatically. Add tests against
`tests/fake_unreal.py`, and a UE-Bench task with a checker when the feature is user-visible.
