# Epic's native MCP stack (UE 5.8) and UnrealMCP

UE 5.8 ships its own, experimental, in-editor MCP server plus a tool framework and about 870 agent tools. This page
covers how that stack works (from the 5.8.4 Linux engine sources), what it can and can't do compared with UnrealMCP,
how UnrealMCP now plugs into it from both sides, and a strategy.

Everything lives in `Engine/Plugins/Experimental/`. All of it is `IsExperimentalVersion`, `NoRedist`, and **off by default**.

| Plugin | Role |
|---|---|
| `ModelContextProtocol` ("Unreal MCP") | The MCP server. It runs inside the editor process over Streamable HTTP. It also writes client configs. |
| `ToolsetRegistry` | The tool framework: `UToolsetDefinition`, `FToolset`, `FToolsetRegistry`, JSON schema generation, async results, agent skills. |
| `AIAssistant` | Epic's in-editor assistant. It consumes the same ToolsetRegistry. |
| `Toolsets/*` | 27 plugin folders, 54 toolsets, 872 tools (plus 4 skill tools in ToolsetRegistry). |

## 1. The server

**Start.** Use either of these:
- `-ModelContextProtocolStartServer` on the command line. This also works for commandlets. The old `-StartModelContextProtocolServer` is deprecated.
- `bAutoStartServer=True` in the settings. It applies to interactive editors only and is skipped in commandlets, because a cook would race the editor for the port.

You can also call `IModelContextProtocolModule::Get()->StartServer(Port, Path)` from code.

**Settings** live in `UModelContextProtocolSettings`, which is `config=EditorPerProjectUserSettings`. The file is
`Saved/Config/<Platform>Editor/EditorPerProjectUserSettings.ini`, section
`[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]`. They are also shown in Editor Preferences → Model Context Protocol.

| Key | Default | Notes |
|---|---|---|
| `ServerPortNumber` | `8000` | `-ModelContextProtocolPort=N` overrides it |
| `ServerUrlPath` | `/mcp` | An invalid path falls back to `/mcp` |
| `bAutoStartServer` | `False` | |
| `bEnableToolSearch` | `True` | `tools/list` returns only `list_toolsets`, `describe_toolset`, `call_tool`. `False` registers every toolset tool natively as `<Toolset>.<tool>`. |

Toolsets and tools can be hidden with `UToolsetRegistrySettings` `BlockedNames` / `AllowedNames` (substring or `/regex/`; block wins).
That class also has `AgentSkillBlockedNames` / `AgentSkillAllowedNames` and a block list for `SetObjectProperties`.

**Transport.** Streamable HTTP on one route. Protocol versions `2025-11-25`, `2025-06-18` and `2024-11-05` are negotiated.
- `POST` carries JSON-RPC. The reply is `application/json`, or `text/event-stream` when the call carries a `progressToken`, which is how progress notifications are sent.
- `GET` returns BadMethod, so there is no standing server-push channel. `tools/list_changed` reaches a session only while it has a call in flight.
- `DELETE` ends a session.
- Every method except `initialize` and `ping` needs an `Mcp-Session-Id` header.
- Methods: `initialize`, `ping`, `tools/list` (paginated), `tools/call`, `resources/list`, `resources/read`, `notifications/initialized`, `notifications/cancelled`. Capabilities are `tools.listChanged` and `resources`.
- No prompts.
- The server name is `unreal-mcp`.

**Network and auth.**
- The listener uses the engine HTTP server, which binds `localhost` by default (`[HTTPServer.Listeners] DefaultBindAddress`, per-port `ListenerOverrides`).
- The `Origin` header is checked against localhost, 127.0.0.1 and [::1] to block DNS rebinding.
- **There is no authentication at all.** Binding it to another interface would expose unauthenticated editor control.
- On start it logs that data sent to your LLM provider is "Licensed Technology under the UE EULA" (Section 6(e)).

**Lifetime.** The server is a ticker on the editor's game thread, so it exists only while that editor runs, and every tool runs on the game thread.
- A modal dialog or long compile stalls every request.
- An editor crash drops every session.
- Clients must re-`initialize` after a restart, because session ids are per process.

**Client configs.** The console command `ModelContextProtocol.GenerateClientConfig <ClaudeCode|Cursor|VSCode|Gemini|Codex|All>` writes the files below. They go in the engine root for source builds, or in the project directory for installed builds such as the Linux binary:

| Client | File | Entry |
|---|---|---|
| Claude Code | `.mcp.json` | `{"type":"http","url":"http://127.0.0.1:<port><path>"}` |
| Cursor | `.cursor/mcp.json` | |
| VS Code / Copilot | `.vscode/mcp.json` | |
| Gemini | `.gemini/settings.json` | `httpUrl` |
| Codex | `.codex/config.toml` | |

Each file gets a server entry named `unreal-mcp`.

**Tools** reach the server in two ways:
1. **ToolsetRegistry (current).** `ModelContextProtocolEditor` subscribes to `FToolsetRegistry::OnToolsetRegistered` and republishes all toolsets whenever one is added. It uses the three meta tools in tool-search mode, or one adapter per tool in eager mode. Results are always **text**: the adapter wraps the tool's JSON string with `MakeTextResult`. A toolset tool that returns an `FToolsetImage` therefore arrives as base64 inside JSON text, not as MCP image content.
2. **Native `IModelContextProtocolTool` (`AddTool`).** These are always listed in `tools/list`, even in tool-search mode. They can return image or audio content, an output schema and progress. `UModelContextProtocolToolLibrary` (Blueprint tool libraries) is the deprecated predecessor, superseded by `UToolsetDefinition`.

**Resources.** Register an `IModelContextProtocolResourceProvider` with `IModelContextProtocolModule::AddResourceProvider`; it implements `ListResources` and `ReadResource(uri)`. No engine plugin registers one in 5.8.4, so the native server serves no resources.

## 2. Defining tools

**C++ (`UToolsetDefinition`).**
- A `UCLASS` deriving from `UToolsetDefinition` has static `UFUNCTION(meta=(AICallable))` tools. `meta=(AIIgnore)` silences one; any other non-static or non-AICallable UFunction is logged as an error.
- The module registers the class with `UToolsetRegistry::RegisterToolsetClass(UMyToolset::StaticClass())`.
- **Schema.** `FJsonSchemaGenerator` turns the UFunction's parameters and return type into a JSON schema. Tool and parameter descriptions come from the UHT doc comment (`/** ... @param ... @return */`). The toolset description is the class tooltip, i.e. its doc comment.
- **Names.** The toolset is `<ModuleOrPythonPackage>.<ClassName>`, e.g. `GameplayTagsToolset.GameplayTagsToolset`, and a tool is `<Toolset>.<Function>`.
- **Async.** Return a `UToolCallAsyncResult` subclass (`...String`, `...Void`, `...Image` or your own) and complete it later. Images use `FToolsetImage {MimeType, Data(base64)}`.
- Custom JSON converters can be registered per property type. Niagara and WorldConditions use them.

**C++ (raw `FToolset`).** Subclass `UE::ToolsetRegistry::FToolset` and implement:
- `GetToolsetName`, `GetToolsetVersion`, `GetToolsetDescription`;
- `GetJsonSchemaInternal`, which returns `{"name","version","description","tools":[{"name":"<Toolset>.<tool>","description","inputSchema"}]}`;
- `ExecuteToolInternal(tool, jsonArgs)`, which returns `TFuture<TValueOrError<FString,FString>>`.

Register the instance with `UToolsetRegistrySubsystem::ToolsetRegistry.RegisterToolset(...)`. `MCPClientToolset` does this, and so does **our `UnrealMCPToolsets`**. Use this route when tools are data-driven rather than UFunctions.

**Python.** Write a `@unreal.uclass()` subclass of `unreal.ToolsetDefinition`, with `@toolset_registry.tool_call` on static methods. Type annotations are required; they become `unreal.ufunction(static=True, meta={'AICallable': ''})`. Exceptions become script errors and then tool errors. Register it with `toolset_registry.registration.Registration([MyTools]).register()` from `Content/Python/init_unreal.py`. Python toolset names are dotted module paths, e.g. `aimodule_toolset.toolsets.behavior_tree.BehaviorTreeTools`.

**Skills.**
- A skill is a `UAgentSkill` (`Description`, `Instructions`, overridable `GeneratePrompt`). It can be defined in C++, in Python (`@toolset_registry.agent_skill`: the docstring is the description, the `instructions` attribute the instructions) or as a Blueprint asset.
- They are served through the built-in `AgentSkill` toolset: `ListSkills`, `GetSkills`, `CreateSkill`, `UpdateSkill`. The last two create or modify Blueprint skill assets.
- They are tools, **not** MCP resources or prompts, and there are no SKILL.md files.
- 5.8.4 ships 21 skills: EditorToolset 4, Niagara 7, PCG 1 C++ plus 8 assets, Dataflow 1, ChaosCloth 1.

## 3. Epic's toolsets in 5.8.4

None is enabled by default. `AllToolsets` enables 21 of the 26, leaving out ChaosClothAsset, LiveCoding, MetaHumanGenerator, SequencerAnimMixer and MCPClient.

| Plugin | Tools | Highlights |
|---|---:|---|
| AnimationAssistantToolset (Py) | 319 | Sequencer 140, Sequencer Control Rig 72, Control Rig asset/graph authoring 44, keyframing 22, outliner 18, conditions 9, custom bindings 8, FBX/anim import/export 6 |
| EditorToolset (C++ + Py) | 251 | Blueprint 53 (incl. a graph **text DSL**: `write_graph_dsl`/`read_graph_dsl`), Material 22, SkeletalMesh 22, Asset 21, EditorApp 21 (CaptureViewport with annotation overlay, CaptureEditorImage, StartPIE/StopPIE, camera, selection), Scene 20, Actor 17, StaticMesh 16, MaterialInstance 13, DataTable 10, CurveTable 9, StringTable 8, Object (reflection) 6, Logs 4, Primitive 4, Texture 2, DataAsset 1, Programmatic 2 (`execute_tool_script`: sandboxed Python that batches tool calls, transactional; **not** general Python) |
| NiagaraToolsets | 56 | Full system/emitter/module/renderer/stack-input authoring, compile state, stack issues + fixes |
| PCGToolset | 31 | Graph authoring, spawn/execute graph instances, node data view, DrawSpline |
| UMGToolSet | 23 | Create/add/move/wrap/replace widgets, bind events, compile |
| DataflowAgent | 22 | Dataflow graph authoring |
| SequencerAnimMixerToolset (Py) | 21 | Anim mixer layers and transitions |
| PhysicsToolsets | 17 | Physics asset bodies, shapes, constraints |
| PluginToolset | 17 | List, create and enable plugins, edit descriptors |
| GASToolsets | 14 | Live ASC inspection, AttributeSets, Gameplay Cues |
| SlateInspectorToolset | 14 | Editor UI automation: Snapshot, Click, Type, PressKey, Drag, FillForm, Screenshot |
| MetaHumanGenerator (Py) | 9 | Body shape, skin, eyes, create |
| MVVMToolset | 9 | Viewmodels and view bindings |
| StateTreeToolset (Py) | 9 | **Read-only** inspection |
| ConfigSettingsToolset | 8 | Read/write any settings section |
| AIModuleToolset (Py) | 7 | **Read-only** Behavior Tree inspection |
| AutomationTestToolset | 7 | Discover/run automation tests (async) |
| ConversationToolset (Py) | 7 | Read-only |
| DataRegistryToolset | 7 | Read-only |
| GameFeaturesToolset | 7 | Activate/deactivate game features |
| ChaosClothAssetToolset | 6 | Clothing assets |
| GameplayTagsToolset | 6 | List/add/remove/rename tags, referencers |
| SemanticSearchToolset | 2 | Hybrid vector + BM25 asset search |
| WorldConditionsToolset | 2 | Read-only |
| LiveCodingToolset | 1 | `CompileLiveCoding` |
| MCPClientToolset | n | Proxies **external** MCP servers (Streamable HTTP or legacy SSE, bearer token or OAuth 2.0 PKCE; no stdio) as toolsets, configured in Editor Preferences → MCP Toolset Servers |
| **Total** | **872** (+4 skill tools) | 54 toolsets |

The images Epic returns are EditorApp `CaptureAssetImage`, `CaptureEditorImage` and `CaptureViewport`, and SlateInspector `Screenshot`. Over MCP they arrive as base64 text (see §1).

## 4. Limits

- **Experimental.** The APIs moved within 5.8: `UModelContextProtocolToolLibrary` is already deprecated, and the plugin has a compatibility shim for branches without `GetOnPostEngineInit`. Plan on changes in 5.9.
- **Localhost only, no auth.** You can't drive a remote or headless build box safely without a tunnel plus your own auth.
- **Editor-process lifetime.** There is nothing to talk to before the editor is up. The server can't launch, rebuild or restart the editor, can't recover from a crash, and can't run while the editor sits in a modal dialog. UnrealMCP's out-of-process server can do all of these (`ue_linux.sh launch/quit/rebuild`, clean errors on timeouts, reconnect).
- **Game thread only.** Long tools block the editor, and many tools are async futures because of it.
- **Text results** for toolset tools, no MCP prompts, and no resources from engine plugins.
- **Tool search by default.** Clients see three meta tools and must discover the rest. That saves context, but costs round trips and makes tool selection depend on the model reading `describe_toolset` output.
- **Eager mode** lists about 900 tools with dotted names longer than 64 characters (e.g. `editor_toolset.toolsets.blueprint.BlueprintTools.create_node`). That is too many for most clients' context, and some clients' tool-name rules (`^[A-Za-z0-9_-]{1,64}$`) don't allow these names.
- **No general Python execution tool.** Python is only reachable through the sandboxed `execute_tool_script`.
- **No out-of-editor work** in Epic's stack: no packaging, no Blender, no offline API index, no benchmarks.

## 5. Capability map: Epic native vs UnrealMCP

"Ours" is UnrealMCP 0.3.0, about 100 tools. ✅ means strong, ◐ partial, ✗ none.

| Area | Epic native | UnrealMCP |
|---|---|---|
| Arbitrary Python / reflection | ◐ ObjectTools get/set_properties; sandboxed `execute_tool_script` | ✅ `execute_python`, `object_describe`, `property_get/set`, `function_call` (any UFUNCTION) |
| Blueprint graphs | ✅ 53 granular tools plus a text DSL | ✅ `bp_export` / `bp_apply`: the whole graph as one JSON spec, **atomic with rollback** (`atomic`, `dry_run`), compiled, structured errors, one undo step |
| Animation Blueprints | ✗ | ✅ `animbp_export` / `animbp_apply` (state machines, transitions, asset players) |
| Montages, BlendSpaces, IK retarget | ◐ via Sequencer import/export | ✅ `anim_edit` |
| Control Rig, Sequencer keying | ✅ 300+ tools | ◐ `sequence_edit` (cameras, bindings, transform keys, cuts) |
| UMG | ✅ granular, 23 tools | ✅ `widget_apply`, a whole tree in one call |
| MVVM | ✅ | ✗ |
| Behavior Trees / Blackboards | ◐ read-only | ✅ `bt_build` authors both from one spec |
| StateTree | ◐ read-only | ✗ |
| Materials | ✅ 35 granular tools | ✅ `material_edit` (whole graph per call), `material_instance` |
| Niagara | ✅ 56 tools, full authoring | ◐ placement and User parameters |
| PCG | ✅ 31 | ✅ `pcg_graph` declarative spec |
| Landscape, foliage, splines | ✗ | ✅ `landscape_edit` (procedural sculpt, heightmap import, paint layers), `foliage_scatter`, `spline_edit` |
| Procedural architecture | ✗ | ✅ `create_town`, castles, houses, bridges, mazes, ... |
| Scene and actors | ✅ Scene, Actor, Primitive | ✅ `scene_compose` (batched patterns, one undo), `scene_query`, `scene_brief`, `scene_assert` |
| Assets and import | ✅ incl. mesh/texture/table import | ✅ `asset_manage`, `asset_import` (incl. chunked upload to a remote editor), `asset_references` |
| Mesh setup (LODs, collision, Nanite, sockets) | ✅ | ◐ `mesh_collision`, `instanced_mesh_place` |
| Physics assets, cloth, MetaHuman, Dataflow | ✅ | ✗ |
| GAS | ◐ inspection and cues | ✅ `gas_edit` (effects, abilities, grants, AttributeSets), `gameplay_tags` |
| Gameplay Tags | ✅ | ✅ |
| Enhanced Input, game mode setup | ✗ | ✅ `enhanced_input`, `game_setup` |
| PIE runtime verification | ◐ StartPIE/StopPIE | ✅ `pie_test`: real key/action input, timeline `do`/`until`/`check`, assertions on actors/properties/log, frame stats; `pie_input`; `scene_assert` |
| Viewport images | ✅ CaptureViewport with grid/label overlay (as base64 text) | ✅ `viewport_capture` as real MCP image content |
| Editor UI automation | ✅ SlateInspector | ✗ |
| Logs, console, config, plugins | ✅ | ◐ `editor_log` with cursor, `console_command`; ✗ config/plugin management |
| C++ | ◐ LiveCoding compile | ✅ `cpp_source` (sandboxed read/write) and `live_coding_compile` with parsed errors |
| Automation tests | ✅ | ✗ (we have UE-Bench and pytest instead) |
| Packaging | ✗ | ✅ `package_project` / `package_status` (UAT BuildCookRun in the background) |
| Performance | ✗ | ✅ `frame_stats`, `performance_audit` |
| Semantic asset search | ✅ | ◐ `search_assets` (name/class/path) |
| API knowledge | ✗ | ✅ offline FTS index of the whole `unreal` Python API |
| 3D modeling | ✗ | ✅ headless Blender (`blender_run`) with auto-import |
| Skills | ◐ 21, as tools | ✅ 9 SKILL.md workflows as MCP resources and prompts |
| Benchmark | ✗ | ✅ UE-Bench (24 tasks with checkers) |
| Process control | ✗ (in-process) | ✅ launch/quit/rebuild, multi-editor per port, crash-reporter cleanup, timeouts |
| Remote editor | ✗ | ✅ token-gated bind, chunked uploads |
| Linux | ✅ (same binary) | ✅ live-verified, plus Linux dev scripts |

Epic is broader in the asset domains it owns: Niagara, Control Rig and Sequencer, physics, MVVM, Slate UI driving, and config. UnrealMCP is stronger at
**whole-artifact authoring with verification**, **runtime testing**, **world building**, everything **outside the editor
process**, and measurement.

## 6. Prototype: UnrealMCP tools inside Epic's server (`UnrealMCPToolsets`)

The plugin is `UnrealMCPToolsets/`. It is a separate plugin, `EnabledByDefault: false`, so UnrealMCP itself never depends on experimental plugins. It depends on `UnrealMCP` and `ToolsetRegistry`.

**How it works.**
- The editor module registers 8 raw `FToolset`s with Epic's ToolsetRegistry at PostEngineInit: `UnrealMCP.Blueprint`, `.UI`, `.AI`, `.Scene`, `.Assets`, `.Editor`, `.Reflection` and `.Play`, with 24 tools between them.
- The tools are data-driven. `UnrealMCPToolsets/Resources/UnrealMCPToolsets.json` is **generated** from the Python server's FastMCP tool definitions (same names, descriptions and input schemas) by
  `cd Python && uv run python -m unreal_mcp.native_toolsets`. The curated list is `CURATED` in `Python/unreal_mcp/native_toolsets.py`.
- **There is one code path behind two front doors.** Each tool dispatches into `UEpicUnrealMCPBridge`, the same command registry the TCP server uses:
  - `command` tools (`bp_export/apply`, `animbp_export/apply`, `widget_export/apply`, `bt_export/build`, `level_open`, `project_context`, `console_command`, `editor_log`, reflection, `pie_input`) call `ExecuteCommandJson(<command>, params)`.
  - `editor_script` tools (`scene_compose`, `scene_brief`, `actor_inspect`, `level_inspect`, `search_assets`, `asset_references`) run the very same `editor_scripts/<name>.py` text, wrapped the same way as `run_editor_script`, through the `execute_python` command.
  - `frame_stats` is an **async** tool. It starts the recorder, polls it on the core ticker, and resolves the `TFuture` when done, so the HTTP request stays open while the editor renders the frames.
- **Arguments.** Schema defaults are filled in, nulls dropped and renames applied (`class_name` → `class`).
- **Results.** Handlers that report `ok: false` (`bp_apply` & co) become tool errors carrying the full structured report.
- **Tests.** `tests/test_native_toolsets.py` fails when the committed manifest drifts. It also calls every curated Python tool and checks that the native backend would send the same command or script with the same parameters.

**Not ported yet.** These are orchestrations of many round trips on the Python side and need a C++ async state machine, or another approach, first:
- `pie_test` (PIE start → wait → steps → assertions → stop),
- `landscape_edit` (height fields computed in Python),
- `anim_edit`, `package_project` (subprocess management),
- the legacy world builders,
- `viewport_capture`: it works, but would arrive as base64 text, which is useless to a model.

**Adding a tool:** add it to `CURATED`, regenerate, run `scripts/sync_plugin.sh`, rebuild.

## 7. Gateway: Epic's tools through UnrealMCP

Set `UNREAL_MCP_EPIC_URL=http://127.0.0.1:<port>/mcp` for the UnrealMCP server, and it also exposes Epic's toolsets (`Python/unreal_mcp/tools/epic.py`, using a small Streamable HTTP client in `epic_client.py`):

- **`UNREAL_MCP_EPIC_MODE=meta` (default).** Adds `epic_list_toolsets`, `epic_describe_toolset` and `epic_call_tool`.
  - Discovery is lazy, so the editor can start after the server.
  - It works against Epic's tool-search mode and its eager mode.
  - Images come back as MCP image content. That includes `FToolsetImage` results, which Epic's adapter sends as base64 JSON text.
  - It re-initializes after an editor restart.
- **`UNREAL_MCP_EPIC_MODE=expand`.** At startup it also registers each Epic tool as its own tool, `epic_<ToolsetClass>_<tool>`. Names are sanitized to `[A-Za-z0-9_-]` and at most 64 characters, and the tool keeps Epic's schema.
  - Use `UNREAL_MCP_EPIC_TOOLSETS=niagara,pcg` (comma-separated substrings) to keep the list small.
  - Without a filter, `UnrealMCP.*` is skipped because it is native here.
  - If the editor isn't up at startup, only the meta tools are registered.
- `UNREAL_MCP_EPIC_PREFIX` changes `epic_`.

The reverse direction also works without code. Run UnrealMCP over HTTP, then add it in Editor Preferences → MCP Toolset Servers (MCPClientToolset, Streamable HTTP). Its tools then show up in Epic's AI Assistant and server. The prototype doesn't need this, because UnrealMCPToolsets is in-process.

## 8. Setup and live verification

**Status.**
- Built here: `scripts/ue_linux.sh build` reports `build ok (0 warnings)` on UE 5.8.4 Linux, clang, with `UnrealMCPToolsets` and the Epic plugins enabled in the sample project.
- Unit-tested against fakes: the manifest/parity tests and the gateway tests, which run against a fake of Epic's Streamable HTTP server in both tool modes.
- **Not yet live-verified.** The live run is assigned to the RTX 4090 box, per the steps below and `docs/WINDOWS_TEST.md` §10b. This GTX 1080 had no free VRAM.

**What the sample project enables.** `FlopperamUnrealMCP.uproject` turns on `ModelContextProtocol`, `ToolsetRegistry`, `EditorToolset`, `UMGToolSet`, `GameplayTagsToolset` and `UnrealMCPToolsets`.

To use this in your own project:
1. Copy `UnrealMCP/` and `UnrealMCPToolsets/` into `Plugins/`.
2. Enable the same plugins. Add `AllToolsets` if you want all of Epic's toolsets, at the cost of VRAM and startup time.

**Start the server.** Port 8000 is a common dev port, so pick your own. Linux:
```bash
UNREAL_MCP_PORT=55568 UE_EXTRA_ARGS="-ModelContextProtocolStartServer -ModelContextProtocolPort=8568" scripts/ue_linux.sh launch
```
Windows:
```powershell
$env:UNREAL_MCP_PORT=55568; $env:UE_EXTRA_ARGS="-ModelContextProtocolStartServer -ModelContextProtocolPort=8568"; scripts\ue_windows.ps1 launch
```
For eager mode, where every tool appears in `tools/list`, append
`-ini:EditorPerProjectUserSettings:[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]:bEnableToolSearch=False`.
To make the server permanent instead, set `bAutoStartServer=True` and `ServerPortNumber=8568` in Editor Preferences → Model Context Protocol.

**Expected in `editor.log`.**
- `UnrealMCPToolsets: registered 8 toolsets (24 tools) with the ToolsetRegistry`.
- `Starting MCP server on port 8568`.
- Either `Tool search enabled: registered 3 meta-tools (N toolsets discoverable via list_toolsets)`, or in eager mode `Tool search disabled: registered N ToolsetRegistry tool adapters`.

N should include our 8 toolsets and our 24 tools.

**Verify over HTTP.**
```bash
cd Python && uv run python ../scripts/epic_mcp_smoke.py --url http://127.0.0.1:8568/mcp
```
Expected output, with exit code 0:
- `server: {'name': 'unreal-mcp', ...} protocol: 2025-11-25`.
- `tools/list: 3 tools (tool_search)`, or about 308 tools in eager mode with these plugins (EditorToolset 251, UMG 23, GameplayTags 6, AgentSkill 4, ours 24). Eager mode also prints `our tools in tools/list: [UnrealMCP.AI.bt_build, ...]` with all 24.
- `ours: ['UnrealMCP.AI', 'UnrealMCP.Assets', 'UnrealMCP.Blueprint', 'UnrealMCP.Editor', 'UnrealMCP.Play', 'UnrealMCP.Reflection', 'UnrealMCP.Scene', 'UnrealMCP.UI']`.
- Our tools:
  - `ok UnrealMCP.Editor.project_context` with the project name and engine version;
  - `ok UnrealMCP.Scene.scene_brief` with actor_count (the editor-script path);
  - `ok UnrealMCP.Play.frame_stats` with `frame_ms` and `fps_avg` (the async path; it takes about 30 frames);
  - `ok UnrealMCP.Blueprint.bp_export -> error: ...` naming the missing Blueprint (the error path).
- Epic's tools: `ok EditorToolset.EditorAppToolset.IsPIERunning: false` and `ok ...SceneTools.get_current_level`.

The same checks with curl, in tool-search mode:
```bash
U=http://127.0.0.1:8568/mcp
SID=$(curl -s -D - -o /dev/null $U -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}' | tr -d '\r')
H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H "Mcp-Session-Id: $SID")
curl -s $U "${H[@]}" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
curl -s $U "${H[@]}" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
curl -s $U "${H[@]}" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_toolsets","arguments":{}}}'
curl -s $U "${H[@]}" -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"call_tool","arguments":{"toolset_name":"UnrealMCP.Editor","tool_name":"project_context","arguments":{}}}}'
```

**Point Claude Code at it.** There are three ways:
- **Epic's server directly**, with our tools inside it:
  `claude mcp add --transport http unreal-native http://127.0.0.1:8568/mcp`. Alternatively, run `ModelContextProtocol.GenerateClientConfig ClaudeCode` in the editor console, which writes `<Project>/.mcp.json`. That file uses the configured port, so set `ServerPortNumber` first if you started with `-ModelContextProtocolPort`.
- **UnrealMCP as a superset gateway.** Our ~100 tools stay native and Epic's are reached via `epic_*`:
  ```bash
  claude mcp add unreal -e UNREAL_MCP_PORT=55568 -e UNREAL_MCP_EPIC_URL=http://127.0.0.1:8568/mcp \
    -- uv --directory /path/to/unreal-engine-mcp/Python run unreal_mcp_server_advanced.py
  ```
  Add `-e UNREAL_MCP_EPIC_MODE=expand -e UNREAL_MCP_EPIC_TOOLSETS=Niagara,PCG` to get first-class tools for chosen toolsets.
  - Expected: `epic_list_toolsets` lists Epic's toolsets and ours.
  - Expected: `epic_call_tool(toolset_name="EditorToolset.EditorAppToolset", tool_name="CaptureViewport")` returns an image.
- **Both servers side by side.** This works too, but it duplicates our tools under two names. Prefer one of the two options above.

## 9. Strategy

Epic now owns the plumbing: an in-editor MCP server, a tool framework and broad per-asset-type coverage that will keep
growing with each engine release. Competing on tool count or on the transport is a losing game. UnrealMCP should become
**the layer that makes agents finish real tasks and proves they did.** It should run on top of Epic's stack wherever that stack is good.

**Delegate to Epic.** Don't build these; reach them through the gateway (`epic_call_tool`) and the skills:
- Niagara module and stack authoring, Control Rig, Sequencer keyframing and the anim mixer, MetaHuman, physics assets, cloth, Dataflow, MVVM.
- Config and plugin management, Slate UI automation (SlateInspector), automation tests, semantic asset search.
- Mesh LOD, Nanite and socket setup, data, curve and string tables.

Where we have a thin tool that overlaps one of these (`niagara_edit`), keep it only as long as it helps. Point the skills at Epic's tool first.

**Keep owning.** Epic has nothing, or only granular tools, in these areas:
1. **Whole-artifact atomic authoring with rollback**: `bp_apply`, `animbp_apply`, `widget_apply`, `bt_build`, `material_edit`, `pcg_graph`, `scene_compose`.
   - One spec in, one undo step, compiled, structured errors, `dry_run`.
   - It is fewer round trips and fewer half-broken assets than 53 granular Blueprint calls. AnimBP state machines and BT/Blackboard authoring have no Epic equivalent at all.
2. **Verification**: `pie_test` with real input and timelines, `scene_assert`, `frame_stats`, `performance_audit`.
   - Epic can start PIE but can't drive or assert anything inside it.
   - This is the "it actually works" layer and the most defensible thing we have.
3. **Out-of-process work**: launch, quit and rebuild of editors per port, crash recovery, packaging (UAT), Blender modeling, the offline API index, remote editors with a token, chunked uploads.
   - An in-editor server can't do any of this by construction.
4. **World and game domains**: landscape sculpt and paint, foliage, splines, procedural architecture, Enhanced Input, game mode setup, GAS authoring.
5. **Measurement**: UE-Bench and per-domain pass rates. It is also the neutral way to compare "Epic tools only" vs "UnrealMCP" vs "both" with the same agent. Add that as a bench axis.
6. **Linux and dev ergonomics**: offscreen editors, multi-editor per port, the scripts.

**Add the most value on top.**
- **Both front doors, one implementation.** Keep growing `UnrealMCPToolsets`, so users who only enable Epic's server (and Epic's AI Assistant) still get our authoring and verification tools.
  - Next: port `pie_test` to a C++ async state machine, which gives the native door its biggest win.
  - Expose `viewport_capture` as a native `IModelContextProtocolTool` so it returns real image content.
  - Generate the manifest in CI.
- **A superset gateway** (`UNREAL_MCP_EPIC_URL`), so one client connection gets everything.
  - Teach the skills to mix both, e.g. author a Niagara system with Epic's tools, place it with `scene_compose`, verify it with `pie_test` and `frame_stats`.
  - Map Epic's base64 images to real image content. The gateway already does this.
- **Skills as the glue.** Our SKILL.md workflows are richer than Epic's 21 tool-skills. Also publish them as `UAgentSkill`s through `UnrealMCPToolsets`, so Epic's assistant learns our observe → act → verify loop.
- **Track the experimental API.**
  - Pin the toolset ABI to `FToolset` (data-driven) rather than UHT-generated `UToolsetDefinition`s, so engine changes touch one adapter file.
  - Re-run the capability census on every engine release. The counts in §3 came from a script-like source parse.

**Next months, in order:**
1. Land this prototype and the gateway, live-verified.
2. `pie_test` native.
3. Bench axis "Epic-only vs UnrealMCP vs both".
4. Skills that route Niagara, Control Rig and Sequencer work to Epic tools.
5. Publish our skills as `UAgentSkill`s.
6. Stop investing in domains Epic covers well. Concentrate on verification, world building, packaging and the out-of-editor pipeline (Blender, text/image→3D).
