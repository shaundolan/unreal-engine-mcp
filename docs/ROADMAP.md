# Roadmap

Goal: a local, nearly free agent platform for Unreal that can do any task a human does in the editor,
**measured** by a public benchmark rather than claimed.

Status as of plugin 0.3.0. "Live" means verified against a real editor (UE 5.8.4 Linux, GTX 1080), not only against the fake
plugin. The Windows/D3D12 run is pending: [WINDOWS_TEST.md](WINDOWS_TEST.md).

| Phase | Status | Contents |
|---|---|---|
| 0: Foundations | ✅ live (Linux) · ⏳ Win64 compile | Framed protocol, multi-client, command registry, safe-point command queue, timeouts, token, tests against the fake plugin, per-port dev scripts (`ue_linux.sh`, `ue_windows.ps1`) |
| 1: Universal layer | ✅ live | `execute_python`, reflection (describe/get/set/call), editor log, viewport capture, project context, console, editor actions, offline API index |
| 2: Domain tools | ✅ most done, see gaps | Scene, assets, materials, level, Niagara placement, sequencer, DataTables, **data types** (`data_types`: structs, enums, DataTables from JSON/CSV, DataAssets, curves, curve tables), **audio** (`metasound_apply` / `metasound_export`, `sound_cue_apply`), **Blueprint analysis** (`bp_explain` / `bp_review`), Enhanced Input, game setup, C++ source + Live Coding, Blender modeling. **Blueprint v2** (`bp_export` / `bp_apply`, atomic and compiled, wildcard array pins resolved), **UMG** (`widget_export` / `widget_apply`), **AI/BT** (`bt_export` / `bt_build`), **animation** (`animbp_apply` state machines/transitions, montages, notifies, BlendSpaces, IK retarget), **GAS** (`gas_edit` effects/abilities/grants, `gameplay_tags`), **world building** (`landscape_edit`, `foliage_scatter`, `pcg_graph`, `spline_edit`; live 4/4) |
| 3: Verification & autonomy | ✅ core done, see gaps | `pie_test` (assertions, `do`/`until`/`check` timeline, **real input**: `press`/`hold`/`axis`/`look` on keys or Enhanced Input actions, `frame_stats`, `spawn` with properties set before BeginPlay, `possess`, `capture` at a fixed PIE size, NavMesh repair reported as `navmesh_repaired`), `editor_health` with an `editor_unresponsive` watchdog, `scene_assert`, static `performance_audit`, **packaging** (`package_project` / `package_status`, background UAT BuildCookRun; Linux build live, Win64 pending) |
| 4: Agent experience | 🚧 | Per-domain skills, served as MCP resources (`skill://`) and prompts. **Local text/image→3D** (`mesh_generate` through [`servers/gen3d`](../servers/gen3d/): TripoSR / SF3D / Hunyuan3D 2.1 / TRELLIS.2 + FLUX.1-schnell, with Blender cleanup, PBR bake, LODs, collision, and a licence on every result) and **auto-rigging** (`auto_rig`: armature with Mannequin bone names, automatic weights, IK Rig, retargeted Mannequin animations). Planned: level/selection resources, an in-editor chat panel |
| 4b: Epic native MCP | 🚧 built, not live yet | `UnrealMCPToolsets` (24 tools as 8 Epic toolsets, one code path), `epic_*` gateway to Epic's server; study + strategy in [EPIC_NATIVE_MCP.md](EPIC_NATIVE_MCP.md) |
| 5: UE-Bench | 🚧 v0: 20 tasks | 20 tasks, reference 20/20 live and baseline 0/20 (`bench/results/`). Target: ~60 real-workflow tasks, pass rate per domain published each release, scored real-agent runs |

## Remaining gaps
- **Windows:** the plugin has not been compiled with MSVC yet. Live Coding, D3D12 frame timings and Win64 packaging are unverified (WINDOWS_TEST.md).
- **Input:** Enhanced Input action injection is accepted live, but its effect on a bound pawn hasn't been observed yet. Slate/UMG
  UI automation (clicking buttons, typing into widgets) is not done.
- **Frame timings:** render- and RHI-thread times read 0 offscreen on Linux and are reported as `unavailable`. There is no Unreal Insights
  / `stat` capture yet.
- **Animation:** linked anim layers, Control Rig nodes, `AnimNotify_<Name>` events in AnimBPs; generic AnimGraph nodes untested.
- **VFX / audio:** Niagara module graphs (only placement and User parameters today). MetaSound authoring
  (`metasound_apply` / `metasound_export` / `metasound_nodes`) and `sound_cue_apply` compile, but have not been run live yet
  (WINDOWS_TEST.md). There's no MetaSound Patch authoring, no Quartz, and no submix or attenuation assets.
- **World:** World Partition / data layers; landscape streaming proxies and edit layers beyond the first.
- **Gameplay:** GAS ability tasks, Gameplay Cues and custom calculations (AttributeSets are generated C++), multiplayer/replication testing (multi-client PIE).
- **Blueprints:** interfaces, event dispatchers bound across Blueprints, timelines, local variables, macros, promotable operators,
  select/switch, make/break struct, format text, async actions, Get Data Table Row, struct/enum/DataTable/curve authoring
  (`data_types`) and `bp_explain` / `bp_review` are built (`feat/bp-complete`). The C++ ops are not run live yet; the analysis and
  routing are unit-tested. Still missing: renaming or deleting dispatchers and timelines, editing interface signatures in place,
  Blueprint function libraries, and replicated variables or RPC flags.
- **Packaging:** only Win64 and Linux hosts. No console/mobile targets, no signing or upload.
- **Source control** (Perforce/git through the editor) is not integrated.
- **3D generation:** the pipeline was verified live (UE 5.8.4 Linux) with the mock backend and real Blender:
  - a 90 cm barrel imported with its baked texture, 4 LODs and convex collision, and re-imported safely;
  - a T-pose figure rigged with the engine's auto-characterisation (11 chains);
  - the Mannequin idle and walk retargeted onto it, with the walk verified in PIE by the moving foot bones.

  The real models (TripoSR, SF3D, Hunyuan3D, TRELLIS.2, FLUX) are wrapped but have only run on the 4090:
  WINDOWS_TEST.md §9b.

  Live findings now handled in code:
  - Blender FBX armatures import with a 100x scale and a 90° rotation on the root bone, so auto_rig defaults to GLB.
  - A re-import onto an existing skeleton keeps its old bone positions, so auto_rig deletes the old mesh, skeleton
    and physics asset first.
  - Interchange enables Nanite and puts materials and textures in `<name>/Materials` and `<name>/Textures`.
  - Exporting a Nanite mesh writes its coarse fallback, so mesh_export hands back the import source file, or turns
    Nanite off for the export.

  Remaining gaps:
  - auto_rig has no finger, face or twist bones, and its fit expects a T or A pose.
  - The engine's own skinning (GeometryScript `compute_smooth_bone_weights` + `SkeletonModifier`) is reachable from
    Python but unused. Blender's bone heat gives better weights.
