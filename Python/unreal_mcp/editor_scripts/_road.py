# Road surface rules shared by every MCP road placement: spline_edit's "road" option and its "junction" op (text sent
# to the editor with `# include: _road`), and the town street grid (helpers/infrastructure_creation.py, which imports
# this file). Pure Python: no `unreal` here, so tests/test_road_surface.py runs it as is. The same rules as RingFold's
# planet roads (Source/Kinfold/Roads/PlanetRoads.cpp, NycCity.Roads.SurfaceContinuity):
#   * one continuous vertical profile: dense stations, no step, the grade turning at most MAX_BEND_DEG per metre;
#   * the deck at least CLEAR above the ground under its whole width (the ground never pokes through it), the ground
#     carved to sit MARGIN under it;
#   * a crossing is ONE junction pad at ONE height, both roads blended to it and stopped at its edge (no road laid
#     over another, no lip where they meet);
#   * one collision surface per piece, the same one that is drawn.
# Units: cm (Unreal world), z up.
import math as _rm

STEP = 200.0           # station spacing along a road
CLEAR = 3.0            # the deck's top over the highest ground under its width
MARGIN = 5.0           # the carved ground under the deck's top (CLEAR + the mesh's own thickness hides the rest)
MAX_BEND_DEG = 3.0     # the grade may turn at most this much per 100 cm of road
MAX_STEP = 2.0         # a step in the surface (tests): never more than this
BLEND = 1500.0         # a road eases into a junction pad's height over this length


def stations(points, step=STEP):
    """[(x, y)] polyline -> [(x, y, s)] every <= step cm along it (the given points kept)."""
    out = []
    s = 0.0
    for i in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[i][:2], points[i + 1][:2]
        seg = _rm.hypot(x1 - x0, y1 - y0)
        if seg < 1e-6:
            continue
        n = max(1, int(_rm.ceil(seg / step)))
        for k in range(n):
            t = k / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, s + seg * t))
        s += seg
    if points:
        x, y = points[-1][:2]
        out.append((x, y, s))
    return out


def _normals(st):
    """Unit left normals per station (central differences)."""
    out = []
    for i in range(len(st)):
        a = st[max(0, i - 1)]
        b = st[min(len(st) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = _rm.hypot(dx, dy) or 1.0
        out.append((-dy / L, dx / L))
    return out


def floor_heights(st, ground, width, samples=5):
    """The highest ground under the road at each station (ground(x, y) -> z or None): across its width, at the station
    and a quarter and half way to each neighbour (the deck is straight between stations: a bump between two would
    come through)."""
    nr = _normals(st)
    out = []
    for i, ((x, y, _s), (nx, ny)) in enumerate(zip(st, nr)):
        best = None
        along = [(x, y, nx, ny)]
        for j in (i - 1, i + 1):
            if 0 <= j < len(st):
                # (across that segment's own direction: at a bend the two segments' cross-sections differ)
                dx, dy = st[j][0] - x, st[j][1] - y
                L = _rm.hypot(dx, dy) or 1.0
                sx, sy = (-dy / L, dx / L) if j > i else (dy / L, -dx / L)
                for t in (0.25, 0.5):
                    along.append((x + dx * t, y + dy * t, sx, sy))
                along.append((x, y, sx, sy))
        for ax, ay, ux, uy in along:
            for k in range(samples):
                o = (k / (samples - 1) - 0.5) * width if samples > 1 else 0.0
                z = ground(ax + ux * o, ay + uy * o)
                if z is not None and (best is None or z > best):
                    best = z
        out.append(best)
    # stations with no ground at all (over a void): carried from the neighbours
    known = [i for i, z in enumerate(out) if z is not None]
    if not known:
        return [0.0] * len(out)
    for i, z in enumerate(out):
        if z is None:
            j = min(known, key=lambda k: abs(k - i))
            out[i] = out[j]
    return out


def profile(st, floor, clear=CLEAR, max_bend_deg=MAX_BEND_DEG):
    """Deck heights over the stations: the lowest smooth profile never under floor + clear whose grade turns at most
    max_bend_deg per metre either way. Two exact steps (curvature bound k = the turn per cm):
      crests  every floor point carries a downward parabola of curvature k, the profile is their upper envelope (a
              crest or a ridge gets a rounded vertical curve over it; the envelope only ever has sag kinks);
      sags    the profile minus k s^2 / 2 is replaced by its upper concave hull, plus k s^2 / 2 back (each sag fills
              to a parabola of curvature k; crests stay within -k: the hull is C1 over the envelope's sag kinks).
    Only ever raises: the floor constraint always holds."""
    n = len(st)
    f = [x + clear for x in floor]
    if n < 3:
        return f
    s0 = st[0][2]
    s = [p[2] - s0 for p in st]
    k = 0.95 * _rm.tan(_rm.radians(max_bend_deg)) / 100.0
    reach = _rm.sqrt(2.0 * max(1.0, max(f) - min(f)) / k)
    # crests: the upper envelope of the floor's parabolas (a sliding window: farther ones are under the floor anyway)
    d = []
    lo = 0
    for i in range(n):
        while s[i] - s[lo] > reach:
            lo += 1
        best = f[i]
        j = lo
        while j < n and s[j] - s[i] <= reach:
            v = f[j] - 0.5 * k * (s[i] - s[j]) ** 2
            if v > best:
                best = v
            j += 1
        d.append(best)
    # sags: the upper concave hull of d - k s^2 / 2 (monotone chain), sampled back at the stations
    g = [d[i] - 0.5 * k * s[i] ** 2 for i in range(n)]
    hull = []
    for i in range(n):
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            # drop b when it lies on or under the chord a -> i
            if (g[b] - g[a]) * (s[i] - s[a]) <= (g[i] - g[a]) * (s[b] - s[a]):
                hull.pop()
            else:
                break
        hull.append(i)
    z = [0.0] * n
    for h in range(len(hull) - 1):
        a, b = hull[h], hull[h + 1]
        for i in range(a, b + 1):
            t = (s[i] - s[a]) / (s[b] - s[a]) if s[b] > s[a] else 0.0
            z[i] = g[a] + (g[b] - g[a]) * t + 0.5 * k * s[i] ** 2
    return [max(a, b) for a, b in zip(z, f)]


def road_points(points, ground, width, step=STEP, clear=CLEAR, max_bend_deg=MAX_BEND_DEG):
    """A road's centreline [(x, y[, z])] -> dense [(x, y, z_top)]: its deck top, continuous and over the ground."""
    st = stations(points, step)
    z = profile(st, floor_heights(st, ground, width), clear, max_bend_deg)
    return [(x, y, zz) for (x, y, _s), zz in zip(st, z)]


def surface_faults(pts, max_step=MAX_STEP, max_bend_deg=MAX_BEND_DEG):
    """Steps (> max_step between neighbours beyond the local grade) and slope breaks (> max_bend_deg per metre) along a
    dense [(x, y, z)] profile: [(index, kind, value)]."""
    out = []
    s = [0.0]
    for i in range(1, len(pts)):
        s.append(s[-1] + _rm.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    for i in range(1, len(pts) - 1):
        d0, d1 = s[i] - s[i - 1], s[i + 1] - s[i]
        if d0 < 1e-6 or d1 < 1e-6:
            continue
        g0 = (pts[i][2] - pts[i - 1][2]) / d0
        g1 = (pts[i + 1][2] - pts[i][2]) / d1
        if abs(g1 * d1 - g0 * d1) > max_step and d1 <= 50.0:
            out.append((i, "step", g1 * d1 - g0 * d1))
        turn = abs(_rm.degrees(_rm.atan(g1) - _rm.atan(g0))) / max(1e-6, 0.5 * (d0 + d1) / 100.0)
        if turn > max_bend_deg + 1e-6:
            out.append((i, "bend", turn))
    return out


def crossings(a, b):
    """Where two polylines [(x, y, ...)] cross: [(ia, ta, ib, tb, x, y)] (segment index + parameter on each)."""
    out = []
    for i in range(len(a) - 1):
        ax0, ay0, ax1, ay1 = a[i][0], a[i][1], a[i + 1][0], a[i + 1][1]
        for j in range(len(b) - 1):
            bx0, by0, bx1, by1 = b[j][0], b[j][1], b[j + 1][0], b[j + 1][1]
            dax, day, dbx, dby = ax1 - ax0, ay1 - ay0, bx1 - bx0, by1 - by0
            den = dax * dby - day * dbx
            if abs(den) < 1e-9:
                continue
            ta = ((bx0 - ax0) * dby - (by0 - ay0) * dbx) / den
            tb = ((bx0 - ax0) * day - (by0 - ay0) * dax) / den
            if 0.0 <= ta <= 1.0 and 0.0 <= tb <= 1.0:
                x, y = ax0 + dax * ta, ay0 + day * ta
                # (a crossing exactly on a station is found on both segments either side: once)
                if all(_rm.hypot(x - o[4], y - o[5]) > 1.0 for o in out):
                    out.append((i, ta, j, tb, x, y))
    return out


def _arc(pts):
    s = [0.0]
    for i in range(1, len(pts)):
        s.append(s[-1] + _rm.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    return s


def _smooth01(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def junction(a, b, radius, blend=BLEND):
    """Two dense road profiles [(x, y, z)] that cross: ONE pad height for the crossing (the higher of the two decks
    over the pad: neither road's ground comes through), both profiles eased into it over `blend` (only raised, so
    each stays over its ground) and the pad itself. Returns (a2, b2, pads): pads = [{x, y, z, radius, ia, ib}]."""
    xs = crossings(a, b)
    if not xs:
        return a, b, []
    sa, sb = _arc(a), _arc(b)
    a2, b2 = [list(p) for p in a], [list(p) for p in b]
    pads = []
    for ia, ta, ib, tb, x, y in xs:
        ja = sa[ia] + (sa[ia + 1] - sa[ia]) * ta
        jb = sb[ib] + (sb[ib + 1] - sb[ib]) * tb
        h = max([p[2] for p, s in zip(a2, sa) if abs(s - ja) <= radius] + [p[2] for p, s in zip(b2, sb) if abs(s - jb) <= radius]
                + [a2[ia][2], b2[ib][2]])
        k = 0.95 * _rm.tan(_rm.radians(MAX_BEND_DEG)) / 100.0
        for prof, arc, j in ((a2, sa, ja), (b2, sb, jb)):
            # the ease's length: its own curvature (6 dh / B^2 at most) well inside the bend limit
            dh = max([h - p[2] for p, s in zip(prof, arc) if radius < abs(s - j) <= radius + 4.0 * blend] + [0.0])
            B = max(blend, 2.5 * _rm.sqrt(6.0 * dh / k))
            for p, s in zip(prof, arc):
                d = abs(s - j)
                if d <= radius:
                    p[2] = h
                elif d <= radius + B:
                    w = 1.0 - _smooth01((d - radius) / B)
                    if h > p[2]:
                        p[2] += (h - p[2]) * w   # (only raised: the road stays over its ground)
        pads.append({"x": x, "y": y, "z": h, "radius": radius, "ia": ia, "ib": ib, "sa": ja, "sb": jb})
    # the eases sit on curved roads: the profile rule once more over each (only raising), the pads re-levelled to the
    # highest station under them, until both hold (a couple of rounds)
    for _ in range(60):
        for prof, arc in ((a2, sa), (b2, sb)):
            st = [(p[0], p[1], s) for p, s in zip(prof, arc)]
            z = profile(st, [p[2] for p in prof], 0.0)
            for p, zz in zip(prof, z):
                p[2] = zz
        level = True
        for pd in pads:
            on = [p for prof, arc, key in ((a2, sa, "sa"), (b2, sb, "sb")) for p, s in zip(prof, arc) if abs(s - pd[key]) <= pd["radius"]]
            top = max([pd["z"]] + [p[2] for p in on])
            level &= all(abs(p[2] - top) < 1e-6 for p in on)
            pd["z"] = top
            for p in on:
                p[2] = top
        if level:
            break
    return [tuple(p) for p in a2], [tuple(p) for p in b2], pads


def outside_pads(pts, pads, key):
    """Station indices of a road that are NOT under one of its junction pads (key "sa" / "sb": that road's arc length
    at the pad): the road's own surface stops at the pad's edge, the pad is the one surface over the crossing."""
    s = _arc(pts)
    keep = []
    for i, si in enumerate(s):
        if all(abs(si - p[key]) > p["radius"] for p in pads):
            keep.append(i)
    return keep


def grid_layout(blocks, block_size, street_width, origin=(0.0, 0.0, 0.0)):
    """A town's street grid as non-overlapping pieces: (blocks + 1) streets each way, every crossing one square pad
    (street_width across), the blocks between them one strip each, all with their top at origin z. Returns
    [{"kind": "pad"|"strip", "center": (x, y, z_top), "size": (sx, sy)}]: together they cover every street once (no
    gap, no piece over another)."""
    ox, oy, oz = origin
    n = blocks + 1
    half = blocks / 2.0
    lines = [(i - half) * block_size for i in range(n)]
    out = []
    for i in range(n):
        for j in range(n):
            out.append({"kind": "pad", "center": (ox + lines[i], oy + lines[j], oz), "size": (street_width, street_width)})
    for i in range(n):
        for j in range(n - 1):
            mid = (lines[j] + lines[j + 1]) * 0.5
            length = block_size - street_width
            # along x between two crossings on the street y = lines[i], and along y on x = lines[i]
            out.append({"kind": "strip", "center": (ox + mid, oy + lines[i], oz), "size": (length, street_width)})
            out.append({"kind": "strip", "center": (ox + lines[i], oy + mid, oz), "size": (street_width, length)})
    return out
