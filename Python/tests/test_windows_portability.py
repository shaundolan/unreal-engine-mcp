"""Windows-specific paths in the server-side code, exercised on any OS."""

from unreal_mcp import bench
from unreal_mcp.config import Settings
from unreal_mcp.tools import blender


def test_agent_cmd_split_keeps_windows_paths():
    tpl = r'"C:\Program Files\nodejs\claude.cmd" -p {prompt} --mcp-config ..\bench\mcp.bench.json'
    assert bench.split_command(tpl, windows=True) == [
        r"C:\Program Files\nodejs\claude.cmd", "-p", "{prompt}", "--mcp-config", r"..\bench\mcp.bench.json"]
    # POSIX splitting is unchanged elsewhere
    assert bench.split_command("claude -p '{prompt}' --x 'a b'", windows=False) == [
        "claude", "-p", "{prompt}", "--x", "a b"]


def test_blender_found_in_windows_install_dir(tmp_path, monkeypatch):
    for version in ("4.2", "4.5"):
        exe = tmp_path / "Blender Foundation" / f"Blender {version}" / "blender.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
    monkeypatch.setattr(blender, "_IS_WINDOWS", True)
    monkeypatch.setattr(blender.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    found = blender.resolve_blender(Settings())
    assert found and found.endswith("blender.exe") and "Blender 4.5" in found
    # an explicit UNREAL_MCP_BLENDER that doesn't exist is not second-guessed
    assert blender.resolve_blender(Settings(blender_path="blender-nope")) is None
