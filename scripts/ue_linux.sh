#!/usr/bin/env bash
# Linux plugin dev loop for the sample project.
#   scripts/ue_linux.sh build      sync plugin copy + compile (editor must be closed)
#   scripts/ue_linux.sh launch     start the editor offscreen (no window) and wait until the MCP plugin answers ping
#   scripts/ue_linux.sh launch-gui same, with a visible editor window
# Offscreen is the default for automation: in UE 5.8.4 Linux/Vulkan a stale window swapchain
# ("AcquireNextImage() failed due to the outdated swapchain") can deadlock the RHI/render/game threads.
#   scripts/ue_linux.sh quit       close the editor over MCP (falls back to SIGTERM)
#   scripts/ue_linux.sh rebuild    quit + build + launch
# UE_PROJECT selects another .uproject (default: the sample project). UE_ROOT defaults to ~/Downloads/Linux_Unreal_Engine_5.8.4; logs go to $UE_LOG_DIR (default /tmp/unreal-mcp).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
UE_ROOT="${UE_ROOT:-$HOME/Downloads/Linux_Unreal_Engine_5.8.4}"
PROJECT="${UE_PROJECT:-$REPO/FlopperamUnrealMCP/FlopperamUnrealMCP.uproject}"
LOG_DIR="${UE_LOG_DIR:-/tmp/unreal-mcp}"
mkdir -p "$LOG_DIR"

ping_editor() {
  (cd "$REPO/Python" && timeout 12 uv run --quiet python -c "
from unreal_mcp.connection import UnrealConnection; from unreal_mcp.config import Settings
c = UnrealConnection(Settings()); c.MAX_CONNECT_ATTEMPTS = 1
r = c.send_command('ping', {}, timeout=8); raise SystemExit(0 if r.get('status') == 'success' else 1)" 2>/dev/null)
}

do_quit() {
  if ! pgrep -x UnrealEditor >/dev/null; then echo "editor not running"; return 0; fi
  (cd "$REPO/Python" && timeout 20 uv run --quiet python -c "
from unreal_mcp.connection import UnrealConnection; from unreal_mcp.config import Settings
UnrealConnection(Settings()).send_command('execute_python', {'code': 'import unreal; unreal.SystemLibrary.quit_editor()'}, timeout=15)" || true)
  for _ in $(seq 1 45); do pgrep -x UnrealEditor >/dev/null || { echo "editor closed"; return 0; }; sleep 2; done
  echo "editor did not quit; sending SIGTERM"; pkill -TERM -x UnrealEditor || true; sleep 5
}

do_build() {
  bash "$REPO/scripts/sync_plugin.sh" >/dev/null
  local log="$LOG_DIR/build.log"
  if "$UE_ROOT/Engine/Build/BatchFiles/Linux/Build.sh" "$(basename "$PROJECT" .uproject)Editor" Linux Development \
      -Project="$PROJECT" -WaitMutex >"$log" 2>&1; then
    echo "build ok ($(grep -c 'warning:' "$log" || true) warnings) -> $log"
  else
    echo "BUILD FAILED -> $log"; grep -E ": error|fatal error" "$log" | sed 's|.*/Source/UnrealMCP/||' | sort -u | head -40; return 1
  fi
}

do_launch() {
  local mode=("-RenderOffscreen")
  [ "${1:-}" = "gui" ] && mode=()
  if pgrep -x UnrealEditor >/dev/null; then echo "editor already running"; return 0; fi
  setsid "$UE_ROOT/Engine/Binaries/Linux/UnrealEditor" "$PROJECT" -nosplash "${mode[@]}" -abslog="$LOG_DIR/editor.log" \
    "-ini:Engine:[/Script/PythonScriptPlugin.PythonScriptPluginSettings]:bDeveloperMode=True" >/dev/null 2>&1 &
  for i in $(seq 1 180); do
    if ping_editor; then sleep 10; echo "editor ready after ~$((i * 5))s"; return 0; fi
    pgrep -x UnrealEditor >/dev/null || { echo "EDITOR EXITED"; tail -20 "$LOG_DIR/editor.log"; return 1; }
    sleep 5
  done
  echo "editor did not answer within 15 min"; return 1
}

case "${1:-}" in
  build) do_build ;;
  launch) do_launch ;;
  launch-gui) do_launch gui ;;
  quit) do_quit ;;
  rebuild) do_quit && do_build && do_launch ;;
  *) echo "usage: $0 build|launch|launch-gui|quit|rebuild"; exit 2 ;;
esac
