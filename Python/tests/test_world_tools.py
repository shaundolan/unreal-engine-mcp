"""World building: heightfield math (pure Python) and the request shapes the landscape / foliage / PCG /
spline tools send. The editor side is verified live (UE-Bench world-* tasks, bench/results)."""

import base64
import math

import pytest

from unreal_mcp import heightfield as hf
from unreal_mcp.editor_scripts import render


# ---------------------------------------------------------------------------------------------- heightfield
def test_grid_size_and_validation():
    assert hf.grid_size([4, 4], 31, 1) == (125, 125)
    assert hf.grid_size(2, 63, 2) == (253, 253)
    for bad in (([4, 4], 30, 1), ([4, 4], 31, 3), ([0, 4], 31, 1), ([33, 1], 7, 1)):
        with pytest.raises(ValueError):
            hf.grid_size(*bad)


def test_u16_roundtrip_and_encoding():
    f = hf.Field(5, 4, origin=(0, 0, 100), scale=(100, 100, 50))
    f.data = [100 + 10 * k for k in range(20)]
    a = f.to_u16()
    assert a[0] == 32768 and a[1] == 32768 + round(10 * 128 / 50)
    g = hf.Field.from_b64(f.to_b64(), 5, 4, (0, 0, 100), (100, 100, 50))
    assert max(abs(x - y) for x, y in zip(f.data, g.data, strict=True)) < 0.5
    assert len(base64.b64decode(f.to_b64())) == 2 * 20
    f.data[0] = 100 + 300 * 50  # beyond +256*scale.z
    assert f.clipped() == 1 and f.to_u16()[0] == 65535
    with pytest.raises(ValueError):
        hf.Field.from_b64(f.to_b64(), 4, 4, (0, 0, 0), (100, 100, 100))


def test_noise_is_deterministic_and_bounded():
    a, b, c = (hf.Field(33, 33) for _ in range(3))
    hf.sculpt(a, {"op": "noise", "amplitude": 1000, "wavelength": 800, "seed": 4})
    hf.sculpt(b, {"op": "noise", "amplitude": 1000, "wavelength": 800, "seed": 4})
    hf.sculpt(c, {"op": "noise", "amplitude": 1000, "wavelength": 800, "seed": 5})
    assert a.data == b.data and a.data != c.data
    assert max(abs(z) for z in a.data) <= 1000 and max(a.data) - min(a.data) > 200
    with pytest.raises(ValueError):
        hf.sculpt(a, {"op": "noise", "wavelength": 0})
    with pytest.raises(ValueError, match="unknown sculpt op"):
        hf.sculpt(a, {"op": "explode"})


def test_ramp_slope_hill_flatten_terrace():
    f = hf.Field(21, 11, scale=(100, 100, 100))
    hf.sculpt(f, {"op": "ramp", "start_uv": [0, 0.5], "end_uv": [1, 0.5], "height_start": 0, "height_end": 2000})
    assert f.data[0] == 0 and f.data[20] == 2000
    assert abs(f.stats()["slope_deg"]["p50"] - math.degrees(math.atan(1.0))) < 0.01  # 2000 cm over 2000 cm
    g = hf.Field(41, 41)
    hf.sculpt(g, {"op": "hill", "center": [2000, 2000], "radius": 1000, "height": 500})
    assert g.data[20 * 41 + 20] == pytest.approx(500) and g.data[0] == pytest.approx(0, abs=1e-6)
    hf.sculpt(g, {"op": "flatten", "center_uv": [0.5, 0.5], "radius": 300, "falloff": 0, "height": 100})
    assert g.data[20 * 41 + 20] == 100
    t = hf.Field(3, 1)
    t.data = [50.0, 450.0, 760.0]  # values snap toward the nearest plateau (0, 500, 1000)
    hf.sculpt(t, {"op": "terrace", "step": 500, "sharpness": 1.0})
    assert t.data[0] == pytest.approx(0) and t.data[1] == pytest.approx(500) and 760 < t.data[2] < 1000


def test_smooth_and_erode_reduce_slopes():
    f = hf.Field(31, 31)
    hf.sculpt(f, {"op": "noise", "amplitude": 3000, "wavelength": 500, "seed": 1})
    before = f.stats()["slope_deg"]["max"]
    e = hf.Field(31, 31, data=list(f.data))
    hf.sculpt(f, {"op": "smooth", "iterations": 2})
    hf.sculpt(e, {"op": "erode", "iterations": 30, "talus_deg": 30, "strength": 0.5})
    assert f.stats()["slope_deg"]["max"] < before and e.stats()["slope_deg"]["max"] < before
    assert abs(sum(e.data) - sum(hf.Field(31, 31, data=list(e.data)).data)) < 1e-6  # mass conserved by erode


def test_png_and_raw_heightmap_import(tmp_path):
    vals = [(i + j) / 13 for j in range(8) for i in range(7)]
    png = tmp_path / "h.png"
    png.write_bytes(hf.encode_png_gray16(vals, 7, 8))
    img, w, h = hf.load_heightmap(str(png))
    assert (w, h) == (7, 8) and max(abs(a - b) for a, b in zip(img, vals, strict=True)) < 1e-4
    f = hf.Field(7, 8)
    hf.sculpt(f, {"op": "import_heightmap", "path": str(png), "min_height": -100, "max_height": 1300})
    assert f.data[0] == pytest.approx(-100, abs=0.1) and f.data[-1] == pytest.approx(1300, abs=0.1)
    raw = tmp_path / "h.r16"
    raw.write_bytes(b"".join(int(v * 65535).to_bytes(2, "little") for v in [0, 0.5, 0.5, 1.0]))
    g = hf.Field(3, 3)
    hf.sculpt(g, {"op": "import_heightmap", "path": str(raw), "min_height": 0, "max_height": 1000})
    assert g.data[0] == 0 and g.data[4] == pytest.approx(500, abs=1) and g.data[8] == pytest.approx(1000, abs=0.1)
    with pytest.raises(FileNotFoundError):
        hf.load_heightmap(str(tmp_path / "missing.png"))


def test_paint_weights_layering():
    f = hf.Field(21, 21)
    hf.sculpt(f, {"op": "ramp", "start_uv": [0, 0], "end_uv": [0.5, 0], "height_start": 0, "height_end": 1000})
    w = hf.paint_weights(f, [{"layer": "Grass"}, {"layer": "Rock", "slope_min": 30, "slope_falloff": 0},
                             {"layer": "Snow", "height_min": 900, "height_falloff": 0}], existing_layers=["Old"])
    assert set(w) == {"Grass", "Rock", "Snow", "Old"} and sum(w["Old"]) == 0
    assert all(sum(w[k][n] for k in w) == 255 for n in range(21 * 21))
    assert w["Rock"][10 * 21 + 5] == 255  # 45 degree ramp -> rock
    assert w["Snow"][10 * 21 + 15] == 255 and w["Grass"][10 * 21 + 15] == 0  # plateau at 1000 -> snow
    with pytest.raises(ValueError):
        hf.paint_weights(f, [])


# ------------------------------------------------------------------------------------------- landscape_edit
@pytest.fixture
def scripts(monkeypatch):
    """Capture editor-script calls of the world tool modules (their editor side is verified live)."""
    calls = []
    replies = {}

    def fake(get_conn, name, args, timeout_s=120.0):
        calls.append((name, args))
        r = replies.get(name, {"success": True})
        return r(args) if callable(r) else r

    for mod in ("landscape", "foliage", "pcg", "splines"):
        monkeypatch.setattr(f"unreal_mcp.tools.{mod}.run_editor_script", fake)
    return calls, replies


def _sent(fake, cmd):
    return [r["params"] for r in fake.requests if r.get("type") == cmd]


def test_landscape_create_sends_one_create_with_heights_layers_weights(fake, tools, scripts):
    calls, replies = scripts
    replies["landscape_material"] = lambda a: {"success": True, "path": a["path"] + ".M_T",
                                               "layers": [lay["name"] for lay in a["layers"]]}
    fake.on("landscape_create", lambda p: {"label": p["label"], "name": "Landscape_0", "errors": []})
    r = tools["landscape_edit"]([
        {"op": "create", "label": "Hills", "component_count": [2, 2], "section_size": 7, "location": [100, 0, 50]},
        {"op": "noise", "amplitude": 500, "wavelength": 400, "seed": 1},
        {"op": "material", "path": "/Game/W/M_T", "layers": [{"name": "Grass", "color": [0, 1, 0]}]},
        {"op": "paint", "rules": [{"layer": "Grass"}, {"layer": "Rock", "slope_min": 20}]},
    ])
    assert r["success"] and r["written"] and r["layer_coverage"]["Grass"] > 0
    (p,) = _sent(fake, "landscape_create")
    assert p["component_count"] == [2, 2] and p["section_size"] == 7 and p["location"] == [100, 0, 50]
    assert len(base64.b64decode(p["heights"])) == 2 * 15 * 15
    assert set(p["weights"]) == {"Grass", "Rock"}
    assert all(len(base64.b64decode(v)) == 225 for v in p["weights"].values())
    assert p["layers"] == ["Grass", "Rock"] and p["material"] == "/Game/W/M_T.M_T"
    assert p["layer_info_dir"] == "/Game/W/LayerInfos"
    assert calls[0][0] == "landscape_material" and calls[0][1]["layers"][0]["name"] == "Grass"


def test_landscape_edit_validates_before_writing(fake, tools, scripts):
    bad = tools["landscape_edit"]([{"op": "noise"}, {"op": "create", "section_size": 30}, {"op": "melt"},
                                   {"op": "paint"}])
    assert bad["success"] is False and bad["written"] is False
    assert {e["index"] for e in bad["errors"]} == {1, 2, 3}
    assert not _sent(fake, "landscape_create") and not _sent(fake, "landscape_inspect")
    missing = tools["landscape_edit"]([{"op": "create", "component_count": [1, 1], "section_size": 7},
                                       {"op": "import_heightmap", "path": "/nope/h.png"}])
    assert missing["success"] is False and "not found" in missing["error"] and not _sent(fake, "landscape_create")


def _existing(fake, heights_z=0.0):
    f = hf.Field(8, 8, origin=(-350, -350, 0))
    f.data = [heights_z] * 64
    info = {"label": "T", "name": "Landscape_3", "location": [-350, -350, 0], "scale": [100, 100, 100], "yaw": 0,
            "resolution": [8, 8], "layers": [{"name": "Grass"}, {"name": "Old"}],
            "height_world_cm": {"min": 0, "max": 0, "mean": 0}}
    fake.on("landscape_inspect", lambda p: {**info, **({"heights": f.to_b64()} if p.get("include_heights") else {})})
    fake.on("landscape_write", lambda p: {**info, "written": ["heights"], "errors": []})
    return info


def test_landscape_edit_existing_reads_once_writes_once(fake, tools, scripts):
    _existing(fake)
    r = tools["landscape_edit"]([{"op": "hill", "center": [0, 0], "radius": 300, "height": 400},
                                 {"op": "paint", "layer": "Sand"}], target="T")
    assert r["success"], r
    (insp,) = _sent(fake, "landscape_inspect")
    assert insp == {"target": "T", "include_heights": True}
    (w,) = _sent(fake, "landscape_write")
    assert w["target"] == "Landscape_3" and set(w["weights"]) == {"Sand", "Grass", "Old"}
    z = hf.Field.from_b64(w["heights"], 8, 8, (-350, -350, 0), (100, 100, 100))
    assert max(z.data) > 300
    assert base64.b64decode(w["weights"]["Grass"]) == bytes(64)  # stale layers zeroed


def test_landscape_dry_run_and_inspect(fake, tools, scripts):
    _existing(fake)
    d = tools["landscape_edit"]([{"op": "noise", "amplitude": 200}], dry_run=True)
    assert d["success"] and d["dry_run"] and "slope_deg" in d and not _sent(fake, "landscape_write")
    i = tools["landscape_inspect"]("T")
    assert i["slope_deg"]["max"] == 0 and "heights" not in i
    fake.on("landscape_inspect", lambda p: {"success": False, "error": "the level has no landscape"})
    assert tools["landscape_inspect"]()["success"] is False


def test_landscape_delete(fake, tools, scripts):
    calls, replies = scripts
    _existing(fake)
    replies["scene_compose"] = {"success": True, "succeeded": 1}
    r = tools["landscape_edit"]([{"op": "delete"}], target="T")
    assert r["success"] and r["deleted"] == "T"
    assert calls[-1] == ("scene_compose", {"operations": [{"op": "delete", "target": "Landscape_3"}]})
    assert tools["landscape_edit"]([{"op": "delete"}, {"op": "flat"}])["success"] is False


# --------------------------------------------------------------------------------------- foliage / pcg / spline
def test_foliage_spline_request_shapes(tools, scripts):
    calls, _ = scripts
    ops = [{"op": "scatter", "mesh": "/Game/SM_Tree", "slope_max": 25, "density": 4}]
    tools["foliage_scatter"](ops, dry_run=True, foliage_type_dir="/Game/X/FT")
    tools["foliage_inspect"](sample_slopes=True)
    tools["spline_edit"]([{"op": "create", "label": "Road", "points": [[0, 0, 0], [100, 0, 0]]}])
    tools["spline_inspect"]("Road")
    assert [c[0] for c in calls] == ["foliage_scatter", "foliage_inspect", "spline_edit", "spline_inspect"]
    assert calls[0][1] == {"operations": ops, "dry_run": True, "foliage_type_dir": "/Game/X/FT", "stop_on_error": False}
    assert calls[1][1]["sample_slopes"] is True and calls[3][1]["target"] == "Road"


def test_pcg_graph_waits_for_generation(tools, scripts, monkeypatch):
    calls, replies = scripts
    monkeypatch.setattr("unreal_mcp.tools.pcg.time.sleep", lambda s: None)
    replies["pcg_graph"] = {"success": True, "errors": [], "generation_requested": True,
                            "volume": {"label": "V", "generated": False, "instances": 0}}
    polls = iter([{"generated": False, "instances": 0}, {"generated": True, "instances": 120}])
    replies["pcg_inspect"] = lambda a: {"success": True, "volumes": [{"label": "V", **next(polls)}]}
    r = tools["pcg_graph"]("/Game/PCG/G", nodes=[{"id": "s", "type": "SurfaceSampler"}],
                           edges=[{"from": "input.In", "to": "s.Bounding Shape"}], create=True,
                           volume={"label": "V", "fit": "Terrain"})
    assert r["success"] and r["volume"]["instances"] == 120
    assert calls[0][1]["create"] is True and calls[0][1]["volume"] == {"label": "V", "fit": "Terrain"}
    assert [c[0] for c in calls] == ["pcg_graph", "pcg_inspect", "pcg_inspect"]
    replies["pcg_graph"] = {"success": True, "errors": [{"stage": "edges", "index": 0, "error": "no pin"}]}
    bad = tools["pcg_graph"]("/Game/PCG/G")
    assert bad["success"] is False and "edges 0: no pin" in bad["error"]


def test_world_scripts_include_helpers():
    for name in ("foliage_scatter", "foliage_inspect", "spline_edit"):
        assert "def trace_down" in render(name, {})
    for name in ("pcg_graph", "pcg_inspect"):
        assert "def pcg_settings_class" in render(name, {})
    assert "def trace_down" not in render("scene_compose", {})


# ------------------------------------------------------------------------------------------ UE-Bench world-*
def test_world_bench_tasks_load_and_landscape_specs_meet_their_checkers(tools, scripts):
    from unreal_mcp import bench

    world = {t.id: t for t in bench.load_tasks() if t.domain == "world"}
    assert set(world) >= {"world-rolling-hills", "world-forest-slopes", "world-road-spline", "world-pcg-forest"}
    assert all("command:landscape_create" in t.requires for t in world.values())  # live-only (fake CI skips)
    hills = [o for o in world["world-rolling-hills"].solution[0]["args"]["operations"] if o["op"] != "material"]
    d = tools["landscape_edit"](hills, dry_run=True)
    assert d["success"] and d["height_world_cm"]["max"] - d["height_world_cm"]["min"] >= 800
    assert d["slope_deg"]["max"] <= 35 and d["layer_coverage"]["Grass"] == 1.0
    steep = [dict(o, amplitude=4000, wavelength=1500) if o["op"] == "noise" else o for o in hills]  # near miss
    assert tools["landscape_edit"](steep, dry_run=True)["slope_deg"]["max"] > 35
    terrain = world["world-forest-slopes"].setup[0]["args"]["operations"]
    t = tools["landscape_edit"](terrain, dry_run=True)
    assert t["slope_deg"]["p50"] < 25 < t["slope_deg"]["max"]  # the slope filter has something to reject
