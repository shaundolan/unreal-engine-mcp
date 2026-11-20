# Epic's official Unreal MCP vs UnrealMCP: what to adopt, and which to use

This page compares the two feature by feature and makes a recommendation. [EPIC_NATIVE_MCP.md](EPIC_NATIVE_MCP.md) has the
internals of Epic's stack (server, ToolsetRegistry, the 54 toolsets) and how our two bridges into it work. This page
doesn't repeat that.

UnrealMCP is an independent MIT fork of flopperam/unreal-engine-mcp. It is not affiliated with Epic Games, Flopperam or Aura.

## Short answer

- **Use Epic's server *through* ours; don't replace ours.** Epic's server is good plumbing with broad per-asset tools. It
  can't do the out-of-process work our pipeline depends on: launching, rebuilding and recovering editors, packaging, Blender,
  and remote editors. It also lacks atomic whole-artifact authoring and in-PIE verification.
- **Recommendation: option (b), done incrementally, with ours staying the front door.**
  - Keep the Python server as the single client connection.
  - Reach Epic's toolsets through the `epic_*` gateway.
  - Keep moving our in-editor tools into `UnrealMCPToolsets`, so they are also Epic toolsets.
- **Don't** run the two as separate, peer client connections by default (option a), and **don't** enable Epic's plugins
  in a game's `.uproject` yet. Turn them on per launch with `-EnablePlugins=`.

## What was checked

| Source | What it covers |
|---|---|
| Epic docs: [Unreal MCP in Unreal Editor](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor), [Using Python with AI agents](https://dev.epicgames.com/documentation/unreal-engine/using-python-with-ai-agents-in-unreal-engine), [Run external AI agents in the Unreal Assistant panel](https://dev.epicgames.com/documentation/unreal-engine/run-external-ai-agents-in-the-unreal-assistant-panel-in-unreal-engine) | The pages are labelled **UE 5.9**, and several features they describe are not in 5.8.4 (see "Version drift") |
| Engine source, UE 5.8.4 Win64 installed build (`Engine/Plugins/Experimental/{ModelContextProtocol,ToolsetRegistry,AIAssistant,Toolsets/*}`) | Ground truth for what we can use today |
| Live probe, 2026-11-20: a blank, code-free project on Win64 5.8.4, `-nullrhi`, `-EnablePlugins=EditorToolset,GameplayTagsToolset -ModelContextProtocolStartServer -ModelContextProtocolPort=8568` | Epic's server was listening about 30 s after launch. It served 20 toolsets through 3 meta tools (`list_toolsets`, `describe_toolset`, `call_tool`) at 20–30 ms per call, and `IsPIERunning` / `SceneTools.get_current_level` returned `{"returnValue": ...}` as text. `resources/list` was empty, `prompts/list` returned HTTP 400, and `serverInfo.name` came back empty |

## Feature by feature

✅ strong, ◐ partial, ✗ none. "Epic" means 5.8.4 unless marked 5.9 (docs only).

| Area | Epic official (5.8.4, experimental) | UnrealMCP (this fork) |
|---|---|---|
| Status, licence | Experimental, `NoRedist`, off by default. On start it logs that data sent to your LLM is "Licensed Technology under the UE EULA" §6(e) | MIT, stable tool names, our own release cadence |
| Process model | Inside the editor process. It exists only while that editor runs | An out-of-process Python server plus an in-editor C++ bridge. The server outlives editor crashes and restarts |
| Transport | Streamable HTTP (POST JSON or SSE for progress), one route, `127.0.0.1:8000/mcp` by default. No stdio, no standing GET stream | stdio to the client; a length-framed JSON TCP socket to the editor (`55557`, or `UNREAL_MCP_PORT`) |
| Auth and exposure | No auth. Binds to loopback. Checks the `Origin` header against localhost to block DNS rebinding | Loopback by default. Binding another address requires `UNREAL_MCP_TOKEN`; without it the bridge refuses and falls back to 127.0.0.1 |
| Threading | Every tool runs on the game thread, one at a time; async tools return futures. A modal dialog or long compile stalls every call | The game thread too (`RunOnGameThread`), but the socket thread answers `editor_health` even when the game thread hangs, and stuck calls fail fast with `editor_unresponsive` |
| Undo / transactions | No transaction per toolset tool. `execute_tool_script` wraps a whole script in one transaction and **undoes it on error**. The deprecated library tools got an `FScopedTransaction` each | One undo step per authoring call (`bp_apply`, `scene_compose`, `material_edit`, `landscape_edit`, ...). `atomic` rollback and `dry_run` on the graph appliers. The agent panel's "undo last agent turn" |
| Discovery | Tool search by default: 3 meta tools, so the client discovers the rest. Eager mode lists about 900 dotted names, many over 64 characters | About 110 named tools, listed eagerly, with client-safe names. The gateway adds Epic's tools in `meta` or filtered `expand` mode |
| Schema conventions | Generated from UFunction or Python signatures and doc comments. Results come back as **text** (`{"returnValue": ...}`). 5.9 adds `AIAccessMode` Read/Write/Execute → `readOnlyHint` | FastMCP schemas from Python signatures. Structured dict results; images as real MCP image content. No tool annotations yet (`mcp` 1.5.0 predates them) |
| Client setup | `ModelContextProtocol.GenerateClientConfig` writes `.mcp.json`, Cursor, VS Code, Gemini and Codex configs | `mcp.json`, the Claude Code plugin and agent pack |
| Python | `execute_tool_script` is **sandboxed** (allow-listed modules; it only glues tool calls). 5.9 docs add `execute_python` and stub search | `execute_python`: any Unreal Python, with stdout and traceback. Plus an offline full-text index of the `unreal` API |
| Reflection | `ObjectTools`: `get/set/reset_properties`, `list_properties`, `search_subclasses` | `object_describe`, `property_get/set` (undoable), `function_call` on any UFUNCTION |
| Blueprints | 53 granular `BlueprintTools` plus a graph text DSL (`blueprint_dsl.py`, which snapshots and rolls back the graph on failure) | `bp_export` / `bp_apply`: the whole graph as one JSON spec, atomic, compiled, structured errors. Also `bp_review` lint |
| AnimBP, BT/Blackboard | ✗ authoring (BT and StateTree are read-only) | `animbp_apply` (state machines), `bt_build` |
| Materials | 22 + 13 granular tools | `material_edit` (whole graph per call), `material_instance` |
| Niagara | ✅ 56 tools, full stack authoring | `niagara_export` / `niagara_apply` (atomic), `niagara_particles` |
| Sequencer, Control Rig, MetaHuman, physics assets, cloth, Dataflow, MVVM | ✅ 300+ tools | ◐ `sequence_edit` only. **Delegate these to Epic** |
| PCG, UMG | ✅ granular | ✅ declarative `pcg_graph`, `widget_apply` |
| Landscape, foliage, splines, procedural buildings | ✗ | ✅ `landscape_edit`, `foliage_scatter`, `spline_edit`, `scene_compose`, the world builders |
| Assets and import | ✅ Asset, StaticMesh (LODs, Nanite, collision), tables, imports | ✅ `asset_manage`, `asset_import` (chunked upload to a remote editor), `asset_references` |
| PIE control | `StartPIE`, `StopPIE`, `IsPIERunning` | `editor_action` PIE start/stop at a fixed size, `pie_input`, `spawn_in_pie`, `pie_possess` |
| Runtime verification | ✗. It can start PIE but can't drive input or assert inside it | ✅ `pie_test` (real key and Enhanced Input presses, timelines, assertions, frame stats, captures), `scene_assert` |
| Viewport capture | `CaptureViewport` with a grid and label overlay, `CaptureEditorImage`, `CaptureAssetImage`, `WorldPosToScreenCoords` / `ScreenCoordsToWorld`. Arrives as base64 **text** through the toolset adapter | `viewport_capture` as real MCP image content, at an exact PIE size and offscreen. The gateway converts Epic's base64 images to images |
| Editor UI automation | ✅ SlateInspector (click, type, snapshot) | ✗. **Delegate** |
| Logs, console, config, plugins | ✅ LogsToolset, CVars, ConfigSettings, PluginToolset | ◐ `editor_log` with a cursor, `console_command` |
| Automation tests | ✅ AutomationTestToolset | ✗ in-tool (we have UE-Bench and pytest) |
| C++ | `CompileLiveCoding` | `cpp_source` (sandboxed) and `live_coding_compile` with parsed errors |
| Packaging, Blender, gen3D, benchmark | ✗ | ✅ `package_project`, `blender_run`, `mesh_generate`, UE-Bench |
| Editor lifecycle | ✗, by construction | ✅ launch, quit and rebuild per port, crash-reporter cleanup, health watchdog |
| Skills, prompts, resources | 21 skills, served as *tools*. No prompts, no resources. 5.9 adds SKILL.md folders | 9 SKILL.md workflows as MCP resources and prompts, plus live `unreal://` resources |
| Progress and cancel | ✅ `progressToken` (SSE) and `notifications/cancelled` | ✗ (long tools use job and status polling instead) |
| In-editor assistant | AIAssistant (Epic backend). 5.9 adds external agents over ACP (Claude Code, Codex, Cursor) | Our agent panel with any provider, or a local model |
| Telemetry | `ModelContextProtocol.EnableAnalytics=true` by default (SessionStart, ToolCall and SessionEnd events through the engine's analytics) | None |

### Version drift (5.9 docs vs 5.8.4 source)

The docs describe things the 5.8.4 binaries don't have:
- four meta tools (`describe_toolsets` plus `describe_tools`, with `include_schemas`) instead of 3;
- `AIRuntimeCallable` tools in cooked builds, and `AIAccessMode`;
- an `execute_python` tool and SKILL.md folder discovery;
- auto-start on by default;
- the ACP external-agent panel.

Expect 5.9 to move again. Our gateway now also accepts the plural `describe_toolsets` (see "In this PR").

## Adopt from Epic

These are ordered by value for the effort.

1. **Transactional scripts that roll back by default.** Epic's `_TransactionScope` (`programmatic.py`):
   - opens a transaction;
   - undoes on exit unless the script committed;
   - snapshots the undo count first, so a no-op transaction never pops someone else's undo entry;
   - only undoes when it is the outermost transaction.

   Give `execute_python` an opt-in `transactional=true` that does the same. Effort is small (Python only). Risk is low.
2. **Access-mode hints.** Tag each tool Read, Write or Execute, the way Epic does with `AIAccessMode` → `readOnlyHint`, plus
   `destructiveHint`, so clients can auto-approve reads. This needs an `mcp` SDK bump from 1.5.0 to a version with
   `ToolAnnotations`, so do it with that upgrade, not alone. Effort is medium; the risk is the SDK upgrade itself.
3. **Progress notifications and cancellation** for long tools (`pie_test`, `package_project`, `landscape_edit`, `mesh_generate`), instead of
   only polling. Epic sends progress over SSE every `ProgressIntervalSeconds`. Medium.
4. **Vision helpers for placement.** Add a grid/label overlay option to `viewport_capture`, and world↔screen coordinate
   conversion. Epic's `CaptureViewport` overlay plus `WorldPosToScreenCoords` lets a model say "move the lamp to cell C4". Small to medium.
5. **A lean tool profile.** Epic's tool search exists to save context. Offer an `UNREAL_MCP_PROFILE=lite` that lists only
   core tools plus a `describe`/`call` pair for the rest. Medium.
6. **Origin-header checks** if we ever serve MCP over HTTP (today we're stdio, plus a raw TCP socket browsers can't speak). Small, if needed.
7. **Client-config generation** like `GenerateClientConfig` (Cursor, VS Code, Gemini, Codex) next to our `mcp.json`. Small, nice to have.

Not worth copying: per-tool granular graph editing (our whole-artifact appliers are the point), and text-only results.

## Keep (Epic lacks these)

- **Whole-artifact atomic authoring**: `bp_apply`, `animbp_apply`, `widget_apply`, `bt_build`, `material_edit`, `niagara_apply`,
  `pcg_graph`, `scene_compose`. One spec in, one undo step, compiled, with structured errors.
- **Verification**: `pie_test`, `scene_assert`, `frame_stats`, `performance_audit`, captures as real images.
- **Out-of-process work**: editor launch, rebuild and recovery, `editor_health`, packaging, Blender and gen3D, the offline API index, remote editors with a token.
- **World building**: landscape, foliage, splines and procedural architecture. The game's `tools/build_city.py` pipeline lives here.
- **Skills as resources and prompts**, UE-Bench, and the agent panel with any model.

## Options

| Option | What it means | Effort | Risk | Verdict |
|---|---|---|---|---|
| **(a) Side by side** | Clients connect to both servers: ours over stdio, Epic's over HTTP | None (a launch flag plus a `claude mcp add`) | Duplicate tools under two names, two discovery styles, ~900 Epic tools competing for context; Epic's server dies with every editor restart. Agents pick the wrong tool more often | Fine for occasional manual use, not the default |
| **(b) Toolset provider on Epic's server** | Our in-editor tools are also registered as Epic toolsets (`UnrealMCPToolsets`, 24 tools today), and ours proxies Epic's (`epic_*` gateway) | Built. Next steps: live-verify on Win64; port `pie_test` to a C++ async state machine (L); add `viewport_capture` as a native `IModelContextProtocolTool` so it returns real image content (S); generate the manifest in CI (S) | It tracks an experimental API, contained by pinning to raw `FToolset` (one adapter file). Our out-of-process tools can never move into Epic's server | **Recommended**, with ours as the front door |
| **(c) Standalone** | Ignore Epic's stack | None | We'd be rebuilding Niagara, Sequencer and Control Rig coverage that Epic gives away, and users of Epic's assistant would never see our tools | No |

So the decision is (b) with ours as the entry point: one client connection (`unreal`) that carries our tools natively and
Epic's through `epic_*`, plus `UnrealMCPToolsets` for people who only run Epic's server or assistant.

## For the game project (NYCity)

- **Leave `NycCity.uproject` alone for now.**
  - The Epic plugins are experimental and `NoRedist`.
  - They add editor startup time and RAM on a 32 GB PC that has bluescreened under load.
  - `tools/build_city.py` gains nothing from them: it talks to our bridge on TCP.
  - `UnrealMCPToolsets` isn't vendored in the game yet.
- **Opt in per launch** when an agent needs Epic's Niagara, Sequencer, Control Rig or UI-automation tools. No project edits are needed:
  ```
  UnrealEditor.exe NycCity.uproject -EnablePlugins=EditorToolset,NiagaraToolsets -ModelContextProtocolStartServer -ModelContextProtocolPort=8568
  ```
  Then point ours at it with `UNREAL_MCP_EPIC_URL=http://127.0.0.1:8568/mcp`. The `-EnablePlugins` route was verified on 5.8.4 with a blank project and `EditorToolset,GameplayTagsToolset` (see "What was checked"); NiagaraToolsets in the game project is not yet tested.
  - Choose a port per editor; 8000 is a common dev port.
  - To turn off the analytics events, set the CVar `ModelContextProtocol.EnableAnalytics=0` (for example under `[ConsoleVariables]` in your user `Engine.ini`).
- **Revisit at 5.9.** If the 5.9 features hold (runtime tools in cooked builds, the ACP panel, SKILL.md discovery), vendor
  `UnrealMCPToolsets` into the game and drop our SKILL.md files where Epic's assistant finds them.

## In this PR

- This document, plus links to it from the README and `ROADMAP.md`.
- The `ROADMAP.md` comparison cells that were stale ("no Blueprint-graph toolset", "?", "not assessed") are corrected from the 5.8.4 source.
- **Gateway forward-compatibility**:
  - `epic_describe_toolset` / `expand` now also work against servers that expose the batched
    `describe_toolsets` meta tool (the 5.9 docs). The arguments come from that tool's own input schema, and several reply shapes are accepted.
  - The tests run against a stub (`Python/tests/test_epic_gateway.py`, 15 pass).
  - The rest of the Python suite fails the same 9 tests on Windows as `origin/main`, so this PR adds no new failures.
- `EPIC_NATIVE_MCP.md` §8 now records the live Win64 check of Epic's server.
