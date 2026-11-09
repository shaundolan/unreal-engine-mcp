#!/usr/bin/env bash
# Linux plugin dev loop for the sample project (Windows: scripts/ue_windows.ps1).
#   scripts/ue_linux.sh build      sync plugin copy + compile (this project's editor must be closed)
#   scripts/ue_linux.sh launch     start the editor offscreen (no window, -unattended) and wait until the MCP plugin answers ping
#   scripts/ue_linux.sh launch-gui same, with a visible editor window
# Offscreen is the default for automation: in UE 5.8.4 Linux/Vulkan a stale window swapchain
# ("AcquireNextImage() failed due to the outdated swapchain") can deadlock the RHI/render/game threads.
# Offscreen launches also pass -unattended: nobody can answer a modal dialog there, and on a crash the
# CrashReportClient window would otherwise pop up on the desktop. UE_EXTRA_ARGS (e.g. "-nosound") are appended.
#   scripts/ue_linux.sh quit       close the editor over MCP (falls back to SIGTERM of the editor this script started)
#   scripts/ue_linux.sh kill       SIGTERM, then SIGKILL, the editor this script started on this port (a hung editor:
#                                  MCP commands fail with editor_unresponsive; see docs/LINUX_UE.md)
#   scripts/ue_linux.sh rebuild    quit + build + launch
#   scripts/ue_linux.sh close-crash-reporter   close a CrashReportClient left by a crashed editor of this project
# UE_PROJECT selects another .uproject (default: the sample project). UE_ROOT defaults to ~/Downloads/Linux_Unreal_Engine_5.8.4;
# Logs (build.log, editor.log) and the editor pid file go to $UE_LOG_DIR (default /tmp/unreal-mcp/<port>).
# UNREAL_MCP_PORT / UNREAL_MCP_TOKEN pick the editor: the launched editor listens there, and ping/quit only talk to it.
# Several editors (one per port) can share a machine: quit only ever signals the PID this script started on that port,
# never every UnrealEditor process, and launch refuses if the port answers or the project is already open elsewhere.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
UE_ROOT="${UE_ROOT:-$HOME/Downloads/Linux_Unreal_Engine_5.8.4}"
PROJECT="${UE_PROJECT:-$REPO/FlopperamUnrealMCP/FlopperamUnrealMCP.uproject}"
PORT="${UNREAL_MCP_PORT:-55557}"
export UNREAL_MCP_PORT="$PORT"
LOG_DIR="${UE_LOG_DIR:-/tmp/unreal-mcp/$PORT}"
PID_FILE="$LOG_DIR/editor.pid"
EDITOR_LOG="$LOG_DIR/editor.log"
mkdir -p "$LOG_DIR"

# PIDs of editors that already have $PROJECT open (any port).
project_editors() {
  local p
  for p in $(pgrep -x UnrealEditor 2>/dev/null || true); do
    if tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | grep -qF -- "$PROJECT"; then echo "$p"; fi
  done
}

# UnrealConnection() reads UNREAL_MCP_HOST/PORT/TOKEN from the environment.
ping_editor() {
  (cd "$REPO/Python" && timeout 12 uv run --quiet python -c "
from unreal_mcp.connection import UnrealConnection
c = UnrealConnection(); c.MAX_CONNECT_ATTEMPTS = 1
r = c.send_command('ping', {}, timeout=8); raise SystemExit(0 if r.get('status') == 'success' else 1)" 2>/dev/null)
}

our_pid() {
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  # alive AND still an UnrealEditor (a stale pid file must never make us signal a recycled pid)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q UnrealEditor; then
    echo "$pid"
  fi
}

# A crashed editor of THIS project starts CrashReportClient (a desktop window unless -unattended). Close only the
# ones whose command line points at this project's Saved/Crashes, never anyone else's.
close_crash_reporter() {
  local p crash_dir
  crash_dir="$(dirname "$PROJECT")/Saved/Crashes"
  for p in $(pgrep -f CrashReportClient 2>/dev/null || true); do
    if tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | grep -qF -- "$crash_dir"; then
      echo "closing CrashReportClient $p"; kill -TERM "$p" 2>/dev/null || true
    fi
  done
}

do_quit() {
  local pid
  pid="$(our_pid)"
  if ! ping_editor; then
    if [ -z "$pid" ]; then echo "no editor on port $PORT"; return 0; fi
  else
    (cd "$REPO/Python" && timeout 20 uv run --quiet python -c "
from unreal_mcp.connection import UnrealConnection
UnrealConnection().send_command('execute_python', {'code': 'import unreal; unreal.SystemLibrary.quit_editor()'}, timeout=15)" || true)
  fi
  if [ -z "$pid" ]; then
    # Not started by this script: we can only wait for the port to go quiet.
    for _ in $(seq 1 45); do ping_editor || { echo "editor closed"; return 0; }; sleep 2; done
    echo "editor on port $PORT did not quit (not started by this script, so not killing it)"; return 1
  fi
  for _ in $(seq 1 45); do kill -0 "$pid" 2>/dev/null || { echo "editor closed"; rm -f "$PID_FILE"; return 0; }; sleep 2; done
  echo "editor did not quit; sending SIGTERM to $pid"; kill -TERM "$pid" 2>/dev/null || true; sleep 5; rm -f "$PID_FILE"
  close_crash_reporter
}

do_build() {
  local open
  open="$(project_editors)"
  if [ -n "$open" ]; then echo "the project is open in editor pid(s) $(echo $open) - quit it first"; return 1; fi
  bash "$REPO/scripts/sync_plugin.sh" >/dev/null
  # -NoHotReloadFromIDE: with ANY other editor running from the same engine, UBT otherwise builds a hot-reload
  # libUnrealEditor-UnrealMCP-NNNN.so that UnrealEditor.modules never points to, so the next launch loads stale code.
  local log="$LOG_DIR/build.log"
  if "$UE_ROOT/Engine/Build/BatchFiles/Linux/Build.sh" "$(basename "$PROJECT" .uproject)Editor" Linux Development \
      -Project="$PROJECT" -WaitMutex -NoHotReloadFromIDE >"$log" 2>&1; then
    echo "build ok ($(grep -c 'warning:' "$log" || true) warnings) -> $log"
  else
    echo "BUILD FAILED -> $log"; grep -E ": error|fatal error" "$log" | sed 's|.*/Source/UnrealMCP/||' | sort -u | head -40; return 1
  fi
}

do_launch() {
  local mode=("-RenderOffscreen" "-unattended")
  [ "${1:-}" = "gui" ] && mode=()
  # shellcheck disable=SC2206
  local extra=(${UE_EXTRA_ARGS:-})
  if ping_editor; then echo "an editor already answers on port $PORT"; return 0; fi
  if [ -n "$(our_pid)" ]; then echo "editor $(our_pid) is still starting or hung; run quit first"; return 1; fi
  local open
  open="$(project_editors)"
  if [ -n "$open" ]; then echo "the project is already open in editor pid(s) $(echo $open) on another port"; return 1; fi
  UNREAL_MCP_PORT="$PORT" setsid "$UE_ROOT/Engine/Binaries/Linux/UnrealEditor" "$PROJECT" -nosplash "${mode[@]}" "${extra[@]}" \
    -abslog="$EDITOR_LOG" \
    "-ini:Engine:[/Script/PythonScriptPlugin.PythonScriptPluginSettings]:bDeveloperMode=True" >/dev/null 2>&1 &
  local pid=$!
  echo "$pid" >"$PID_FILE"
  for i in $(seq 1 180); do
    if ping_editor; then sleep 10; echo "editor $pid ready on port $PORT after ~$((i * 5))s"; return 0; fi
    kill -0 "$pid" 2>/dev/null || { echo "EDITOR EXITED"; tail -20 "$EDITOR_LOG"; rm -f "$PID_FILE"; close_crash_reporter; return 1; }
    sleep 5
  done
  echo "editor did not answer within 15 min"; return 1
}

# A hung editor (e.g. the 5.8.4 Vulkan RHI-thread spin: 100% CPU, game thread frozen, MCP commands fail with
# editor_unresponsive) does not answer quit and may ignore SIGTERM. SIGKILL only the PID this script started on this
# port, never other editors.
do_kill() {
  local pid
  pid="$(our_pid)"
  if [ -z "$pid" ]; then
    echo "no editor started by this script on port $PORT (pid file: $PID_FILE)"
    echo "if editor_health named a pid, check it is yours (ps -o pid,args -p <pid>) and run: kill -9 <pid>"
    return 1
  fi
  echo "killing editor $pid (port $PORT)"
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid" 2>/dev/null || true; sleep 2; fi
  if kill -0 "$pid" 2>/dev/null; then echo "editor $pid survived SIGKILL"; return 1; fi
  rm -f "$PID_FILE"
  close_crash_reporter
  echo "editor killed; relaunch with: UNREAL_MCP_PORT=$PORT $0 launch"
}

case "${1:-}" in
  build) do_build ;;
  launch) do_launch ;;
  launch-gui) do_launch gui ;;
  quit) do_quit ;;
  kill) do_kill ;;
  close-crash-reporter) close_crash_reporter ;;
  rebuild) do_quit && do_build && do_launch ;;
  *) echo "usage: $0 build|launch|launch-gui|quit|kill|rebuild|close-crash-reporter"; exit 2 ;;
esac
