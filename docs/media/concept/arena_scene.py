# Built only with the blender_run prelude helpers + plain bpy (what an agent gets).
import random

random.seed(7)
clear_scene()
tris = 0

# --- arena floor: 8x8 beveled tiles, two-tone ---------------------------------------------
TILE = 3.0
for i in range(8):
    for j in range(8):
        t = primitive("cube", f"Tile_{i}_{j}", size=1, location=((i - 3.5) * TILE, (j - 3.5) * TILE, -0.15),
                      scale=(TILE * 0.98, TILE * 0.98, 0.3))
        apply_transforms(t)
        modifier(t, "BEVEL", width=0.06, segments=2)
        dark = (i + j) % 2 == 0
        material(t, "M_TileA" if dark else "M_TileB",
                 color=(0.09, 0.1, 0.13) if dark else (0.16, 0.17, 0.2), roughness=0.55)

ground = primitive("plane", "Ground", size=200, location=(0, 0, -0.31))
material(ground, "M_Sand", color=(0.2, 0.13, 0.08), roughness=0.95)

# --- walls with buttresses -----------------------------------------------------------------
HALF = 4 * TILE
for k, (x, y, sx, sy) in enumerate([(0, HALF + 0.5, 2 * HALF + 2, 1), (0, -HALF - 0.5, 2 * HALF + 2, 1),
                                    (HALF + 0.5, 0, 1, 2 * HALF), (-HALF - 0.5, 0, 1, 2 * HALF)]):
    w = primitive("cube", f"Wall_{k}", size=1, location=(x, y, 1.6), scale=(sx, sy, 3.2))
    apply_transforms(w)
    modifier(w, "BEVEL", width=0.08, segments=2)
    material(w, "M_Stone", color=(0.42, 0.3, 0.2), roughness=0.85)
for k in range(12):
    side = k % 4
    a = (k // 4 - 1) * 8.0
    pos = [(a, HALF + 0.3), (a, -HALF - 0.3), (HALF + 0.3, a), (-HALF - 0.3, a)][side]
    b = primitive("cube", f"Buttress_{k}", size=1, location=(pos[0], pos[1], 2.0), scale=(1.4, 1.4, 4.0))
    apply_transforms(b)
    modifier(b, "BEVEL", width=0.1, segments=2)
    material(b, "M_StoneDark", color=(0.26, 0.19, 0.14), roughness=0.9)

# --- central dais with glowing ring ---------------------------------------------------------
for lvl, (r, h) in enumerate([(4.2, 0.35), (3.2, 0.7)]):
    d = primitive("cylinder", f"Dais_{lvl}", radius=r, depth=h, vertices=48, location=(0, 0, h / 2))
    modifier(d, "BEVEL", width=0.05, segments=2)
    material(d, "M_Dais", color=(0.36, 0.37, 0.42), roughness=0.4, metallic=0.3)
ring = primitive("torus", "DaisRing", major_radius=3.25, minor_radius=0.06, location=(0, 0, 0.72))
material(ring, "M_Energy", color=(0.1, 0.6, 1.0), emission=(0.05, 0.45, 1.0), emission_strength=6)

# --- pillars ring with emissive caps + coloured point lights --------------------------------
import math as _m
for k in range(8):
    ang = 2 * _m.pi * k / 8 + _m.pi / 8
    x, y = 8.0 * _m.cos(ang), 8.0 * _m.sin(ang)
    p = primitive("cylinder", f"Pillar_{k}", radius=0.45, depth=4.5, vertices=24, location=(x, y, 2.25))
    modifier(p, "BEVEL", width=0.04, segments=2)
    material(p, "M_Pillar", color=(0.5, 0.42, 0.32), roughness=0.7)
    cap = primitive("cylinder", f"PillarCap_{k}", radius=0.6, depth=0.25, vertices=24, location=(x, y, 4.6))
    material(cap, "M_Cap", color=(1.0, 0.3, 0.03), emission=(1.0, 0.28, 0.02), emission_strength=2.2)
    L = bpy.data.lights.new(f"PillarLight_{k}", "POINT")
    L.energy, L.color, L.shadow_soft_size = 1400, (1.0, 0.55, 0.25), 0.4
    lo = bpy.data.objects.new(f"PillarLight_{k}", L)
    lo.location = (x * 0.92, y * 0.92, 5.2)
    bpy.context.scene.collection.objects.link(lo)

# --- ramp + raised platform in a corner -----------------------------------------------------
plat = primitive("cube", "Platform", size=1, location=(8.5, 8.5, 1.0), scale=(5, 5, 2))
apply_transforms(plat)
modifier(plat, "BEVEL", width=0.08, segments=2)
material(plat, "M_StoneDark", color=(0.22, 0.21, 0.2))
ramp = mesh_from_data("Ramp", [(-1.2, 0, 0), (1.2, 0, 0), (1.2, 5, 2), (-1.2, 5, 2), (-1.2, 5, 0), (1.2, 5, 0)],
                      [(0, 1, 2, 3), (0, 3, 4), (1, 5, 2), (4, 3, 2, 5), (0, 4, 5, 1)], location=(8.5, 1.0, 0))
material(ramp, "M_Dais", color=(0.36, 0.37, 0.42))

# --- crates (the 3d-assets recipe), stacked ---------------------------------------------------
def crate(name, loc, rotz=0.0, s=1.0):
    c = primitive("cube", name, size=s, location=(loc[0], loc[1], loc[2] + s / 2), rotation=(0, 0, rotz))
    modifier(c, "BEVEL", width=0.05 * s, segments=2)
    material(c, "M_Wood", color=(0.42, 0.27, 0.13), roughness=0.8)
    for dz in (0.15, 0.85):
        band = primitive("cube", name + "_band", size=1, location=(loc[0], loc[1], loc[2] + s * dz),
                         rotation=(0, 0, rotz), scale=(s * 1.02, s * 1.02, s * 0.07))
        material(band, "M_Iron", color=(0.25, 0.25, 0.27), metallic=1.0, roughness=0.35)

for k, (x, y, z, rz) in enumerate([(-9.5, -9.0, 0, 0.1), (-8.3, -9.2, 0, -0.2), (-9.0, -9.1, 1.0, 0.35),
                                   (-9.6, -7.7, 0, 0.6), (9.2, -8.8, 0, 0.25), (8.6, 7.0, 2.0, 0.1)]):
    crate(f"Crate_{k}", (x, y, z), rz)

# --- coins: gold, emissive, in an arc ---------------------------------------------------------
for k in range(9):
    ang = -_m.pi * 0.15 + k * 0.17
    c = primitive("cylinder", f"Coin_{k}", radius=0.32, depth=0.08, vertices=32,
                  location=(5.6 * _m.cos(ang), 5.6 * _m.sin(ang) - 1.0, 1.1), rotation=(_m.pi / 2, 0, ang + 0.6))
    modifier(c, "BEVEL", width=0.02, segments=2)
    material(c, "M_Coin", color=(1.0, 0.75, 0.15), metallic=1.0, roughness=0.25,
             emission=(1.0, 0.6, 0.05), emission_strength=2.5)

# --- health pickup: green cross on a pedestal ---------------------------------------------------
ped = primitive("cylinder", "HealthPedestal", radius=0.6, depth=0.5, vertices=32, location=(1.5, -5.6, 0.25))
material(ped, "M_Dais", color=(0.36, 0.37, 0.42))
for sc in ((0.7, 0.22, 0.22), (0.22, 0.22, 0.7)):
    arm = primitive("cube", "HealthCross", size=1, location=(1.5, -5.6, 1.35), scale=sc, rotation=(0, 0, 0.5))
    material(arm, "M_Health", color=(0.1, 1.0, 0.3), emission=(0.05, 1.0, 0.2), emission_strength=4)

# --- characters: player (blue) on the dais, three patrolling enemies (red) ----------------------
def capsule(name, loc, col, eye):
    body = primitive("cylinder", name, radius=0.4, depth=1.0, vertices=24, location=(loc[0], loc[1], loc[2] + 0.9))
    top = primitive("uv_sphere", name + "_t", radius=0.4, location=(loc[0], loc[1], loc[2] + 1.4))
    bot = primitive("uv_sphere", name + "_b", radius=0.4, location=(loc[0], loc[1], loc[2] + 0.4))
    m = join([body, top, bot], name)
    shade_smooth(m)
    material(m, "M_" + name, color=col, roughness=0.35)
    v = primitive("cube", name + "_visor", size=1, location=(loc[0] - 0.24, loc[1] - 0.26, loc[2] + 1.45), scale=(0.15, 0.5, 0.14), rotation=(0, 0, 0.83))
    material(v, "M_Visor" + name, color=eye, emission=eye, emission_strength=1.6)

capsule("Player", (0, 0, 0.7), (0.12, 0.35, 0.95), (0.4, 0.9, 1.0))
for k, (x, y) in enumerate([(-5.5, -4.5), (4.0, -7.0), (-8.0, 1.5)]):
    capsule(f"Enemy_{k}", (x, y, 0), (0.75, 0.1, 0.1), (1.0, 0.2, 0.05))

# --- patrol path markers (the AI waypoints) ----------------------------------------------------
for k, (x, y) in enumerate([(-5.5, -4.5), (-1.0, -8.5), (4.0, -7.0)]):
    wp = primitive("cylinder", f"Waypoint_{k}", radius=0.35, depth=0.03, vertices=24, location=(x, y, 0.02))
    material(wp, "M_Waypoint", color=(1, 0.2, 0.1), emission=(1, 0.2, 0.1), emission_strength=3)

# --- lighting, sky, camera ----------------------------------------------------------------------
sun = bpy.data.lights.new("Sun", "SUN")
sun.energy, sun.angle, sun.color = 2.6, 0.06, (1.0, 0.68, 0.42)
so = bpy.data.objects.new("Sun", sun)
so.rotation_euler = (_m.radians(68), 0, _m.radians(-50))
bpy.context.scene.collection.objects.link(so)

world = bpy.data.worlds.new("Sky")
bpy.context.scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
sky = world.node_tree.nodes.new("ShaderNodeTexSky")
try:
    sky.sky_type = "NISHITA"
    sky.sun_elevation, sky.sun_rotation = _m.radians(6), _m.radians(130)
except Exception:
    pass
grad = world.node_tree.nodes.new("ShaderNodeTexGradient")
ramp_n = world.node_tree.nodes.new("ShaderNodeValToRGB")
texco = world.node_tree.nodes.new("ShaderNodeTexCoord")
sep = world.node_tree.nodes.new("ShaderNodeSeparateXYZ")
world.node_tree.links.new(texco.outputs["Generated"], sep.inputs["Vector"])
world.node_tree.links.new(sep.outputs["Z"], ramp_n.inputs["Fac"])
ramp_n.color_ramp.elements[0].position = 0.0
ramp_n.color_ramp.elements[0].color = (0.9, 0.32, 0.07, 1)
_mid = ramp_n.color_ramp.elements.new(0.06)
_mid.color = (0.32, 0.12, 0.18, 1)
ramp_n.color_ramp.elements[-1].position = 0.22
ramp_n.color_ramp.elements[-1].color = (0.02, 0.035, 0.12, 1)
world.node_tree.links.new(ramp_n.outputs["Color"], bg.inputs["Color"])
bg.inputs["Strength"].default_value = 0.35

cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 26
cam = bpy.data.objects.new("Cam", cam_data)
bpy.context.scene.collection.objects.link(cam)
cam.location = (-15.5, -17.0, 8.2)
from mathutils import Vector as _V
cam.rotation_euler = (_V((1.5, 1.5, 0.4)) - cam.location).to_track_quat("-Z", "Y").to_euler()
scene = bpy.context.scene
scene.camera = cam

scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
device = "CPU"
for backend in ("OPTIX", "CUDA"):
    try:
        prefs.compute_device_type = backend
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            scene.cycles.device = "GPU"
            device = backend
            break
    except Exception:
        continue
scene.cycles.samples = int(ARGS.get("samples", 160))
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.view_settings.view_transform = "Filmic"
scene.view_settings.look = "Medium High Contrast"
scene.view_settings.exposure = -0.3
scene.render.filepath = str(OUT_DIR / "arena.png")
bpy.ops.render.render(write_still=True)

result = {"device": device, "objects": len(mesh_objects()), "triangles": sum(triangles().values()),
          "image": scene.render.filepath}
