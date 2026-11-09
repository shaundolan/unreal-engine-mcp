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
| 3: Verification & autonomy | ✅ core done, see gaps | `pie_test` (assertions, `do`/`until`/`check` timeline, **real input**: `press`/`hold`/`axis`/`look` on keys or Enhanced Input actions, `frame_stats`, NavMesh repair reported as `navmesh_repaired`), `scene_assert`, static `performance_audit`, **packaging** (`package_project` / `package_status`, background UAT BuildCookRun; Linux build live, Win64 pending) |
| 4: Agent experience | 🚧 most done | Per-domain skills as MCP resources (`skill://`) and prompts; live `unreal://project`, `unreal://level/summary`, `unreal://selection`; **Claude Code agent pack** (7 specialist subagents, `/unreal-*` commands, MCP config, [CLAUDE_CODE_PLUGIN.md](CLAUDE_CODE_PLUGIN.md)); **in-editor agent panel** with a model-agnostic local runner ([AGENT_PANEL.md](AGENT_PANEL.md)). Planned: local text/image→3D import |
| 5: UE-Bench | 🚧 v0: 20 tasks | 20 tasks, reference 20/20 live and baseline 0/20 (`bench/results/`). Target: ~60 real-workflow tasks, pass rate per domain published each release, scored real-agent runs |

## Compared with Aura and Epic's native stack

**Aura** is Ramen VR's hosted product. Its column comes from its public description, not from testing it.
**Epic native** means the experimental plugins in UE 5.8.4 (`Engine/Plugins/Experimental`: ModelContextProtocol,
ToolsetRegistry, AIAssistant, 27 `Toolsets/*`), all disabled by default. Its column comes from reading their source, not
from running them. "?" means we don't know.

| Feature | Aura | Epic native (5.8, experimental) | UnrealMCP | Status |
|---|---|---|---|---|
| In-editor chat panel | ✅ | ✅ AI Assistant tab: a web view of Epic's hosted assistant, needs an Epic account | ✅ "UnrealMCP Agent" Slate tab: transcript, collapsible tool rows, stop, status line | compiles (Linux, 0 warnings); runner unit-tested; live check pending on the 4090 ([WINDOWS_TEST.md](WINDOWS_TEST.md) §10) |
| Choice of model | hosted models | Epic's backend; no provider hook in the C++ | Anthropic (own key), any OpenAI-compatible endpoint (local models), Claude Code CLI | unit-tested with fakes; no paid or real-model run on this branch |
| Works fully offline | ❌ | MCP server yes, assistant no | ✅ with a local model | — |
| MCP server for external agents | hosted endpoint + API key | ✅ HTTP `127.0.0.1:8000/mcp`, tools from ToolsetRegistry toolsets, writes client configs for Claude Code / Cursor / VS Code / Gemini / Codex | ✅ stdio, 110+ tools | ours live (Linux); Epic's not run here |
| Specialist agents | Dragon (Python), Telos (Blueprint), Verification, Level Design, VFX | none found (toolsets, not agents) | 7: blueprint, python-batch, level-designer, vfx, verifier, tech-artist, animator, as Claude Code subagents and panel specialists | prompts written, untested against a scored run |
| Blueprint graph authoring | Telos | no Blueprint-graph toolset in 5.8's list | `bp_export` / `bp_apply`, atomic and compiled | live |
| Python batch editing | Dragon | ? (Python Editor Script Plugin) | `execute_python` + batch subagent (preview, one transaction, per-item report) | live tool; subagent untested |
| Verification with real input | Verification agent | AutomationTestToolset (not assessed) | `pie_test` with key / Enhanced Input presses, `scene_assert`, `viewport_capture` + vision critique, `frame_stats` | live |
| VFX / Niagara | VFX agent | NiagaraToolsets (not assessed) | `niagara_edit` (placement, User params), materials; `niagara_apply` on a sibling branch | partial |
| Editor context (selection, level) | ? | Slate querier reads the editor UI | level, PIE state, selected actors, Content Browser selection and folder in every message; `unreal://selection` resource | compiles; unit-tested; live check pending (§10) |
| Undo an agent turn | ? | separate transaction buffer per assistant session | "Undo last agent turn": exactly the turn's transactions, refused if anything happened since | compiles; live check pending (§10) |
| Confirmation before destructive ops | ? | pending-file approval list | configurable rules (off / destructive / strict / all), also enforced in Claude Code mode via `--permission-prompt-tool` | unit-tested (fake editor); live check pending (§10) |
| Public benchmark | ❌ | ❌ | UE-Bench, 24 tasks with checkers | v0 |
| Cost | subscription | free (Epic account) | free; you pay your own model provider, or nothing with a local model | — |
| Platforms | Windows (?) | engine platforms | Linux live; Windows compiles pending | — |

**Building on Epic's stack instead of duplicating it:** Epic's AI Assistant can't take other providers (see
[AGENT_PANEL.md](AGENT_PANEL.md#why-not-extend-epics-ai-assistant)), so the panel stays ours. Epic's MCP server and
toolsets are complementary: the agent pack and the runner's Claude Code mode can attach Epic's server alongside ours
(`unreal-native`). Registering UnrealMCP's tools as Epic toolsets, so Epic's server and assistant can call them, is a
separate work item.

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
