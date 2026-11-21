"""Opt-in editor launch guards: lock dir (pc_ue_queue/ue_lock convention), RAM/load floors, port selection."""

import os
import socket
import time

import pytest

from unreal_mcp import editor_guard as g


def cfg(tmp_path, **kw):
    return g.GuardConfig(lock_path=tmp_path / ".ue_editor_lock", **kw)


def test_defaults_are_off():
    c = g.GuardConfig.from_env({})
    assert not c.any_enabled and c.lock_path is None and c.reserved_ports == ()
    assert g.check_resources(c, ram_gb=lambda: 0.1, load=lambda: 99.0)["ok"]
    assert g.acquire_lock(c) == {"ok": True, "lock": "off"}
    assert g.preflight(c, pid=123)["ok"]


def test_from_env_parses_everything(tmp_path):
    c = g.GuardConfig.from_env({"UNREAL_MCP_EDITOR_LOCK": str(tmp_path / "L"), "UNREAL_MCP_MIN_FREE_RAM_GB": "8",
                                "UNREAL_MCP_MAX_LOAD": "48", "UNREAL_MCP_RESERVED_PORTS": "55557, 55560",
                                "UNREAL_MCP_PORT_RANGE": "55557-55570", "UNREAL_MCP_LOCK_HELD": "1"})
    assert c.any_enabled and c.lock_path == tmp_path / "L" and c.lock_held
    assert c.min_free_ram_gb == 8 and c.max_load == 48
    assert c.reserved_ports == (55557, 55560) and c.port_range == (55557, 55570)
    with pytest.raises(ValueError, match="UNREAL_MCP_MIN_FREE_RAM_GB"):
        g.GuardConfig.from_env({"UNREAL_MCP_MIN_FREE_RAM_GB": "lots"})
    with pytest.raises(ValueError, match="PORT_RANGE"):
        g.GuardConfig.from_env({"UNREAL_MCP_PORT_RANGE": "9-3"})


def test_resources_refuse_with_reasons():
    c = g.GuardConfig(min_free_ram_gb=8, max_load=48)
    r = g.check_resources(c, ram_gb=lambda: 5.5, load=lambda: 60.0)
    assert not r["ok"] and len(r["reasons"]) == 2
    assert "5.5 GB" in r["reasons"][0] and "60.0" in r["reasons"][1]
    assert g.check_resources(c, ram_gb=lambda: 32.0, load=lambda: 3.0)["ok"]
    # unreadable metrics never block, they are reported
    r = g.check_resources(c, ram_gb=lambda: None, load=lambda: None)
    assert r["ok"] and len(r["skipped"]) == 2
    p = g.preflight(c, pid=1, ram_gb=lambda: 1.0, load=lambda: 1.0)
    assert not p["ok"] and "RAM" in p["reason"]


def test_real_metrics_are_numbers_or_none():
    ram = g.free_ram_gb()
    assert ram is None or ram > 0
    la = g.load_average()
    assert la is None or la >= 0


def test_lock_acquire_writes_convention_files_and_refuses_second(tmp_path):
    c = cfg(tmp_path)
    r = g.acquire_lock(c, "agent A port 55558", pid=4242, alive=lambda p, k=None: True)
    assert r["ok"] and r["lock"] == "acquired"
    d = tmp_path / ".ue_editor_lock"
    assert d.is_dir() and (d / "pid").read_text().strip() == "4242"
    assert (d / "owner").read_text().startswith("agent A port 55558")
    assert (d / "pidkind").read_text().strip() == g.PIDKIND
    busy = g.acquire_lock(c, "agent B", pid=999, alive=lambda p, k=None: True)
    assert not busy["ok"] and "4242" in busy["reason"] and "agent A" in busy["reason"]
    assert g.acquire_lock(c, pid=4242)["lock"] == "already_ours"


def test_stale_lock_moved_aside_not_deleted(tmp_path):
    c = cfg(tmp_path, lock_stale_s=60)
    d = tmp_path / ".ue_editor_lock"
    d.mkdir()
    (d / "pid").write_text("777\n")
    (d / "owner").write_text("someone gone\n")
    old = time.time() - 300
    os.utime(d, (old, old))
    r = g.acquire_lock(c, "me", pid=5, alive=lambda p, k=None: False)
    assert r["ok"] and r["lock"] == "acquired"
    aside = [p for p in tmp_path.iterdir() if p.name.startswith(".ue_editor_lock.stale.")]
    assert len(aside) == 1 and (aside[0] / "owner").read_text().startswith("someone gone")


def test_dead_but_young_or_unknown_holder_is_respected(tmp_path):
    c = cfg(tmp_path, lock_stale_s=60)
    d = tmp_path / ".ue_editor_lock"
    d.mkdir()
    (d / "pid").write_text("777\n")
    r = g.acquire_lock(c, "me", pid=5, alive=lambda p, k=None: False)
    assert not r["ok"] and "younger" in r["reason"]
    old = time.time() - 300
    os.utime(d, (old, old))
    r = g.acquire_lock(c, "me", pid=5, alive=lambda p, k=None: None)
    assert not r["ok"] and "unknown" in r["reason"]


def test_shell_made_lock_pidkind_detection(tmp_path):
    d = tmp_path / "L"
    d.mkdir()
    (d / "pid").write_text("207079\n")
    info = g.read_lock(d)
    assert info["pid"] == 207079 and info["pidkind"] == ("posix" if os.name == "nt" else g.PIDKIND)


def test_handover_and_release(tmp_path):
    c = cfg(tmp_path)
    g.acquire_lock(c, "launch", pid=100)
    assert not g.set_lock_pid(c, 99, 200)["ok"]
    assert g.set_lock_pid(c, 100, 200)["ok"]
    assert g.read_lock(c.lock_path)["pid"] == 200
    # someone else (alive) cannot release it
    r = g.release_lock(c, pid=100, alive=lambda p, k=None: True)
    assert not r["ok"] and c.lock_path.is_dir()
    assert g.release_lock(c, pid=200)["lock"] == "released"
    assert not c.lock_path.exists()
    assert g.release_lock(c, pid=200)["lock"] == "not_held"


def test_release_of_dead_holder(tmp_path):
    c = cfg(tmp_path)
    g.acquire_lock(c, "launch", pid=100)
    assert g.release_lock(c, pid=1, alive=lambda p, k=None: False)["ok"]


def test_lock_held_by_outer_queue_is_skipped(tmp_path):
    c = cfg(tmp_path, lock_held=True)
    assert g.acquire_lock(c, pid=1)["lock"] == "held_by_caller"
    assert not c.lock_path.exists()


def test_pid_alive_for_this_process():
    assert g.pid_alive(os.getpid()) is True
    assert g.pid_alive(0) is False


def test_pick_port_skips_reserved_and_in_use():
    c = g.GuardConfig(reserved_ports=(55557,), port_range=(55557, 55560))
    busy = {55558}
    assert g.pick_port(c, in_use=lambda p: p in busy) == 55559
    with pytest.raises(RuntimeError, match="no free port"):
        g.pick_port(c, in_use=lambda p: True)


def test_port_in_use_sees_a_listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert g.port_in_use(s.getsockname()[1])
    finally:
        s.close()


def test_status_reports_lock_and_port(tmp_path):
    c = cfg(tmp_path, min_free_ram_gb=4, reserved_ports=(55558,))
    g.acquire_lock(c, "holder", pid=321)
    st = g.status(c, ram_gb=lambda: 2.0, load=lambda: None, in_use=lambda p: False)
    assert st["guards_enabled"] and st["free_ram_gb"] == 2.0 and not st["resources"]["ok"]
    assert st["lock"]["holder"]["pid"] == 321 and st["suggested_port"] == 55559


def test_cli_preflight_and_release(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UNREAL_MCP_EDITOR_LOCK", str(tmp_path / "L"))
    monkeypatch.delenv("UNREAL_MCP_MIN_FREE_RAM_GB", raising=False)
    monkeypatch.delenv("UNREAL_MCP_MAX_LOAD", raising=False)
    me = os.getpid()
    assert g.main(["preflight", "--pid", str(me), "--owner", "cli test"]) == 0
    assert (tmp_path / "L" / "pid").read_text().strip() == str(me)
    assert g.main(["preflight", "--pid", str(me + 1), "--owner", "other"]) == g.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().err
    assert g.main(["lock-release", "--pid", str(me)]) == 0
    assert not (tmp_path / "L").exists()


def test_cli_refuses_low_ram(monkeypatch, capsys):
    if g.free_ram_gb() is None:
        pytest.skip("free RAM unreadable here")
    monkeypatch.setenv("UNREAL_MCP_MIN_FREE_RAM_GB", "100000")  # no machine has that much free
    assert g.main(["check-resources"]) == g.EXIT_REFUSED
    assert "need >= 100000 GB" in capsys.readouterr().err


def test_cli_pick_port(monkeypatch, capsys):
    monkeypatch.setenv("UNREAL_MCP_RESERVED_PORTS", "55557")
    monkeypatch.setattr(g, "port_in_use", lambda p, host="127.0.0.1": p == 55558)
    monkeypatch.setenv("UNREAL_MCP_PORT_RANGE", "55557-55570")
    assert g.main(["pick-port"]) == 0
    assert capsys.readouterr().out.strip() == "55559"
    assert g.main(["pick-port", "--prefer", "55557"]) == 0  # reserved: falls back to the scan
    assert capsys.readouterr().out.strip() == "55559"
