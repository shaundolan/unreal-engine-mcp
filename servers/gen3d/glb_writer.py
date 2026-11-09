"""Minimal pure-Python GLB (binary glTF 2.0) writer and procedural fixture meshes.

The mock backend uses it so the whole pipeline (server -> client -> Blender post-process -> Unreal import) can be
tested without a GPU or model weights. Fixtures mimic what real image-to-3D models return: a dense, Y-up,
roughly unit-sized mesh with per-vertex colours (TripoSR style) and no UVs.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Sequence

Vec3 = tuple[float, float, float]
Tri = tuple[int, int, int]


def write_glb(positions: Sequence[Vec3], triangles: Sequence[Tri], colors: Sequence[Vec3] | None = None,
              name: str = "mesh") -> bytes:
    """One mesh, one primitive, glTF Y-up metres. colors: linear RGB per vertex (COLOR_0)."""
    pos = b"".join(struct.pack("<3f", *p) for p in positions)
    col = b"".join(struct.pack("<3f", *c) for c in colors) if colors else b""
    idx = b"".join(struct.pack("<3I", *t) for t in triangles)
    views, accessors, blob = [], [], b""

    def add(data: bytes, target: int, count: int, comp: int, typ: str, minmax: tuple | None = None) -> int:
        nonlocal blob
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": count, "type": typ}
        if minmax:
            acc["min"], acc["max"] = minmax
        accessors.append(acc)
        blob += data + b"\0" * (-len(data) % 4)
        return len(accessors) - 1

    lo = [min(p[i] for p in positions) for i in range(3)]
    hi = [max(p[i] for p in positions) for i in range(3)]
    attrs = {"POSITION": add(pos, 34962, len(positions), 5126, "VEC3", (lo, hi))}
    if colors:
        attrs["COLOR_0"] = add(col, 34962, len(colors), 5126, "VEC3")
    indices = add(idx, 34963, len(triangles) * 3, 5125, "SCALAR")
    doc = {
        "asset": {"version": "2.0", "generator": "unreal-mcp gen3d mock"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [{"attributes": attrs, "indices": indices, "material": 0}]}],
        "materials": [{"name": f"M_{name}", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1],
                                                                       "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
        "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": accessors,
    }
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(blob), b"BIN\0") + blob
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def read_glb_json(data: bytes) -> dict:
    """The JSON chunk of a GLB (for tests and the fake editor's importer)."""
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("not a GLB file")
    n, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON":
        raise ValueError("GLB without a JSON chunk")
    return json.loads(data[20:20 + n])


class _Builder:
    def __init__(self):
        self.p: list[Vec3] = []
        self.c: list[Vec3] = []
        self.t: list[Tri] = []

    def box(self, centre: Vec3, size: Vec3, color: Vec3, seg: int = 3) -> None:
        """Axis-aligned box with `seg` subdivisions per edge (separate island, like a kitbashed part)."""
        cx, cy, cz = centre
        sx, sy, sz = (s / 2 for s in size)
        for axis in range(3):
            for sign in (-1, 1):
                base = len(self.p)
                u_ax, v_ax = [a for a in range(3) if a != axis]
                for i in range(seg + 1):
                    for j in range(seg + 1):
                        q = [0.0, 0.0, 0.0]
                        q[axis] = sign
                        q[u_ax] = -1 + 2 * i / seg
                        q[v_ax] = -1 + 2 * j / seg
                        self.p.append((cx + q[0] * sx, cy + q[1] * sy, cz + q[2] * sz))
                        self.c.append(color)
                for i in range(seg):
                    for j in range(seg):
                        a, b = base + i * (seg + 1) + j, base + (i + 1) * (seg + 1) + j
                        quad = (a, b, b + 1, a + 1)
                        # outward winding: flip on the faces whose (u x v) points inward
                        flip = (sign < 0) != ((v_ax - u_ax) % 3 == 2)
                        if flip:
                            self.t += [(quad[0], quad[2], quad[1]), (quad[0], quad[3], quad[2])]
                        else:
                            self.t += [(quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3])]


def barrel(segments: int = 48, rings: int = 24) -> tuple[list[Vec3], list[Tri], list[Vec3]]:
    """A dense, closed barrel about 1 unit tall (Y up), wood-brown with darker hoops (vertex colours)."""
    p: list[Vec3] = []
    c: list[Vec3] = []
    t: list[Tri] = []
    for r in range(rings + 1):
        y = r / rings
        radius = 0.32 + 0.06 * math.sin(math.pi * y)  # bulge
        hoop = abs(y - 0.15) < 0.03 or abs(y - 0.85) < 0.03
        col = (0.12, 0.1, 0.09) if hoop else (0.42, 0.25, 0.12)
        for s in range(segments):
            a = 2 * math.pi * s / segments
            p.append((radius * math.cos(a), y, radius * math.sin(a)))
            c.append(col)
    for r in range(rings):
        for s in range(segments):
            a, b = r * segments + s, r * segments + (s + 1) % segments
            t += [(a, b + segments, b), (a, a + segments, b + segments)]
    for cap, y, flip in ((0, 0.0, True), (rings, 1.0, False)):
        centre = len(p)
        p.append((0.0, y, 0.0))
        c.append((0.35, 0.2, 0.1))
        for s in range(segments):
            a, b = cap * segments + s, cap * segments + (s + 1) % segments
            t.append((centre, a, b) if flip else (centre, b, a))
    return p, t, c


def humanoid() -> tuple[list[Vec3], list[Tri], list[Vec3]]:
    """A blocky T-pose figure 1.8 units tall (Y up, facing +Z like image-to-3D output), arms along X."""
    b = _Builder()
    skin, shirt, pants, shoe = (0.8, 0.6, 0.5), (0.2, 0.35, 0.7), (0.15, 0.15, 0.2), (0.1, 0.07, 0.05)
    b.box((0, 1.66, 0), (0.22, 0.26, 0.24), skin, 4)          # head
    b.box((0, 1.50, 0), (0.1, 0.08, 0.1), skin, 2)            # neck
    b.box((0, 1.22, 0), (0.40, 0.50, 0.22), shirt, 5)         # chest + belly
    b.box((0, 0.93, 0), (0.36, 0.14, 0.2), pants, 3)          # hips
    for side in (-1, 1):
        b.box((side * 0.48, 1.40, 0), (0.58, 0.1, 0.1), shirt, 6)    # arm (T-pose)
        b.box((side * 0.83, 1.40, 0), (0.14, 0.08, 0.1), skin, 2)    # hand
        b.box((side * 0.1, 0.47, 0), (0.14, 0.82, 0.14), pants, 6)   # leg
        b.box((side * 0.1, 0.03, 0.05), (0.13, 0.06, 0.26), shoe, 2)  # foot, toes towards +Z
    return b.p, b.t, b.c


def fixture_glb(kind: str = "barrel") -> bytes:
    p, t, c = humanoid() if kind == "humanoid" else barrel()
    return write_glb(p, t, c, name=kind)
