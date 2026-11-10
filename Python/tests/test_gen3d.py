"""mesh_generate / mesh_postprocess / auto_rig end to end: mock gen3d server (the real FastAPI app, mock backend)
-> HTTP client -> Blender post-process / auto-rig (real Blender when installed, else a fake) -> asset import into
the fake editor -> LODs, collision, IK Rig, retargeter, retargeted animations, placed actor."""

import base64
import json
import shutil
import stat
import struct
import sys
import urllib.request
from pathlib import Path

import pytest
from gen3d_mock import MockGen3D
from test_editor_scripts import ue  # noqa: F401  (fixture)

from unreal_mcp.config import Settings
from unreal_mcp.gen3d import Gen3DClient, Gen3DError
from unreal_mcp.tools.gen3d import _licence_block, lod_fractions

REAL_BLENDER = shutil.which("blender")
needs_blender = pytest.mark.skipif(not REAL_BLENDER, reason="Blender not installed")

FAKE_BLENDER = f"""#!{sys.executable}
import json, shutil, sys
from pathlib import Path
argv = sys.argv
if "--version" in argv:
    print("Blender 9.9 (fake)"); sys.exit(0)
res, out, args, user = argv[argv.index("--") + 1:]
A = json.loads(Path(args).read_text()); code = Path(user).read_text()
out = Path(out); out.mkdir(parents=True, exist_ok=True)
name = A["name"]
if "def humanoid_joints" in code:
    bones = [b"root", b"pelvis", b"spine_01", b"neck_01", b"head", b"clavicle_l", b"upperarm_l", b"clavicle_r",
             b"upperarm_r", b"thigh_l", b"thigh_r"]
    p = out / (name + ".fbx")  # this fake always writes a minimal skinned FBX
    p.write_bytes(b"Kaydara FBX Binary\\x00" + b"".join(b + b"\\x00\\x01Model" for b in bones) + b"Deformer")
    r = {{"type": A["type"], "bones": [b.decode() for b in bones], "warnings": [],
          "export": {{"name": name, "format": "fbx", "path": str(p)}}}}
else:
    p = out / (name + ".glb")
    shutil.copyfile(A["input"], p)
    r = {{"triangles": 1000, "baked": ["base_color"], "uv": "smart", "size_cm": [60, 60, 100]}}
Path(res).write_text(json.dumps({{"ok": True, "result": r, "previews": [],
    "exports": [{{"name": name, "format": p.suffix[1:], "path": str(p)}}]}}))
"""


@pytest.fixture
def gen3d(monkeypatch, tmp_path):
    srv = MockGen3D()
    monkeypatch.setenv("UNREAL_MCP_GEN3D_URL", srv.url)
    monkeypatch.setenv("UNREAL_MCP_WORK_DIR", str(tmp_path / "work"))
    yield srv
    srv.close()


@pytest.fixture
def fake_blender(tmp_path, monkeypatch):
    exe = tmp_path / "blender"
    exe.write_text(FAKE_BLENDER)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("UNREAL_MCP_BLENDER", str(exe))
    return exe


@pytest.fixture
def real_blender(monkeypatch):
    if not REAL_BLENDER:
        pytest.skip("Blender not installed")
    monkeypatch.setenv("UNREAL_MCP_BLENDER", REAL_BLENDER)


def _glb_json(path):
    data = Path(path).read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + n])


# ---- server + client -------------------------------------------------------------------------------------
def test_server_text_and_image_jobs(gen3d, tmp_path):
    c = Gen3DClient(gen3d.url)
    info = c.info()
    assert info["backend"]["name"] == "mock" and info["text_to_image"]["name"] == "mock"
    assert {"triposr", "sf3d", "hunyuan3d", "trellis2"} <= set(info["available"]["image_to_3d"])
    hy = info["available"]["image_to_3d"]["hunyuan3d"]["licence"]
    assert hy["commercial"] == "conditional" and any("EU" in w for w in hy["warnings"])
    job = c.wait(c.submit(prompt="a wooden barrel"), timeout_s=30, poll_s=0.05)
    assert job["status"] == "done" and job["result"]["source"] == "text" and job["result"]["licence"]["model"]
    glb = c.download(job["job_id"], tmp_path / "a.glb")
    assert glb.read_bytes()[:4] == b"glTF" and "COLOR_0" in json.dumps(_glb_json(glb))
    assert c.download(job["job_id"], tmp_path / "a.png", "image").read_bytes()[:4] == b"\x89PNG"
    img = tmp_path / "knight.png"
    img.write_bytes(b"\x89PNG fake")
    j2 = c.wait(c.submit(image_path=str(img)), timeout_s=30, poll_s=0.05)
    assert j2["result"]["source"] == "image"
    hum = _glb_json(c.download(j2["job_id"], tmp_path / "h.glb"))
    assert hum["meshes"][0]["name"] == "humanoid"  # "knight" in the image name picks the humanoid fixture


def test_server_rejects_bad_requests_and_tokens(tmp_path):
    srv = MockGen3D(token="s3cret")
    try:
        with pytest.raises(Gen3DError, match="401"):
            Gen3DClient(srv.url).info()
        c = Gen3DClient(srv.url, token="s3cret")
        assert c.info()["ok"]
        with pytest.raises(Gen3DError, match="prompt"):
            c.submit()
        with pytest.raises(Gen3DError, match="quality"):
            c.submit(prompt="x", quality="ultra")
        with pytest.raises(Gen3DError, match="404"):
            c.job("nope")
        assert urllib.request.urlopen(srv.url + "/health").status == 200  # health stays open
    finally:
        srv.close()


def test_client_reports_unreachable_server():
    with pytest.raises(Gen3DError, match="UNREAL_MCP_GEN3D_URL"):
        Gen3DClient("http://127.0.0.1:9", timeout_s=2).info()


def test_settings_and_helpers():
    s = Settings.from_env({"UNREAL_MCP_GEN3D_URL": "http://4090box:8765", "UNREAL_MCP_GEN3D_TOKEN": "t"})
    assert (s.gen3d_url, s.gen3d_token) == ("http://4090box:8765", "t")
    assert Settings.from_env({}).gen3d_url == "http://127.0.0.1:8765"
    assert lod_fractions(3) == [0.5, 0.25, 0.125] and lod_fractions(0) == []
    lic = _licence_block({"backend": "x", "licence": {"model": "NC", "commercial": "no"}, "warnings": ["w"]})
    assert lic["warnings"][0].startswith("x output may NOT be used commercially") and lic["warnings"][1] == "w"


def test_gen3d_status_tool(gen3d, tools):
    r = tools["gen3d_status"]()
    assert r["success"] and r["backend"] == "mock" and r["licence"].startswith("MIT")
    r = tools["gen3d_status"](server_url="http://127.0.0.1:9")
    assert r["success"] is False and r["available"] is False


# ---- whole pipeline against the fake editor ----------------------------------------------------------------
def test_mesh_generate_pipeline_with_fake_blender(gen3d, ue, tools, fake_blender):  # noqa: F811
    out = tools["mesh_generate"](prompt="a wooden barrel", out_name="SM_Barrel", destination="/Game/Gen",
                                 collision="box", lods=2, show_preview=False)
    r = out[0]
    assert r["success"], r
    assert r["asset"] == "/Game/Gen/SM_Barrel" and r["generation"]["backend"] == "mock"
    assert r["licence"].startswith("MIT") and r["warnings"] == []
    mesh = ue._state["assets"]["/Game/Gen/SM_Barrel"]
    assert mesh.lod_fractions == [1.0, 0.5, 0.25] and r["unreal"]["asset"]["lod_count"] == 3
    assert r["unreal"]["collision"]["meshes"][0]["simple_shapes"] == 1
    assert len(out) == 1  # no images


def test_mesh_generate_validates_before_calling_server(tools):
    assert "out_name" in tools["mesh_generate"](prompt="x", out_name="bad name")[0]["error"]
    assert "prompt" in tools["mesh_generate"]()[0]["error"]
    assert "collision" in tools["mesh_generate"](prompt="x", collision="mesh")[0]["error"]


def test_mesh_generate_server_down(tools, monkeypatch):
    monkeypatch.setenv("UNREAL_MCP_GEN3D_URL", "http://127.0.0.1:9")
    r = tools["mesh_generate"](prompt="a barrel")[0]
    assert r["success"] is False and "not reachable" in r["error"]


@needs_blender
def test_mesh_generate_real_blender_barrel_90cm(gen3d, ue, tools, real_blender):  # noqa: F811
    from mcp.server.fastmcp import Image
    out = tools["mesh_generate"](prompt="a wooden barrel", height_cm=90, target_tris=1200, texture_size=256,
                                 out_name="SM_Barrel", destination="/Game/Props", collision="convex", lods=3)
    r = out[0]
    assert r["success"], r.get("traceback") or r
    pp = r["postprocess"]
    assert pp["baked"] == ["base_color"] and pp["uv"] == "smart" and pp["triangles"] <= 1200
    assert pp["size_cm"][2] == pytest.approx(90, abs=0.1) and pp["min_cm"][2] == pytest.approx(0, abs=0.01)
    doc = _glb_json(r["glb"])
    assert doc["images"] and "COLOR_0" not in json.dumps(doc["meshes"])  # colour baked into a texture
    a = r["unreal"]["asset"]
    assert a["size_cm"][2] == pytest.approx(90, abs=0.5) and a["min_cm"][2] == pytest.approx(0, abs=0.5)
    assert a["lod_count"] == 4 and a["materials"] == ["/Game/Props/M_Barrel.M_Barrel"]
    assert r["unreal"]["collision"]["meshes"][0]["convex_hulls"] == 8
    assert sum(isinstance(x, Image) for x in out) == 2  # conditioning image + render
    # safe re-import over the existing asset: replace flag set, garbage collected around it
    gc0 = ue.SystemLibrary.gc_runs
    r2 = tools["mesh_generate"](prompt="a wooden barrel", height_cm=90, target_tris=1200, texture_size=256,
                                out_name="SM_Barrel", destination="/Game/Props", show_preview=False)[0]
    assert r2["success"] and ue._state["imports"][-1]["replace"] is True and ue.SystemLibrary.gc_runs >= gc0 + 2


@needs_blender
def test_mesh_postprocess_keeps_textured_uvs(gen3d, ue, tools, real_blender, tmp_path):  # noqa: F811
    first = tools["mesh_generate"](prompt="barrel", height_cm=90, texture_size=128, target_tris=800,
                                   import_to_unreal=False, show_preview=False)[0]
    assert first["success"] and "unreal" not in first
    r = tools["mesh_postprocess"](first["glb"], out_name="SM_Again", destination="/Game/P", target_tris=400,
                                  height_cm=45, show_preview=False)[0]
    assert r["success"], r
    pp = r["postprocess"]
    assert pp["uv"] == "kept" and pp["baked"] == [] and pp["source"]["textures"]
    assert pp["triangles"] <= 400 and pp["size_cm"][2] == pytest.approx(45, abs=0.1)


@needs_blender
def test_auto_rig_humanoid_retargets_mannequin_walk(gen3d, ue, tools, real_blender):  # noqa: F811
    ue.add_mannequin()
    gen = tools["mesh_generate"](prompt="a knight character", subject="character", height_cm=180, target_tris=3000,
                                 texture_size=256, out_name="SM_Knight", collision="none", lods=0,
                                 import_to_unreal=False, show_preview=False)[0]
    assert gen["success"] and "auto_rig" in gen["next"]
    out = tools["auto_rig"](gen["glb"], type="humanoid", out_name="SKM_Knight", destination="/Game/Chars",
                            place_actor="Knight", location=[0, 0, 0])
    r = out[0]
    assert r["success"], r.get("traceback") or r.get("error") or r
    rig = r["rig"]
    assert rig["heat_weights"] and {"pelvis", "upperarm_l", "calf_r", "head", "ball_l"} <= set(rig["bones"])
    assert all(rig["vertices_per_bone"].get(b, 0) > 0 for b in ("head", "hand_l", "hand_r", "foot_l", "thigh_r"))
    assert rig["height_cm"] == pytest.approx(180, abs=0.5)
    skm = ue._state["assets"]["/Game/Chars/SKM_Knight"]
    assert isinstance(skm, ue.SkeletalMesh) and "upperarm_l" in skm.bones and "root" in skm.bones
    assert r["skeleton"] == "/Game/Chars/SKM_Knight_Skeleton"
    assert set(r["chains"]) >= {"Spine", "LeftArm", "RightLeg", "Head"}
    rtg = ue._state["assets"]["/Game/Chars/RTG_Mannequin_to_Knight"]
    assert rtg.aligned and "LeftLeg" in rtg.mapped
    assert sorted(r["animations"]) == ["/Game/Chars/Anims/MF_Unarmed_Walk_Fwd_Knight.MF_Unarmed_Walk_Fwd_Knight",
                                       "/Game/Chars/Anims/MM_Idle_Knight.MM_Idle_Knight"]
    walk = ue._state["assets"]["/Game/Chars/Anims/MF_Unarmed_Walk_Fwd_Knight"]
    assert walk.get_skeleton() is skm.skeleton
    actor = next(a for a in ue._state["actors"] if a.get_actor_label() == "Knight")
    comp = actor.skeletal_mesh_component
    assert comp.skeletal_mesh_asset is skm and comp.animation_data.anim_to_play is walk and comp.is_playing()
    assert str(comp.get_animation_mode()) == "AnimationMode.ANIMATION_SINGLE_NODE"
    # again: the rig assets are replaced, not refused
    r2 = tools["auto_rig"](gen["glb"], out_name="SKM_Knight", destination="/Game/Chars", show_preview=False)[0]
    assert r2["success"], r2.get("error")


@needs_blender
@pytest.mark.parametrize("kind", ["quadruped", "prop"])
def test_auto_rig_other_types(gen3d, ue, tools, real_blender, tmp_path, kind):  # noqa: F811
    from glb_writer import _Builder, write_glb
    b = _Builder()
    if kind == "quadruped":  # a dog-ish block: body along Z (glTF front), four legs, head forward, tail back
        b.box((0, 0.55, 0), (0.3, 0.25, 0.8), (0.5, 0.4, 0.3), 5)
        for x in (-0.1, 0.1):
            for z in (-0.3, 0.3):
                b.box((x, 0.22, z), (0.08, 0.44, 0.08), (0.4, 0.3, 0.2), 4)
        b.box((0, 0.75, 0.48), (0.18, 0.18, 0.22), (0.5, 0.4, 0.3), 3)
        b.box((0, 0.62, -0.55), (0.05, 0.05, 0.3), (0.4, 0.3, 0.2), 3)
    else:
        b.box((0, 0.5, 0), (0.4, 1.0, 0.4), (0.6, 0.6, 0.6), 4)
    f = tmp_path / f"{kind}.glb"
    f.write_bytes(write_glb(b.p, b.t, b.c))
    r = tools["auto_rig"](str(f), type=kind, out_name=f"SKM_{kind}", destination="/Game/R", show_preview=False)[0]
    assert r["success"], r.get("traceback") or r.get("error")
    bones = r["rig"]["bones"]
    if kind == "quadruped":
        assert {"spine_02", "thigh_l", "hand_r", "head", "tail_02"} <= set(bones)
        assert "retarget" not in r and any("humanoid" in w for w in r["warnings"])
    else:
        assert bones == ["root", "body"]
    assert isinstance(ue._state["assets"][f"/Game/R/SKM_{kind}"], ue.SkeletalMesh)


def test_auto_rig_from_unreal_asset_and_errors(gen3d, ue, tools, fake_blender, tmp_path):  # noqa: F811
    ue.add_mannequin()
    src = tmp_path / "hero.glb"
    from glb_writer import fixture_glb
    src.write_bytes(fixture_glb("humanoid"))
    sm = ue.register_asset(ue.StaticMesh("SM_Hero", "/Game/Gen/SM_Hero.SM_Hero"))
    sm.source_file = str(src)  # the fake exporter writes this file out
    r = tools["auto_rig"]("/Game/Gen/SM_Hero", destination="/Game/Chars", show_preview=False)[0]
    assert r["success"], r
    assert r["skeletal_mesh"] == "/Game/Chars/SKM_Hero" and len(r["animations"]) == 2
    assert "not found" in tools["auto_rig"]("/Game/Nope/SM_X", show_preview=False)[0]["error"]
    assert "neither" in tools["auto_rig"](str(tmp_path / "missing.glb"), show_preview=False)[0]["error"]
    assert "type" in tools["auto_rig"](str(src), type="bird")[0]["error"]


def test_fetch_editor_file_chunks(ue, tools, tmp_path, conn):  # noqa: F811
    from unreal_mcp.tools.gen3d import fetch_editor_file
    blob = bytes(range(256)) * 100
    p = tmp_path / "editor" / "big.bin"
    p.parent.mkdir()
    p.write_bytes(blob)
    out = fetch_editor_file(lambda: conn, str(p), tmp_path / "local" / "big.bin", chunk=1000)
    assert out.read_bytes() == blob and not p.exists()  # temp file removed after the last chunk


def test_mock_png_is_valid():
    from backends import solid_png
    png = solid_png(8)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and base64.b64encode(png)
