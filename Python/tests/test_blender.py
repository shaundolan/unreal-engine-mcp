import json
import shutil
import stat
import sys
from pathlib import Path

import pytest
from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp.config import Settings
from unreal_mcp.tools.blender import run_blender

REAL_BLENDER = shutil.which("blender")
needs_blender = pytest.mark.skipif(not REAL_BLENDER, reason="Blender not installed")

FAKE_BLENDER = f"""#!{sys.executable}
import json, sys
from pathlib import Path
argv = sys.argv
if "--version" in argv:
    print("Blender 9.9 (fake)"); sys.exit(0)
res, out, args, user = argv[argv.index("--") + 1:]
code = Path(user).read_text()
if "CRASH" in code:
    print("Segmentation fault"); sys.exit(139)
out = Path(out); out.mkdir(parents=True, exist_ok=True)
(out / "SM_Fake.fbx").write_bytes(b"fbx" * 10)
Path(res).write_text(json.dumps({{"ok": True, "result": json.loads(Path(args).read_text()),
    "exports": [{{"name": "SM_Fake", "format": "fbx", "path": str(out / "SM_Fake.fbx"),
                  "objects": ["SM_Fake"], "triangles": 12}}], "previews": []}}))
"""


@pytest.fixture
def fake_blender(tmp_path, monkeypatch):
    exe = tmp_path / "blender"
    exe.write_text(FAKE_BLENDER)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("UNREAL_MCP_BLENDER", str(exe))
    monkeypatch.setenv("UNREAL_MCP_WORK_DIR", str(tmp_path / "work"))
    return exe


def test_missing_blender_is_a_clear_error(tmp_path):
    r = run_blender("pass", settings=Settings(blender_path=str(tmp_path / "nope"), work_dir=tmp_path))
    assert r["success"] is False and "UNREAL_MCP_BLENDER" in r["error"]


def test_blender_crash_reports_output(fake_blender):
    r = run_blender("CRASH")
    assert r["success"] is False and "exited with code 139" in r["error"]
    assert "Segmentation fault" in r["output_tail"]


def test_blender_run_imports_exports_into_unreal(ue, tools, fake_blender):  # noqa: F811
    status = tools["blender_status"]()
    assert status["available"] and status["version"] == "Blender 9.9 (fake)"
    out = tools["blender_run"]("anything", args={"k": 1}, import_destination="/Game/Props")
    r = out[0]
    assert r["success"] and r["result"] == {"k": 1} and r["exports"][0]["bytes"] == 30
    assert r["import"]["imports"][0]["imported"] == ["/Game/Props/SM_Fake.SM_Fake"]
    assert len(out) == 1  # no previews rendered


@needs_blender
def test_real_blender_models_exports_and_previews(tmp_path):
    from mcp.server.fastmcp import Image  # noqa: F401  (tool returns these)
    code = """
clear_scene()
c = primitive("cube", "SM_Crate", size=1)
modifier(c, "BEVEL", width=0.04, segments=2)
material(c, "M_Crate", color=(0.45, 0.3, 0.15))
lid = primitive("cylinder", "Lid", radius=0.2, depth=0.1, location=(0, 0, 0.55))
crate = join([c, lid], "SM_Crate")
smart_uv(crate); shade_smooth(crate); origin_to_bottom(crate)
preview()
export("SM_Crate", "fbx"); export("SM_Crate", "glb")
result = {"tris": triangles(), "bounds": bounds(), "materials": [m.name if m else None for m in crate.data.materials],
          "uv": len(crate.data.uv_layers), "arg": ARGS["n"]}
"""
    r = run_blender(code, args={"n": 3}, settings=Settings(blender_path=REAL_BLENDER, work_dir=tmp_path))
    assert r["success"], r.get("traceback") or r["output_tail"]
    res = r["result"]
    assert res["arg"] == 3 and res["uv"] == 1 and res["materials"] == ["M_Crate", None]
    assert res["bounds"]["min"][2] == pytest.approx(0) and res["bounds"]["size"][2] == pytest.approx(1.1)
    assert 12 < res["tris"]["SM_Crate"] < 5000
    assert [e["format"] for e in r["exports"]] == ["fbx", "glb"] and all(e["bytes"] > 1000 for e in r["exports"])
    assert r["exports"][0]["material_slots"] == ["<empty>", "M_Crate"]
    png = Path(r["previews"][0]).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@needs_blender
def test_real_blender_user_error_returns_traceback(tmp_path):
    r = run_blender("clear_scene()\nexport('x', 'stl')",
                    settings=Settings(blender_path=REAL_BLENDER, work_dir=tmp_path))
    assert r["success"] is False and "unsupported export format" in r["traceback"]
    assert json.dumps(r)  # serializable
