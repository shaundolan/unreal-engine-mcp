"""The shared road rules (unreal_mcp/editor_scripts/_road.py) that every MCP road placement uses: spline_edit's
"road" option and "junction" op, and the town street grid (helpers/infrastructure_creation.py). Same rules as
RingFold's runtime roads (NycCity.Roads.SurfaceContinuity): a continuous profile (no step > 2 cm, the grade turning
<= 3 deg per metre), the ground never through the deck, one pad at one height where roads cross, nothing stacked."""

import math
import random

from unreal_mcp.editor_scripts import _road as R
from unreal_mcp.editor_scripts import render


def hills(x, y):
    # rolling ground, a 2 m wide 0.8 m ridge across the road at x = 5000 and a 1.5 m ditch at x = 12000
    z = 300.0 * math.sin(x / 3000.0) + 40.0 * math.sin(x / 170.0 + y / 90.0)
    if 5000.0 <= x <= 5200.0:
        z += 80.0
    if 11800.0 <= x <= 12200.0:
        z -= 150.0
    return z


def test_profile_continuous_and_over_the_ground():
    pts = R.road_points([(0.0, 0.0), (8000.0, 3000.0), (20000.0, 0.0)], hills, 800.0)
    assert len(pts) > 80
    assert R.surface_faults(pts) == []
    # the ground under the whole width, at and between stations, stays under the deck (by the clearance at stations)
    for i in range(len(pts) - 1):
        (x0, y0, z0), (x1, y1, z1) = pts[i], pts[i + 1]
        L = math.hypot(x1 - x0, y1 - y0) or 1.0
        nx, ny = -(y1 - y0) / L, (x1 - x0) / L
        for t in (0.0, 0.25, 0.5, 0.75):
            x, y, z = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, z0 + (z1 - z0) * t
            for o in (-400.0, -200.0, 0.0, 200.0, 400.0):
                assert hills(x + nx * o, y + ny * o) <= z + 1e-6, (i, t, o)


def test_profile_only_raises_and_keeps_flat_ground_flat():
    flat = R.road_points([(0.0, 0.0), (5000.0, 0.0)], lambda x, y: 100.0, 600.0)
    assert all(abs(z - (100.0 + R.CLEAR)) < 1e-6 for _x, _y, z in flat)


def test_junction_one_pad_one_height_no_stacking():
    a = R.road_points([(0.0, 0.0), (20000.0, 0.0)], hills, 800.0)
    b = R.road_points([(10000.0, -8000.0), (10000.0, 8000.0)], lambda x, y: hills(x, y) + 0.02 * y, 700.0)
    a2, b2, pads = R.junction(a, b, 600.0)
    assert len(pads) == 1
    p = pads[0]
    sa, sb = R._arc(a2), R._arc(b2)
    # both roads at the pad's height over the pad
    for prof, arc, key in ((a2, sa, "sa"), (b2, sb, "sb")):
        on = [z for (_x, _y, z), s in zip(prof, arc) if abs(s - p[key]) <= p["radius"]]
        assert on and all(abs(z - p["z"]) < 1e-6 for z in on)
        # still continuous and over the ground after easing in
        assert R.surface_faults(prof) == []
        assert all(zn >= zo - 1e-6 for (_x, _y, zn), (_x2, _y2, zo) in zip(prof, a if prof is a2 else b))
    # the roads' own segments stop at the pad: nothing of either road under it (no road laid twice)
    for prof, arc, key in ((a2, sa, "sa"), (b2, sb, "sb")):
        keep = R.outside_pads(prof, pads, key)
        assert all(abs(arc[i] - p[key]) > p["radius"] for i in keep)
        assert len(keep) < len(prof)


def test_grid_layout_covers_every_street_once():
    blocks, size, width = 3, 6000.0, 1200.0
    pieces = R.grid_layout(blocks, size, width, (100.0, -50.0, 20.0))
    assert {p["center"][2] for p in pieces} == {20.0}
    half = blocks / 2.0
    lines = [(i - half) * size for i in range(blocks + 1)]
    rnd = random.Random(7)

    def covering(x, y):
        n = 0
        for p in pieces:
            cx, cy, _ = p["center"]
            sx, sy = p["size"]
            if abs(x - cx) < sx / 2 - 1e-6 and abs(y - cy) < sy / 2 - 1e-6:
                n += 1
        return n

    for _ in range(3000):
        # a point on a street (either way), not on a piece's own edge
        along = rnd.uniform(lines[0], lines[-1])
        line = rnd.choice(lines)
        off = rnd.uniform(-width / 2 + 1, width / 2 - 1)
        x, y = (along, line + off) if rnd.random() < 0.5 else (line + off, along)
        if any(abs(abs(v - ln) - width / 2) < 1 for v in (x, y) for ln in lines):
            continue
        assert covering(x + 100.0, y - 50.0) == 1, (x, y)


def test_spline_edit_includes_the_road_rules():
    code = render("spline_edit", {"operations": []})
    assert "def road_points(" in code and "def junction(" in code
    compile(code, "spline_edit", "exec")
