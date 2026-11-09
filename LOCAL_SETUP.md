# Local MCP Server Setup

How to set up the local UnrealMCP server and editor plugin. For the full tool list, see the [README](README.md).

## Prerequisites

- **Unreal Engine 5.5+** with the **Python Editor Script Plugin** enabled (UnrealMCP depends on it)
- **Python 3.10–3.13** and [uv](https://docs.astral.sh/uv/)
- **MCP Client** (Claude Desktop, Cursor, or Windsurf)

## 1. Setup Options

**Option A: Use the Pre-Built Project (Recommended for Quick Start)**
```bash
git clone https://github.com/shaundolan/unreal-engine-mcp.git
cd unreal-engine-mcp

# Open FlopperamUnrealMCP/FlopperamUnrealMCP.uproject
# The plugin is already installed and enabled
```

**Option B: Add Plugin to Your Existing Project**
```bash
cp -r UnrealMCP/ YourProject/Plugins/

# Enable in Unreal Editor
# Edit → Plugins → Search "UnrealMCP" → Enable → Restart Editor
```

**Option C: Install for All Projects**
```bash
cp -r UnrealMCP/ "C:/Program Files/Epic Games/UE_5.5/Engine/Plugins/"

# Edit → Plugins → Search "UnrealMCP" → Enable
```

## 2. Launch the MCP Server

```bash
cd Python
uv run unreal_mcp_server_advanced.py
```

## 3. Configure Your AI Client

Add this to your MCP configuration:

**Cursor**: `.cursor/mcp.json`
**Claude Desktop**: `~/.config/claude-desktop/mcp.json`
**Windsurf**: `~/.config/windsurf/mcp.json`

```json
{
  "mcpServers": {
    "unrealMCP": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/unreal-engine-mcp/Python",
        "run",
        "unreal_mcp_server_advanced.py"
      ]
    }
  }
}
```

On Mac/Windows you may need the absolute path to `uv` (`which uv` or `where uv`).

**Claude Code**:
```bash
claude mcp add unreal -- uv --directory /path/to/unreal-engine-mcp/Python run unreal_mcp_server_advanced.py
```

## 4. Offline API index (recommended)

The editor writes a stub of the full `unreal` Python API to `<Project>/Intermediate/PythonStub/unreal.py` when
*Editor Preferences → Plugins → Python → Developer Mode* is on (restart the editor after turning it on). Then either
ask the agent to call `unreal_api_build_index`, or run:
```bash
cd Python && uv run python -m unreal_mcp.api_index build "/path/to/Project/Intermediate/PythonStub/unreal.py"
```
The index is saved to `~/.unreal_mcp/unreal_api.sqlite`. Agents use `unreal_api_search` to get exact names before writing Python.

## Configuration

| Variable | Side | Default | Meaning |
|---|---|---|---|
| `UNREAL_MCP_PORT` | both | `55557` | TCP port |
| `UNREAL_MCP_HOST` | server | `127.0.0.1` | Where the editor is |
| `UNREAL_MCP_BIND` | editor | `127.0.0.1` | Address the plugin listens on |
| `UNREAL_MCP_TOKEN` | both | (none) | Shared secret. **Required** when `UNREAL_MCP_BIND` isn't loopback |
| `UNREAL_MCP_PROTOCOL` | server | `framed` | `legacy` for editors still running the original (pre-0.2) plugin build |
| `UNREAL_MCP_LOG_DIR` | server | `~/.unreal_mcp/logs` | Server log directory (logs never go to stdout) |
| `UNREAL_MCP_BLENDER` | server | `blender` on PATH | Blender executable for `blender_run` (4.2+ LTS recommended) |
| `UNREAL_MCP_WORK_DIR` | server | `~/.unreal_mcp/work` | Blender scripts, exports and preview renders (kept per run for inspection) |

### Remote editor

To run the MCP server on one machine and the editor on another (e.g. agents on Linux, Unreal on a Windows PC),
set these in the editor machine's environment before launching Unreal:
```powershell
setx UNREAL_MCP_BIND 0.0.0.0
setx UNREAL_MCP_TOKEN <long-random-secret>
```
On the server side, set `UNREAL_MCP_HOST=<windows-ip>` and the same `UNREAL_MCP_TOKEN`. Prefer a private network
(Tailscale/WireGuard) over opening the port to the LAN. `execute_python` is remote code execution.

## 5. Start Building

```bash
> "Create a medieval castle with towers and walls"
> "Generate a town square with fountain and buildings"
> "Make a challenging maze for players to solve"
```

---

## macOS Setup

If you're on macOS and Unreal Engine fails to open the project due to compilation errors, you'll need to manually compile the C++ plugin first.

### Step 1: Check Your Xcode Version

```bash
xcodebuild -version
xcrun --show-sdk-version
```

Note your Xcode version number (e.g., `26.0.1`, `16.0`, `15.2`, etc.). If your version is newer than 16.0, you'll need to patch the Unreal Engine SDK configuration.

### Step 2: Patch Unreal Engine SDK Configuration

Edit the file at your Unreal Engine installation (replace `UE_5.X` with your version):

```bash
# Path to edit:
/Users/Shared/Epic Games/UE_5.X/Engine/Config/Apple/Apple_SDK.json
```

Update the following values:

**Change 1:** Update `MaxVersion` to support your Xcode version
```json
{
  "MaxVersion": "YOUR_XCODE_VERSION.9.0",  // e.g., "26.9.0" if you have Xcode 26.x
}
```
Replace `YOUR_XCODE_VERSION` with your major Xcode version from Step 1.

**Change 2:** Add LLVM version mapping for your Xcode version (add to the `AppleVersionToLLVMVersions` array)
```json
{
  "AppleVersionToLLVMVersions": [
    "14.0.0-14.0.0",
    "14.0.3-15.0.0",
    "15.0.0-16.0.0",
    "16.0.0-17.0.6",
    "16.3.0-19.1.4",
    "YOUR_XCODE_VERSION.0.0-19.1.4"  // e.g., "26.0.0-19.1.4" for Xcode 26.x
  ]
}
```
Replace `YOUR_XCODE_VERSION` with your major Xcode version from Step 1.

### Step 3: Compile the Plugin

Run the Unreal Build Tool to compile the project:

```bash
"/Users/Shared/Epic Games/UE_5.X/Engine/Build/BatchFiles/Mac/Build.sh" \
  UnrealEditor Mac Development \
  -Project="/path/to/unreal-engine-mcp/FlopperamUnrealMCP/FlopperamUnrealMCP.uproject" \
  -WaitMutex
```

Replace:
- `UE_5.X` with your Unreal Engine version (e.g., `UE_5.5`)
- `/path/to/unreal-engine-mcp/` with the actual path to your cloned repository

### Step 4: Open the Project

Once compilation succeeds, you can open `FlopperamUnrealMCP.uproject` in Unreal Engine.

---

## Troubleshooting

See the [Debugging & Troubleshooting Guide](DEBUGGING.md) for solutions to common problems like MCP installation errors and configuration issues.

For Blueprint graph programming with the local server, see the [Blueprint Graph Programming Guide](Guides/blueprint-graph-guide.md).
