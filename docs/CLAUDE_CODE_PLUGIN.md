# UnrealMCP agent pack for Claude Code

A Claude Code plugin. One install gives you:

- **the MCP server** (`unreal`), started by uv from the plugin's own copy of `Python/`, with nothing else to configure;
- **Epic's native MCP server** (`unreal-native`, `http://127.0.0.1:8000/mcp`) as an optional second server. It is used
  when the editor has the experimental ModelContextProtocol plugin enabled, and shows as failed in `/mcp` otherwise;
- **7 specialist subagents** ([`agents/`](../agents/)), the local counterparts to Aura's specialists:

| Subagent | Aura counterpart | Focus | Verifies with |
|---|---|---|---|
| `unrealmcp:unreal-blueprint` | Telos | Blueprints (`bp_export` / `bp_apply`), Blueprint review, UMG, Behavior Trees, Enhanced Input | compile result, `pie_test` with real input, log |
| `unrealmcp:unreal-python-batch` | Dragon | bulk edits through `execute_python`: preview, then one transaction, per-item report | re-query and compare counts, `scene_assert`, log |
| `unrealmcp:unreal-level-designer` | Level Design agent | `scene_compose`, landscape, foliage, PCG, splines, lighting, nav | `scene_assert`, `viewport_capture` critique, walk test in PIE |
| `unrealmcp:unreal-vfx` | VFX agent | Niagara placement and parameters, effect materials (`niagara_apply` is coming in a sibling branch) | capture while playing, `frame_stats` GPU delta |
| `unrealmcp:unreal-verifier` | Verification agent | independent pass/fail: `pie_test` with real input, vision critique, frame times, UE-Bench | evidence table; it doesn't fix things |
| `unrealmcp:unreal-tech-artist` | — | materials, textures, import, collision, Blender modeling | capture under level lighting, `performance_audit` |
| `unrealmcp:unreal-animator` | — | AnimBP state machines, montages, retargeting, GAS | PIE with input, asserting state and tags |

- **5 slash commands** ([`commands/`](../commands/)): `/unrealmcp:unreal-status`, `/unrealmcp:unreal-playtest`,
  `/unrealmcp:unreal-screenshot`, `/unrealmcp:unreal-package`, `/unrealmcp:unreal-bench`. Plugin commands are namespaced;
  Claude Code may also accept the bare `/unreal-status` when nothing else uses that name;
- **the workflow skills** ([`skills/`](../skills/)), loaded on demand.

The same subagent files are the specialists in the in-editor panel ([AGENT_PANEL.md](AGENT_PANEL.md)), and the commands
are also served as MCP prompts to every MCP client.

## Install

Prerequisites: the UnrealMCP editor plugin in your project (see the [README](../README.md)), and
[uv](https://docs.astral.sh/uv/) on your PATH.

```text
/plugin marketplace add shaundolan/unreal-engine-mcp
/plugin install unrealmcp@unrealmcp
```

or, from a shell: `claude plugin marketplace add shaundolan/unreal-engine-mcp && claude plugin install unrealmcp@unrealmcp`.
The repository root is both the marketplace and the plugin (`.claude-plugin/marketplace.json` and
`.claude-plugin/plugin.json`).

To work on the plugin itself from a clone, skip the install and run `claude --plugin-dir /path/to/unreal-engine-mcp`
(use `/reload-plugins` after editing).

Then open the editor and run `/unrealmcp:unreal-status`. `/mcp` should list `plugin:unrealmcp:unreal` as connected.

## Configure

The server reads these from the environment Claude Code runs in:

| Variable | Default | Meaning |
|---|---|---|
| `UNREAL_MCP_PORT` | `55557` | the editor plugin's port (must match the editor's `UNREAL_MCP_PORT`) |
| `UNREAL_MCP_HOST` | `127.0.0.1` | editor host, for a remote editor |
| `UNREAL_MCP_TOKEN` | empty | shared secret, if the editor requires one |
| `UNREAL_NATIVE_MCP_URL` | `http://127.0.0.1:8000/mcp` | Epic's native MCP server |

The Python environment is created in Claude Code's per-plugin data directory (`UV_PROJECT_ENVIRONMENT=${CLAUDE_PLUGIN_DATA}/venv`),
so it survives plugin updates. The first start takes a little longer while uv installs it.

## Targeting our server, Epic's, or both

- **Ours only** (the default when Epic's plugin is off): `unreal-native` fails to connect and nothing uses it.
- **Both:** enable *Model Context Protocol* in the editor (Edit → Plugins, Experimental). The subagents have both servers
  in their tool lists (`mcp__plugin_unrealmcp_unreal__<tool>` and `mcp__plugin_unrealmcp_unreal-native__*`) and are told
  to prefer UnrealMCP's batched, undoable tools when both can do a task.
- **Epic's only:** disable our server in `/mcp`, or run Epic's alone with
  `claude mcp add --transport http unreal-native http://127.0.0.1:8000/mcp`. The subagents' prompts mention UnrealMCP tools
  by name, so they work best with ours present.

## Tool names

Plugin MCP tools are named `mcp__plugin_unrealmcp_unreal__<tool>`. If you instead registered the server by hand as
`unreal` (`claude mcp add unreal ...`), its tools are `mcp__unreal__<tool>`, which don't match the subagents' `tools:`
lists. Install the plugin, or remove the hand-made entry, to use the subagents.
