# Editor launch guards

Some machines cannot take more than one Unreal process at a time (RAM, GPU, or a desktop that blue-screens under
load). The launch scripts and the MCP server can refuse to start Unreal when the machine is busy. **Every guard is
off by default.** With none of the variables below set, `scripts/ue_windows.ps1`, `scripts/ue_linux.sh` and
`package_project` work exactly as before, and the scripts do not call Python for guards.

The logic lives in `Python/unreal_mcp/editor_guard.py`. Its CLI is `python -m unreal_mcp.editor_guard`
(or `uv run unreal-mcp-guard`). Once a guard variable is set, the scripts run it through `uv run`, so `uv sync` must
have been run in `Python/` first.

## Variables

| Variable | Default | Effect |
|---|---|---|
| `UNREAL_MCP_EDITOR_LOCK` | unset (no lock) | Lock directory for one editor per machine, e.g. `~/.ue_lock`. `build` and `launch` refuse while a live process holds it. |
| `UNREAL_MCP_LOCK_OWNER` | `ue_linux.sh launch <project> port <port>` | Free text written to `<lock>/owner`. |
| `UNREAL_MCP_LOCK_STALE_S` | `60` | A lock whose holder pid has exited is moved aside once it is at least this many seconds old. |
| `UNREAL_MCP_LOCK_HELD` | unset | Set to `1` when an outer queue (for example `~/ue_queue.sh take`) already holds the lock, so the script does not try to take it again. |
| `UNREAL_MCP_MIN_FREE_RAM_GB` | `0` (off) | Refuse when less physical RAM than this is available (Linux `MemAvailable`, Windows `ullAvailPhys`). |
| `UNREAL_MCP_MAX_LOAD` | `0` (off) | Refuse when the 1-minute load average is above this. POSIX only: Windows has no load average, so the check is reported as skipped. |
| `UNREAL_MCP_RESERVED_PORTS` | none | Comma-separated ports that automatic selection never picks, e.g. `55557` for the lead's editor. |
| `UNREAL_MCP_PORT_RANGE` | `55558-55599` | The range automatic selection scans. |
| `UNREAL_MCP_PORT=auto` | | Scripts only, for `launch`, `launch-gui` and `rebuild`: the script picks the first port in the range that is neither reserved nor in use, and prints it. Run `quit` and `kill` with that number. |

When the scripts refuse, they exit with code 3 and print the reason, for example:
`editor_guard: REFUSED: only 5.2 GB RAM free, need >= 8 GB (UNREAL_MCP_MIN_FREE_RAM_GB)`.

## Lock format

The lock uses the same layout as the hand-rolled queues (`.ue_editor_lock`, `~/.ue_lock`):

- The lock is a directory created with an atomic `mkdir`.
- `pid` holds the holder's process id.
- `owner` holds free text plus a date.
- This module also writes `pidkind` (`win32` or `posix`), so readers know which pid namespace `pid` belongs to.

How the scripts use it:

- `launch` takes the lock with the script's own pid. Once the editor has started, it hands the lock over to the
  editor's pid (`lock-handover`), so the lock stays held exactly as long as the editor runs.
- `quit` and `kill` release the lock. A failed launch releases it too.
- `build` holds the lock for the length of the compile.

The guard never waits. Queueing and waiting remain the job of a scheduler. It also never deletes a lock someone else
holds. A lock counts as stale only when its holder pid has exited **and** the lock is older than
`UNREAL_MCP_LOCK_STALE_S`. A stale lock is renamed to `<lock>.stale.<epoch>`, the same convention as the existing
queue. If the guard cannot tell whether the holder is alive, it assumes the holder is alive.

### Windows and Git Bash queues

A lock made by a Git Bash queue has no `pidkind` file and stores an MSYS pid, which Windows APIs cannot see. On
Windows the guard checks such a pid with `bash -c "kill -0 <pid>"`. If bash is not on PATH, it treats the holder as
alive.

The reverse does not work: a Git Bash `kill -0` cannot see a Windows pid. A bash queue that checks this module's lock
would therefore see the holder as dead and could move the lock aside after 60 s. On the PC, choose one of these:

- Run the scripts inside the queue (`pc_ue_queue.sh take ...`) with `UNREAL_MCP_LOCK_HELD=1`, so the queue owns the
  lock.
- Give this module a lock path that the bash queue does not manage.

## Typical setups

```sh
# ripper01: wait-free refusal on top of the existing rules (load < 48, >= 8 GB RAM), free port, never 55557
UNREAL_MCP_EDITOR_LOCK=~/.ue_lock UNREAL_MCP_MIN_FREE_RAM_GB=8 UNREAL_MCP_MAX_LOAD=48 \
UNREAL_MCP_RESERVED_PORTS=55557 UNREAL_MCP_PORT=auto UE_PROJECT=/path/to/Game.uproject \
  scripts/ue_linux.sh launch

# the same, already inside ~/ue_queue.sh take: the queue holds the lock
UNREAL_MCP_LOCK_HELD=1 UNREAL_MCP_MIN_FREE_RAM_GB=8 UNREAL_MCP_MAX_LOAD=48 scripts/ue_linux.sh launch
```

```powershell
# Windows PC: one editor, at least 12 GB free
$env:UNREAL_MCP_LOCK_HELD = "1"   # when started through the PC queue; otherwise set UNREAL_MCP_EDITOR_LOCK
$env:UNREAL_MCP_MIN_FREE_RAM_GB = "12"
scripts\ue_windows.ps1 launch
```

## In the MCP server

- `editor_preflight` (read-only) reports free RAM, load, the lock holder, whether a guarded launch would be refused
  and why, and a suggested free port. Call it before running the launch scripts.
- `package_project` refuses to start UAT when `UNREAL_MCP_MIN_FREE_RAM_GB` or `UNREAL_MCP_MAX_LOAD` is exceeded. It
  checks only when the editor runs on this machine (`UNREAL_MCP_HOST` is loopback), because UAT runs on the editor's
  machine. It does not take the lock, since the editor running it already counts as the one Unreal process.

## CLI

```
python -m unreal_mcp.editor_guard status                          # JSON report
python -m unreal_mcp.editor_guard check-resources                 # exit 3 + reason if refused
python -m unreal_mcp.editor_guard pick-port [--prefer 55558]
python -m unreal_mcp.editor_guard preflight --pid $$ --owner "my job"   # resources, then the lock
python -m unreal_mcp.editor_guard lock-handover --from $$ --to <editor pid>
python -m unreal_mcp.editor_guard lock-release --pid <holder pid>
```

Always pass the pid of the long-lived shell or editor to `--pid`. If you pass the pid of the short-lived Python or
`uv` process, the lock goes stale as soon as that process exits.
