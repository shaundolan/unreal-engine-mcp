"""Heightfields for landscape_edit: procedural sculpting, heightmap decoding, slope and auto-paint weights.

Pure Python (no numpy) so it runs anywhere the MCP server does and is unit-testable without an editor.
A :class:`Field` holds WORLD Z in cm for every landscape vertex, row-major (y outer, x inner), plus the
landscape placement needed to convert between vertex indices, world XY and Unreal's uint16 encoding
(32768 = the actor's Z; world dz = (h - 32768) / 128 * scale.z).
"""

from __future__ import annotations

import base64
import math
import random
import struct
import zlib
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_SECTION_SIZES = (7, 15, 31, 63, 127, 255)


@dataclass
class Field:
    width: int
    height: int
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)  # landscape actor location (its corner, not centre)
    scale: tuple[float, float, float] = (100.0, 100.0, 100.0)
    yaw: float = 0.0
    data: list[float] = field(default_factory=list)

    def __post_init__(self):
        if not self.data:
            self.data = [float(self.origin[2])] * (self.width * self.height)
        if len(self.data) != self.width * self.height:
            raise ValueError(f"field data has {len(self.data)} values, expected {self.width * self.height}")

    # ------------------------------------------------------------------ geometry
    @property
    def size_cm(self) -> tuple[float, float]:
        return ((self.width - 1) * self.scale[0], (self.height - 1) * self.scale[1])

    def world_xy(self, i: float, j: float) -> tuple[float, float]:
        lx, ly = i * self.scale[0], j * self.scale[1]
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        return self.origin[0] + lx * c - ly * s, self.origin[1] + lx * s + ly * c

    def index_xy(self, x: float, y: float) -> tuple[float, float]:
        """World XY -> fractional vertex indices (i, j)."""
        dx, dy = x - self.origin[0], y - self.origin[1]
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        return (dx * c + dy * s) / self.scale[0], (-dx * s + dy * c) / self.scale[1]

    def point(self, op: dict[str, Any], key: str = "center", default_uv=(0.5, 0.5)) -> tuple[float, float]:
        """An op position as fractional vertex indices: `<key>` = world [x, y] cm or `<key>_uv` = [0..1, 0..1]."""
        if op.get(key) is not None:
            return self.index_xy(float(op[key][0]), float(op[key][1]))
        u, v = op.get(f"{key}_uv", default_uv)
        return float(u) * (self.width - 1), float(v) * (self.height - 1)

    def sample(self, x: float, y: float) -> float:
        """Bilinear world Z at world XY (clamped to the landscape)."""
        i, j = self.index_xy(x, y)
        i = min(max(i, 0.0), self.width - 1.0)
        j = min(max(j, 0.0), self.height - 1.0)
        i0, j0 = int(i), int(j)
        i1, j1 = min(i0 + 1, self.width - 1), min(j0 + 1, self.height - 1)
        fx, fy = i - i0, j - j0
        d, w = self.data, self.width
        top = d[j0 * w + i0] * (1 - fx) + d[j0 * w + i1] * fx
        bot = d[j1 * w + i0] * (1 - fx) + d[j1 * w + i1] * fx
        return top * (1 - fy) + bot * fy

    # ------------------------------------------------------------------ encoding
    def to_u16(self) -> array:
        k = 128.0 / self.scale[2]
        z0 = self.origin[2]
        out = array("H", bytes(2 * len(self.data)))
        for n, z in enumerate(self.data):
            h = int(round(32768.0 + (z - z0) * k))
            out[n] = 0 if h < 0 else 65535 if h > 65535 else h
        return out

    def to_b64(self) -> str:
        a = self.to_u16()
        if a.itemsize != 2:
            raise RuntimeError("unexpected uint16 size")
        if struct.pack("=H", 1) != struct.pack("<H", 1):
            a.byteswap()
        return base64.b64encode(a.tobytes()).decode("ascii")

    @classmethod
    def from_b64(cls, b64: str, width: int, height: int, origin, scale, yaw: float = 0.0) -> Field:
        raw = base64.b64decode(b64)
        if len(raw) != 2 * width * height:
            raise ValueError(f"heights: expected {width * height} uint16 values, got {len(raw) // 2}")
        a = array("H")
        a.frombytes(raw)
        if struct.pack("=H", 1) != struct.pack("<H", 1):
            a.byteswap()
        k = scale[2] / 128.0
        z0 = float(origin[2])
        return cls(width, height, tuple(origin), tuple(scale), yaw, [z0 + (h - 32768) * k for h in a])

    def clipped(self) -> int:
        """How many vertices fall outside the uint16 height range (they will be clamped)."""
        k = 128.0 / self.scale[2]
        z0 = self.origin[2]
        return sum(1 for z in self.data if not 0 <= 32768.0 + (z - z0) * k <= 65535)

    # ------------------------------------------------------------------ analysis
    def slopes(self) -> list[float]:
        """Slope in degrees at every vertex (central differences; one-sided at the edges)."""
        w, h, d = self.width, self.height, self.data
        sx, sy = self.scale[0], self.scale[1]
        out = [0.0] * (w * h)
        for j in range(h):
            jm, jp = max(j - 1, 0), min(j + 1, h - 1)
            dy_span = (jp - jm) * sy or 1.0
            row, rm, rp = j * w, jm * w, jp * w
            for i in range(w):
                im, ip = max(i - 1, 0), min(i + 1, w - 1)
                gx = (d[row + ip] - d[row + im]) / ((ip - im) * sx or 1.0)
                gy = (d[rp + i] - d[rm + i]) / dy_span
                out[row + i] = math.degrees(math.atan(math.sqrt(gx * gx + gy * gy)))
        return out

    def stats(self) -> dict[str, Any]:
        sl = sorted(self.slopes())
        n = len(sl)
        return {
            "resolution": [self.width, self.height],
            "size_cm": [round(v, 1) for v in self.size_cm],
            "height_world_cm": {"min": round(min(self.data), 1), "max": round(max(self.data), 1),
                                "mean": round(sum(self.data) / n, 1)},
            "slope_deg": {"mean": round(sum(sl) / n, 2), "p50": round(sl[n // 2], 2),
                          "p90": round(sl[min(n - 1, int(n * 0.9))], 2), "max": round(sl[-1], 2)},
        }


def grid_size(component_count, section_size: int, sections_per_component: int) -> tuple[int, int]:
    if section_size not in VALID_SECTION_SIZES:
        raise ValueError(f"section_size must be one of {VALID_SECTION_SIZES}, got {section_size}")
    if sections_per_component not in (1, 2):
        raise ValueError("sections_per_component must be 1 or 2")
    cx, cy = (component_count, component_count) if isinstance(component_count, int) else component_count
    if not (1 <= int(cx) <= 32 and 1 <= int(cy) <= 32):
        raise ValueError("component_count must be 1..32 per axis")
    q = section_size * sections_per_component
    return int(cx) * q + 1, int(cy) * q + 1


# ====================================================================== noise
class _Perlin:
    def __init__(self, seed: int):
        p = list(range(256))
        random.Random(seed).shuffle(p)
        self.p = p + p

    @staticmethod
    def _grad(h: int, x: float, y: float) -> float:
        h &= 7
        u, v = (x, y) if h < 4 else (y, x)
        return (-u if h & 1 else u) + (-2.0 * v if h & 2 else 2.0 * v)

    def __call__(self, x: float, y: float) -> float:
        xi, yi = math.floor(x), math.floor(y)
        xf, yf = x - xi, y - yi
        xi &= 255
        yi &= 255
        u = xf * xf * xf * (xf * (xf * 6 - 15) + 10)
        v = yf * yf * yf * (yf * (yf * 6 - 15) + 10)
        p, g = self.p, self._grad
        aa, ab = p[p[xi] + yi], p[p[xi] + yi + 1]
        ba, bb = p[p[xi + 1] + yi], p[p[xi + 1] + yi + 1]
        x1 = g(aa, xf, yf) + u * (g(ba, xf - 1, yf) - g(aa, xf, yf))
        x2 = g(ab, xf, yf - 1) + u * (g(bb, xf - 1, yf - 1) - g(ab, xf, yf - 1))
        return (x1 + v * (x2 - x1)) * 0.5  # ~[-1, 1]


def _fbm(f: Field, op: dict[str, Any]) -> list[float]:
    amplitude = float(op.get("amplitude", 1000.0))
    wavelength = float(op.get("wavelength", 0.25 * max(f.size_cm) or 10000.0))
    octaves = int(op.get("octaves", 4))
    persistence = float(op.get("persistence", 0.5))
    lacunarity = float(op.get("lacunarity", 2.0))
    ridged = bool(op.get("ridged", False))
    if wavelength <= 0 or not 1 <= octaves <= 10:
        raise ValueError("noise needs wavelength > 0 and 1..10 octaves")
    noise = _Perlin(int(op.get("seed", 0)))
    norm = sum(persistence ** o for o in range(octaves))
    sx, sy = f.scale[0], f.scale[1]
    out = [0.0] * (f.width * f.height)
    off = random.Random(int(op.get("seed", 0)) + 1).random() * 1000.0
    for j in range(f.height):
        for i in range(f.width):
            x, y = i * sx / wavelength + off, j * sy / wavelength + off
            total, amp, freq = 0.0, 1.0, 1.0
            for _ in range(octaves):
                n = noise(x * freq, y * freq)
                if ridged:
                    n = 1.0 - 2.0 * abs(n)
                total += n * amp
                amp *= persistence
                freq *= lacunarity
            out[j * f.width + i] = total / norm * amplitude
    return out


def _combine(f: Field, values: list[float], mode: str):
    d = f.data
    if mode == "add":
        for n, v in enumerate(values):
            d[n] += v
    elif mode == "set":
        z0 = f.origin[2]
        for n, v in enumerate(values):
            d[n] = z0 + v
    elif mode == "max":
        z0 = f.origin[2]
        for n, v in enumerate(values):
            d[n] = max(d[n], z0 + v)
    elif mode == "min":
        z0 = f.origin[2]
        for n, v in enumerate(values):
            d[n] = min(d[n], z0 + v)
    else:
        raise ValueError(f"unknown mode {mode!r}; expected add, set, max or min")


def _smoothstep(t: float) -> float:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


# ====================================================================== sculpt ops
def op_flat(f: Field, op):
    z = float(op.get("height", f.origin[2]))
    f.data = [z] * len(f.data)


def op_noise(f: Field, op):
    _combine(f, _fbm(f, op), op.get("mode", "add"))


def op_hill(f: Field, op):
    """Gaussian/cone/dome bump (negative height = valley/crater) of `radius` cm at center."""
    ci, cj = f.point(op)
    radius = float(op.get("radius", 0.25 * min(f.size_cm)))
    height = float(op.get("height", 1000.0))
    shape = op.get("shape", "gaussian")
    if radius <= 0:
        raise ValueError("radius must be > 0")
    sx, sy = f.scale[0], f.scale[1]
    for j in range(f.height):
        dy = (j - cj) * sy
        for i in range(f.width):
            r = math.hypot((i - ci) * sx, dy) / radius
            if shape == "gaussian":
                k = math.exp(-4.0 * r * r) if r < 2.5 else 0.0
            elif shape == "cone":
                k = max(0.0, 1.0 - r)
            elif shape == "dome":
                k = math.sqrt(max(0.0, 1.0 - r * r))
            else:
                raise ValueError(f"unknown hill shape {shape!r}; expected gaussian, cone or dome")
            f.data[j * f.width + i] += height * k


def op_ramp(f: Field, op):
    """Linear slope from `start` (height_start) to `end` (height_end); add (default) or set."""
    si, sj = f.point(op, "start", (0.0, 0.5))
    ei, ej = f.point(op, "end", (1.0, 0.5))
    h0, h1 = float(op.get("height_start", 0.0)), float(op.get("height_end", 1000.0))
    sx, sy = f.scale[0], f.scale[1]
    ax, ay = (ei - si) * sx, (ej - sj) * sy
    length2 = ax * ax + ay * ay
    if length2 <= 0:
        raise ValueError("ramp start and end must differ")
    vals = []
    for j in range(f.height):
        for i in range(f.width):
            t = ((i - si) * sx * ax + (j - sj) * sy * ay) / length2
            t = 0.0 if t < 0 else 1.0 if t > 1 else t
            vals.append(h0 + (h1 - h0) * t)
    _combine(f, vals, op.get("mode", "add"))


def op_flatten(f: Field, op):
    """Pull heights toward `height` (world Z) inside `radius`, blending out over `falloff` cm."""
    ci, cj = f.point(op)
    radius = float(op.get("radius", 0.15 * min(f.size_cm)))
    falloff = float(op.get("falloff", radius * 0.5))
    target = op.get("height")
    if target is None:
        target = f.sample(*f.world_xy(ci, cj))
    target = float(target)
    sx, sy = f.scale[0], f.scale[1]
    for j in range(f.height):
        for i in range(f.width):
            r = math.hypot((i - ci) * sx, (j - cj) * sy)
            k = 1.0 if r <= radius else 1.0 - _smoothstep((r - radius) / falloff) if falloff > 0 else 0.0
            if k > 0:
                n = j * f.width + i
                f.data[n] += (target - f.data[n]) * k


def op_smooth(f: Field, op):
    """Box blur, `iterations` passes of `radius` vertices (separable)."""
    iterations, r = int(op.get("iterations", 1)), int(op.get("radius", 1))
    if not (1 <= iterations <= 50 and 1 <= r <= 16):
        raise ValueError("smooth: iterations 1..50, radius 1..16")
    w, h = f.width, f.height
    for _ in range(iterations):
        d = f.data
        tmp = [0.0] * (w * h)
        for j in range(h):
            row = j * w
            for i in range(w):
                lo, hi = max(0, i - r), min(w - 1, i + r)
                tmp[row + i] = sum(d[row + lo:row + hi + 1]) / (hi - lo + 1)
        out = [0.0] * (w * h)
        for j in range(h):
            lo, hi = max(0, j - r), min(h - 1, j + r)
            n = hi - lo + 1
            for i in range(w):
                out[j * w + i] = sum(tmp[k * w + i] for k in range(lo, hi + 1)) / n
        f.data = out


def op_erode(f: Field, op):
    """Thermal erosion: material slides downhill wherever the slope exceeds `talus_deg`."""
    iterations = int(op.get("iterations", 10))
    talus = math.tan(math.radians(float(op.get("talus_deg", 35.0))))
    strength = float(op.get("strength", 0.5))
    if not (1 <= iterations <= 200 and 0 < strength <= 1):
        raise ValueError("erode: iterations 1..200, strength (0, 1]")
    w, h = f.width, f.height
    sx, sy = f.scale[0], f.scale[1]
    nbrs = ((1, 0, sx), (-1, 0, sx), (0, 1, sy), (0, -1, sy))
    for _ in range(iterations):
        d = f.data
        delta = [0.0] * (w * h)
        for j in range(h):
            for i in range(w):
                n = j * w + i
                z = d[n]
                moves = []
                total = 0.0
                for di, dj, dist in nbrs:
                    ii, jj = i + di, j + dj
                    if 0 <= ii < w and 0 <= jj < h:
                        excess = (z - d[jj * w + ii]) - talus * dist
                        if excess > 0:
                            moves.append((jj * w + ii, excess))
                            total += excess
                if total > 0:
                    amount = strength * 0.25 * max(e for _, e in moves)
                    for m, e in moves:
                        share = amount * e / total
                        delta[m] += share
                        delta[n] -= share
        f.data = [a + b for a, b in zip(d, delta, strict=True)]


def op_terrace(f: Field, op):
    step = float(op.get("step", 500.0))
    sharp = float(op.get("sharpness", 0.8))
    if step <= 0:
        raise ValueError("terrace step must be > 0")
    z0 = f.origin[2]
    out = []
    for z in f.data:
        rel = (z - z0) / step
        base = math.floor(rel)
        t = rel - base
        t = t if sharp <= 0 else (1 - sharp) * t + sharp * _smoothstep((t - 0.5) * 4 + 0.5)
        out.append(z0 + (base + t) * step)
    f.data = out


def op_clamp(f: Field, op):
    lo = float(op["min"]) if op.get("min") is not None else -math.inf
    hi = float(op["max"]) if op.get("max") is not None else math.inf
    f.data = [lo if z < lo else hi if z > hi else z for z in f.data]


def op_scale(f: Field, op):
    k = float(op.get("factor", 1.0))
    pivot = float(op.get("pivot", f.origin[2]))
    f.data = [pivot + (z - pivot) * k for z in f.data]


def op_offset(f: Field, op):
    v = float(op.get("value", 0.0))
    f.data = [z + v for z in f.data]


def op_import(f: Field, op):
    """Heightmap file on the MCP server's disk: PNG (8/16-bit) or RAW .r16 (uint16 LE)."""
    img, iw, ih = load_heightmap(op["path"], op.get("raw_size"))
    if op.get("flip_y"):
        img = [v for j in range(ih - 1, -1, -1) for v in img[j * iw:(j + 1) * iw]]
    lo, hi = float(op.get("min_height", f.origin[2] - 2000.0)), float(op.get("max_height", f.origin[2] + 2000.0))
    vals = resample(img, iw, ih, f.width, f.height)
    mode = op.get("mode", "set")
    z0 = f.origin[2]
    if mode == "set":
        f.data = [lo + v * (hi - lo) for v in vals]
    else:
        _combine(f, [lo + v * (hi - lo) - (z0 if mode in ("max", "min") else 0) for v in vals]
                 if mode != "add" else [v * (hi - lo) for v in vals], mode)


SCULPT_OPS = {"flat": op_flat, "noise": op_noise, "hill": op_hill, "ramp": op_ramp, "flatten": op_flatten,
              "smooth": op_smooth, "erode": op_erode, "terrace": op_terrace, "clamp": op_clamp,
              "scale": op_scale, "offset": op_offset, "import_heightmap": op_import}


def sculpt(f: Field, op: dict[str, Any]) -> None:
    fn = SCULPT_OPS.get(op.get("op"))
    if fn is None:
        raise ValueError(f"unknown sculpt op {op.get('op')!r}")
    fn(f, op)


# ====================================================================== heightmap files
def resample(img: list[float], iw: int, ih: int, w: int, h: int) -> list[float]:
    """Bilinear resample of a row-major float image to w x h (corners map to corners)."""
    if (iw, ih) == (w, h):
        return list(img)
    out = []
    for j in range(h):
        y = j * (ih - 1) / max(h - 1, 1)
        y0 = int(y)
        y1 = min(y0 + 1, ih - 1)
        fy = y - y0
        for i in range(w):
            x = i * (iw - 1) / max(w - 1, 1)
            x0 = int(x)
            x1 = min(x0 + 1, iw - 1)
            fx = x - x0
            top = img[y0 * iw + x0] * (1 - fx) + img[y0 * iw + x1] * fx
            bot = img[y1 * iw + x0] * (1 - fx) + img[y1 * iw + x1] * fx
            out.append(top * (1 - fy) + bot * fy)
    return out


def load_heightmap(path: str, raw_size=None) -> tuple[list[float], int, int]:
    """-> (values 0..1 row-major, width, height). PNG gray/RGB(A) 8/16-bit, or RAW uint16 LE."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"heightmap {path!r} not found on the MCP server machine")
    raw = p.read_bytes()
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return decode_png(raw)
    n = len(raw) // 2
    if raw_size:
        w, h = int(raw_size[0]), int(raw_size[1])
    else:
        w = h = int(round(math.sqrt(n)))
    if w * h != n:
        raise ValueError(f"RAW heightmap has {n} samples; pass raw_size [w, h] (not square)")
    a = array("H")
    a.frombytes(raw[:2 * n])
    if struct.pack("=H", 1) != struct.pack("<H", 1):
        a.byteswap()
    return [v / 65535.0 for v in a], w, h


def decode_png(raw: bytes) -> tuple[list[float], int, int]:
    """Minimal PNG decoder: non-interlaced gray / gray+alpha / RGB / RGBA, 8 or 16 bit. Returns channel 0."""
    pos, idat = 8, []
    width = height = depth = ctype = None
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        kind = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
            if interlace:
                raise ValueError("interlaced PNGs are not supported; re-save without interlacing")
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if width is None:
        raise ValueError("PNG has no IHDR")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if channels is None or depth not in (8, 16):
        raise ValueError(f"unsupported PNG (color type {ctype}, bit depth {depth}); use 8/16-bit gray or RGB")
    bpp = channels * depth // 8
    stride = width * bpp
    data = zlib.decompress(b"".join(idat))
    out: list[float] = []
    prev = bytearray(stride)
    p = 0
    maxv = 65535.0 if depth == 16 else 255.0
    for _ in range(height):
        ftype = data[p]
        line = bytearray(data[p + 1:p + 1 + stride])
        p += 1 + stride
        if ftype == 1:
            for k in range(bpp, stride):
                line[k] = (line[k] + line[k - bpp]) & 255
        elif ftype == 2:
            for k in range(stride):
                line[k] = (line[k] + prev[k]) & 255
        elif ftype == 3:
            for k in range(stride):
                left = line[k - bpp] if k >= bpp else 0
                line[k] = (line[k] + ((left + prev[k]) >> 1)) & 255
        elif ftype == 4:
            for k in range(stride):
                a = line[k - bpp] if k >= bpp else 0
                b = prev[k]
                c = prev[k - bpp] if k >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[k] = (line[k] + pr) & 255
        elif ftype != 0:
            raise ValueError(f"corrupt PNG (filter type {ftype})")
        if depth == 16:
            out.extend(((line[k] << 8) | line[k + 1]) / maxv for k in range(0, stride, bpp))
        else:
            out.extend(line[k] / maxv for k in range(0, stride, bpp))
        prev = line
    return out, width, height


def encode_png_gray16(values: list[float], w: int, h: int) -> bytes:
    """values 0..1 -> 16-bit grayscale PNG (used by tests and for exporting)."""
    rows = b"".join(b"\x00" + b"".join(struct.pack(">H", max(0, min(65535, int(round(v * 65535)))))
                                         for v in values[j * w:(j + 1) * w]) for j in range(h))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


# ====================================================================== auto paint
def _band(value: float, lo, hi, falloff: float) -> float:
    """1 inside [lo, hi], smooth falloff outside (width `falloff`), 0 beyond."""
    k = 1.0
    if lo is not None and value < lo:
        k = 1.0 - _smoothstep((lo - value) / falloff) if falloff > 0 else 0.0
    if hi is not None and value > hi:
        k = min(k, 1.0 - _smoothstep((value - hi) / falloff) if falloff > 0 else 0.0)
    return k


def paint_weights(f: Field, rules: list[dict[str, Any]], existing_layers=()) -> dict[str, list[int]]:
    """Layered auto-paint. Rules apply in order; each covers what came before by its mask:
    {"layer":"Grass"} (no conditions = fill), {"layer":"Rock","slope_min":30,"slope_falloff":5},
    {"layer":"Snow","height_min":2500,"height_falloff":300,"strength":1}. Heights are world Z cm, slopes degrees.
    Returns uint8 weights per layer that sum to 255 at every vertex (layers in existing_layers that no
    rule paints are returned as zeros so they don't keep stale weight)."""
    if not rules:
        raise ValueError("paint needs at least one rule")
    n = f.width * f.height
    needs_slope = any(r.get("slope_min") is not None or r.get("slope_max") is not None for r in rules)
    slopes = f.slopes() if needs_slope else None
    weights: dict[str, list[float]] = {}
    for r in rules:
        layer = r.get("layer")
        if not layer:
            raise ValueError("every paint rule needs a layer")
        strength = float(r.get("strength", 1.0))
        sf, hf = float(r.get("slope_falloff", 3.0)), float(r.get("height_falloff", 100.0))
        noise = None
        if r.get("noise"):
            nz = dict(r["noise"])
            nz.setdefault("amplitude", 0.5)
            noise = _fbm(f, nz)
        w = weights.setdefault(layer, [0.0] * n)
        for k in range(n):
            m = strength
            if slopes is not None and (r.get("slope_min") is not None or r.get("slope_max") is not None):
                m *= _band(slopes[k], r.get("slope_min"), r.get("slope_max"), sf)
            if r.get("height_min") is not None or r.get("height_max") is not None:
                m *= _band(f.data[k], r.get("height_min"), r.get("height_max"), hf)
            if noise is not None:
                m = min(1.0, max(0.0, m + noise[k]))
            if m <= 0:
                continue
            keep = 1.0 - m
            for other in weights.values():
                other[k] *= keep
            w[k] += m
    for layer in existing_layers:
        weights.setdefault(layer, [0.0] * n)
    names = list(weights)
    out = {name: [0] * n for name in names}
    for k in range(n):
        vals = [weights[name][k] for name in names]
        total = sum(vals)
        if total <= 0:
            out[names[0]][k] = 255  # nothing painted here: base layer
            continue
        ints = [int(v / total * 255) for v in vals]
        ints[max(range(len(ints)), key=lambda q: vals[q])] += 255 - sum(ints)
        for name, v in zip(names, ints, strict=True):
            out[name][k] = v
    return out


def weights_b64(values: list[int]) -> str:
    return base64.b64encode(bytes(values)).decode("ascii")


def coverage(values: list[int]) -> float:
    return round(sum(values) / (255.0 * max(1, len(values))), 4)
