"""Machine-safety guards for starting Unreal processes (editor launches, rebuilds, packaging).

Everything here is opt-in: with none of the variables below set, every check passes and nothing changes for
existing flows. scripts/ue_windows.ps1 and scripts/ue_linux.sh call the CLI (`python -m unreal_mcp.editor_guard`)
before `launch`; package_project checks resources when the editor is on this machine; editor_preflight reports it all.

UNREAL_MCP_EDITOR_LOCK       Machine-wide single-editor lock directory (default: unset = no lock). Same convention as
                             hand-rolled queues: the lock is a directory made with an atomic mkdir, holding `pid`
                             (holder process id) and `owner` (free text + date). This module also writes `pidkind`
                             ("win32" or "posix") so a reader knows which pid namespace `pid` is in.
UNREAL_MCP_LOCK_OWNER        Owner text written to the lock (default "unreal-mcp <command> port <port>")
UNREAL_MCP_LOCK_STALE_S      A lock whose holder pid is gone is moved aside (renamed <lock>.stale.<epoch>, never
                             deleted) once it is at least this old in seconds (default 60)
UNREAL_MCP_LOCK_HELD         "1": the caller already holds the lock through an outer queue; skip taking it
UNREAL_MCP_MIN_FREE_RAM_GB   Refuse to start when less physical RAM than this is free/available (default 0 = off)
UNREAL_MCP_MAX_LOAD          Refuse to start when the 1-minute load average is above this (default 0 = off;
                             POSIX only: on Windows there is no load average and the check is reported as skipped)
UNREAL_MCP_RESERVED_PORTS    Comma-separated ports automatic selection never picks, e.g. "55557" for a lead editor
                             (default: none)
UNREAL_MCP_PORT_RANGE        Range automatic selection scans, "first-last" (default 55558-55599)

UNREAL_MCP_PORT=auto (scripts only) picks the first port in the range that is not reserved and not in use.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PIDKIND = "win32" if os.name == "nt" else "posix"
DEFAULT_PORT_RANGE = (55558, 55599)
EXIT_REFUSED = 3


def _float(e: dict[str, str] | os._Environ, name: str, default: float) -> float:
    v = (e.get(name) or "").strip()
    try:
        return float(v) if v else default
    except ValueError as err:
        raise ValueError(f"{name} must be a number, got {v!r}") from err


def parse_ports(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in (text or "").replace(";", ",").split(",") if p.strip())


def parse_range(text: str) -> tuple[int, int]:
    if not (text or "").strip():
        return DEFAULT_PORT_RANGE
    a, _, b = text.partition("-")
    lo, hi = int(a), int(b or a)
    if not (0 < lo <= hi < 65536):
        raise ValueError(f"UNREAL_MCP_PORT_RANGE must be first-last within 1-65535, got {text!r}")
    return lo, hi


@dataclass(frozen=True)
class GuardConfig:
    lock_path: Path | None = None
    lock_owner: str = ""
    lock_stale_s: float = 60.0
    lock_held: bool = False
    min_free_ram_gb: float = 0.0
    max_load: float = 0.0
    reserved_ports: tuple[int, ...] = ()
    port_range: tuple[int, int] = field(default=DEFAULT_PORT_RANGE)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> GuardConfig:
        e = os.environ if env is None else env
        lock = (e.get("UNREAL_MCP_EDITOR_LOCK") or "").strip()
        return cls(
            lock_path=Path(os.path.expanduser(lock)) if lock else None,
            lock_owner=(e.get("UNREAL_MCP_LOCK_OWNER") or "").strip(),
            lock_stale_s=_float(e, "UNREAL_MCP_LOCK_STALE_S", 60.0),
            lock_held=(e.get("UNREAL_MCP_LOCK_HELD") or "").strip().lower() in ("1", "true", "yes"),
            min_free_ram_gb=_float(e, "UNREAL_MCP_MIN_FREE_RAM_GB", 0.0),
            max_load=_float(e, "UNREAL_MCP_MAX_LOAD", 0.0),
            reserved_ports=parse_ports(e.get("UNREAL_MCP_RESERVED_PORTS", "")),
            port_range=parse_range(e.get("UNREAL_MCP_PORT_RANGE", "")),
        )

    @property
    def any_enabled(self) -> bool:
        return bool(self.lock_path or self.min_free_ram_gb or self.max_load)


# ---------------------------------------------------------------- resources

def free_ram_gb() -> float | None:
    """Available physical RAM in GB, or None when it cannot be read."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullAvailPhys / 1024 ** 3
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 ** 2
    except (OSError, ValueError, AttributeError):
        return None
    return None


def load_average() -> float | None:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


def check_resources(cfg: GuardConfig, ram_gb=None, load=None) -> dict[str, Any]:
    """{"ok": bool, "reasons": [...], "free_ram_gb", "load_1m", "skipped": [...]} -- reasons say why it refused."""
    ram_gb, load = ram_gb or free_ram_gb, load or load_average
    out: dict[str, Any] = {"ok": True, "reasons": [], "skipped": []}
    if cfg.min_free_ram_gb > 0:
        free = ram_gb()
        out["free_ram_gb"] = None if free is None else round(free, 2)
        if free is None:
            out["skipped"].append("free RAM unreadable on this platform")
        elif free < cfg.min_free_ram_gb:
            out["reasons"].append(f"only {free:.1f} GB RAM free, need >= {cfg.min_free_ram_gb:g} GB "
                                  "(UNREAL_MCP_MIN_FREE_RAM_GB)")
    if cfg.max_load > 0:
        la = load()
        out["load_1m"] = None if la is None else round(la, 2)
        if la is None:
            out["skipped"].append("no load average on this platform (UNREAL_MCP_MAX_LOAD ignored)")
        elif la > cfg.max_load:
            out["reasons"].append(f"load average {la:.1f} is above {cfg.max_load:g} (UNREAL_MCP_MAX_LOAD)")
    out["ok"] = not out["reasons"]
    return out


# ---------------------------------------------------------------- ports

def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True when something listens on the port (a connect succeeds) or it cannot be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        if s.connect_ex((host, port)) == 0:
            return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name == "nt":  # never steal a port someone bound with SO_REUSEADDR
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


def pick_port(cfg: GuardConfig, in_use=None) -> int:
    in_use = in_use or port_in_use
    lo, hi = cfg.port_range
    for p in range(lo, hi + 1):
        if p in cfg.reserved_ports or in_use(p):
            continue
        return p
    raise RuntimeError(f"no free port in {lo}-{hi} (reserved: {list(cfg.reserved_ports) or 'none'})")


# ---------------------------------------------------------------- lock

def _bash_pid_alive(pid: int) -> bool | None:
    """A lock made by a Git Bash/MSYS queue stores an MSYS pid that Windows APIs cannot see; ask bash."""
    bash = shutil.which("bash")
    if not bash:
        return None
    try:
        r = subprocess.run([bash, "-c", f"kill -0 {int(pid)}"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


def pid_alive(pid: int, kind: str = PIDKIND) -> bool | None:
    """True/False, or None when it cannot be determined (callers then treat the holder as alive)."""
    if pid <= 0:
        return False
    if kind != PIDKIND:
        if os.name == "nt" and kind in ("posix", ""):
            return _bash_pid_alive(pid)
        return None
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.GetLastError() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return None
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def read_lock(path: Path) -> dict[str, Any] | None:
    if not path.is_dir():
        return None
    def _txt(name: str) -> str:
        try:
            return (path / name).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
    pid_txt = _txt("pid")
    # No pidkind file: written by a shell queue. On Windows that is Git Bash (MSYS pids).
    kind = _txt("pidkind") or ("posix" if os.name == "nt" else PIDKIND)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = 0.0
    return {"path": str(path), "pid": int(pid_txt) if pid_txt.isdigit() else None, "pidkind": kind,
            "owner": _txt("owner"), "age_s": round(age, 1)}


def _write_holder(path: Path, pid: int, owner: str) -> None:
    (path / "pid").write_text(f"{pid}\n", encoding="utf-8")
    (path / "pidkind").write_text(f"{PIDKIND}\n", encoding="utf-8")
    (path / "owner").write_text(f"{owner} {time.strftime('%a %b %d %H:%M:%S %Y')}\n", encoding="utf-8")


def acquire_lock(cfg: GuardConfig, owner: str = "", pid: int | None = None,
                 alive=None, now=time.time) -> dict[str, Any]:
    """Take the lock once (no waiting: queues/schedulers wait, this refuses with the holder named)."""
    alive = alive or pid_alive
    if not cfg.lock_path:
        return {"ok": True, "lock": "off"}
    if cfg.lock_held:
        return {"ok": True, "lock": "held_by_caller", "path": str(cfg.lock_path)}
    path, pid = cfg.lock_path, pid or os.getpid()
    owner = owner or cfg.lock_owner or "unreal-mcp"
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            path.mkdir()
        except FileExistsError:
            info = read_lock(path) or {}
            hp = info.get("pid")
            if hp == pid and info.get("pidkind") == PIDKIND:
                return {"ok": True, "lock": "already_ours", "path": str(path)}
            state = alive(hp, info.get("pidkind", PIDKIND)) if hp else None
            if hp and state is False and info.get("age_s", 0) >= cfg.lock_stale_s:
                aside = path.with_name(f"{path.name}.stale.{int(now())}")
                try:
                    path.rename(aside)
                except OSError:
                    pass
                else:
                    continue
            why = "holder pid is gone but the lock is younger than UNREAL_MCP_LOCK_STALE_S" if state is False else \
                "holder is alive" if state else "holder liveness unknown, assuming alive"
            return {"ok": False, "lock": "busy", "path": str(path), "holder": info,
                    "reason": f"editor lock {path} is held by pid {hp} ({info.get('owner') or 'no owner'}); {why}"}
        _write_holder(path, pid, owner)
        return {"ok": True, "lock": "acquired", "path": str(path), "pid": pid}
    return {"ok": False, "lock": "busy", "path": str(path), "reason": f"could not take {path}"}


def set_lock_pid(cfg: GuardConfig, old_pid: int, new_pid: int, owner: str = "") -> dict[str, Any]:
    """Hand the lock from the launcher process to the editor it started (so stale detection follows the editor)."""
    if not cfg.lock_path or cfg.lock_held:
        return {"ok": True, "lock": "off" if not cfg.lock_path else "held_by_caller"}
    info = read_lock(cfg.lock_path)
    if not info or info.get("pid") != old_pid:
        return {"ok": False, "reason": f"lock {cfg.lock_path} is not held by pid {old_pid}", "holder": info}
    _write_holder(cfg.lock_path, new_pid, owner or info.get("owner") or cfg.lock_owner or "unreal-mcp")
    return {"ok": True, "lock": "handed_over", "pid": new_pid}


def release_lock(cfg: GuardConfig, pid: int | None = None, alive=None) -> dict[str, Any]:
    """Remove the lock only when `pid` (default: this process) holds it, or its holder is gone."""
    alive = alive or pid_alive
    if not cfg.lock_path or cfg.lock_held:
        return {"ok": True, "lock": "off" if not cfg.lock_path else "held_by_caller"}
    info = read_lock(cfg.lock_path)
    if not info:
        return {"ok": True, "lock": "not_held"}
    pid = pid or os.getpid()
    mine = info.get("pid") == pid and info.get("pidkind") == PIDKIND
    if not mine and not (info.get("pidkind") == PIDKIND and info.get("pid") and alive(info["pid"]) is False):
        return {"ok": False, "lock": "busy", "holder": info,
                "reason": f"lock {cfg.lock_path} belongs to pid {info.get('pid')}, not {pid}; left in place"}
    shutil.rmtree(cfg.lock_path, ignore_errors=True)
    return {"ok": True, "lock": "released"}


# ---------------------------------------------------------------- combined

def status(cfg: GuardConfig | None = None, ram_gb=None, load=None, in_use=None) -> dict[str, Any]:
    cfg = cfg or GuardConfig.from_env()
    free, la = (ram_gb or free_ram_gb)(), (load or load_average)()
    out: dict[str, Any] = {
        "guards_enabled": cfg.any_enabled,
        "free_ram_gb": None if free is None else round(free, 2), "load_1m": None if la is None else round(la, 2),
        "min_free_ram_gb": cfg.min_free_ram_gb or None, "max_load": cfg.max_load or None,
        "resources": check_resources(cfg, lambda: free, lambda: la),
        "lock": {"path": str(cfg.lock_path), "holder": read_lock(cfg.lock_path)} if cfg.lock_path else "off",
        "reserved_ports": list(cfg.reserved_ports), "port_range": list(cfg.port_range),
    }
    try:
        out["suggested_port"] = pick_port(cfg, in_use)
    except RuntimeError as e:
        out["suggested_port"] = None
        out["port_error"] = str(e)
    return out


def preflight(cfg: GuardConfig, owner: str = "", pid: int | None = None, ram_gb=None,
              load=None, alive=None) -> dict[str, Any]:
    """Resources first (refusing costs nothing), then the lock."""
    res = check_resources(cfg, ram_gb, load)
    if not res["ok"]:
        return {"ok": False, "reason": "; ".join(res["reasons"]), "resources": res}
    lk = acquire_lock(cfg, owner, pid, alive)
    return {"ok": lk["ok"], **({"reason": lk["reason"]} if not lk["ok"] else {}), "resources": res, "lock": lk}


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m unreal_mcp.editor_guard",
                                 description="Opt-in machine guards for Unreal launches (see module docstring).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("preflight", help="check RAM/load, then take the lock; exit 3 with the reason if refused")
    pf.add_argument("--owner", default="")
    pf.add_argument("--pid", type=int, required=True,
                    help="holder pid to record: the launching shell ($$ / $PID), not this Python process")
    sub.add_parser("check-resources", help="RAM/load only; exit 3 if refused")
    pp = sub.add_parser("pick-port", help="print a free, non-reserved port from UNREAL_MCP_PORT_RANGE")
    pp.add_argument("--prefer", type=int, default=0, help="return this port if it is free and not reserved")
    hp = sub.add_parser("lock-handover", help="re-point the lock from --from pid to --to pid (the editor)")
    hp.add_argument("--from", dest="old", type=int, required=True)
    hp.add_argument("--to", dest="new", type=int, required=True)
    rl = sub.add_parser("lock-release", help="release the lock held by --pid (or whose holder is gone)")
    rl.add_argument("--pid", type=int, required=True)
    sub.add_parser("status", help="JSON: RAM, load, lock holder, suggested port")
    a = ap.parse_args(argv)
    try:
        cfg = GuardConfig.from_env()
    except ValueError as e:
        print(f"editor_guard: {e}", file=sys.stderr)
        return 2
    if a.cmd == "pick-port":
        if a.prefer and a.prefer not in cfg.reserved_ports and not port_in_use(a.prefer):
            print(a.prefer)
            return 0
        try:
            print(pick_port(cfg))
        except RuntimeError as e:
            print(f"editor_guard: {e}", file=sys.stderr)
            return EXIT_REFUSED
        return 0
    if a.cmd == "status":
        print(json.dumps(status(cfg), indent=1, default=str))
        return 0
    if a.cmd == "check-resources":
        r = check_resources(cfg)
    elif a.cmd == "preflight":
        r = preflight(cfg, a.owner, a.pid)
    elif a.cmd == "lock-handover":
        r = set_lock_pid(cfg, a.old, a.new)
    else:
        r = release_lock(cfg, a.pid)
    if not r.get("ok"):
        print(f"editor_guard: REFUSED: {r.get('reason') or '; '.join(r.get('reasons', []))}", file=sys.stderr)
        return EXIT_REFUSED
    for note in r.get("skipped", []) + r.get("resources", {}).get("skipped", []):
        print(f"editor_guard: note: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
