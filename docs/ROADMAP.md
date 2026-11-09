# Roadmap

Goal: a local, nearly free agent platform for Unreal that can do any task a human does in the editor,
**measured** by a public benchmark rather than claimed.

| Phase | Status | Contents |
|---|---|---|
| 0: Foundations | ✅ code, ⏳ Windows compile | Framed protocol, multi-client, command registry, timeouts, token, tests against the fake plugin, docs for the local tools |
| 1: Universal layer | ✅ code, ⏳ Windows compile | `execute_python`, reflection (describe/get/set/call), editor log, viewport capture, project context, console, editor actions, offline API index |
| 2: Domain tools | 🚧 done: scene, assets, materials, level, Niagara placement, sequencer, DataTables. Enhanced Input, game setup, C++ source + Live Coding, animation (sequences, montages, BlendSpaces, IK retargeting, AnimBP state machines) and GAS (tags, effects, abilities, grants, generated AttributeSets). Next: Blueprint v2 (C++), UMG, AI/BT, landscape | Scene (`scene_query`, `scene_compose`), assets (`search_assets`, `asset_references`, `asset_factory`), Blueprint v2 (`bp_export` GraphSpec + `bp_author` diff apply), materials, Niagara, animation/AnimBP, UMG, AI/GAS, landscape/foliage, sequencer/MetaSound, PCG, C++ + Live Coding |
| 3: Verification & autonomy | 🚧 `pie_test`, static `performance_audit` done; next: PIE input injection, frame timings, UI automation, packaging | PIE test harness with assertions, performance audit (stat/Insights), Slate UI automation fallback, cook/package via UAT, source control |
| 4: Agent experience | planned | Per-domain skills, MCP resources (level summary, selection), optional in-editor chat panel, local text/image→3D import |
| 5: UE-Bench | planned | ~60 real-workflow tasks with automatic checkers; pass rate per domain published each release |

Most Phase 2 tools will be thin Python modules in `Python/unreal_mcp/tools/` that send `execute_python` scripts.
They don't need a C++ rebuild. C++ is only for things Python can't reach (graph pins, AnimGraph, parts of PCG, Slate).

Adding a tool group: create `Python/unreal_mcp/tools/<name>.py` with `register(mcp, get_conn)`. It's picked up automatically.
Add tests against `tests/fake_unreal.py`.
