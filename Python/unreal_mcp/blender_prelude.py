# Runs INSIDE Blender (bpy). Prepended to every blender_run script; not imported by the server.
# argv after "--": <result.json> <out_dir> <args.json> <user_code.py>
# ruff: noqa: E402
import json as _json
import math
import sys as _sys
import traceback as _tb
from pathlib import Path as _Path

import bpy

_argv = _sys.argv[_sys.argv.index("--") + 1:]
_RESULT_FILE, OUT_DIR = _Path(_argv[0]), _Path(_argv[1])
ARGS = _json.loads(_Path(_argv[2]).read_text(encoding="utf-8"))
_USER_CODE = _Path(_argv[3]).read_text(encoding="utf-8")
OUT_DIR.mkdir(parents=True, exist_ok=True)
_EXPORTS, _PREVIEWS = [], []


def clear_scene():
    """Start from an empty scene (no default cube/camera/light)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def primitive(kind, name=None, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), **kw):
    """kind: cube, uv_sphere, ico_sphere, cylinder, cone, torus, plane, circle, monkey. kw go to
    bpy.ops.mesh.primitive_<kind>_add (size, radius, depth, vertices, segments, ...). Units: metres."""
    getattr(bpy.ops.mesh, f"primitive_{kind}_add")(location=location, rotation=rotation, **kw)
    obj = bpy.context.active_object
    obj.scale = scale
    if name:
        obj.name = obj.data.name = name
    return obj


def mesh_from_data(name, verts, faces, location=(0, 0, 0)):
    """Build a mesh from raw vertices [(x,y,z),...] and faces [(i,j,k[,l...]),...]."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    me.update()
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return _activate(obj)


def modifier(obj, kind, apply=True, **props):
    """Add (and by default apply) a modifier: modifier(o, "BEVEL", width=0.02, segments=3),
    "SUBSURF" levels=2, "SOLIDIFY" thickness=0.05, "DECIMATE" ratio=0.5, "ARRAY" count=4, "MIRROR"..."""
    m = obj.modifiers.new(name=kind.title(), type=kind.upper())
    for k, v in props.items():
        setattr(m, k, v)
    if apply:
        _activate(obj)
        bpy.ops.object.modifier_apply(modifier=m.name)
    return m


def material(obj, name, color=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.5,
             emission=None, emission_strength=1.0):
    """Assign a Principled BSDF material (becomes a material slot in Unreal)."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    color = tuple(color) + (1.0,) * (4 - len(color))
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission is not None:
        em = tuple(emission) + (1.0,) * (4 - len(emission))
        bsdf.inputs["Emission Color"].default_value = em
        bsdf.inputs["Emission Strength"].default_value = emission_strength
    mat.diffuse_color = color
    obj.data.materials.append(mat)
    return mat


def smart_uv(obj, angle=66.0, margin=0.02):
    """Unwrap with Smart UV Project (Unreal needs UVs for textures and lightmaps)."""
    _activate(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(angle), island_margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")


def shade_smooth(obj, auto_angle=30.0):
    _activate(obj)
    try:
        bpy.ops.object.shade_auto_smooth(angle=math.radians(auto_angle))
    except Exception:
        bpy.ops.object.shade_smooth()


def apply_transforms(obj, location=False, rotation=True, scale=True):
    _activate(obj)
    bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)


def origin_to_bottom(obj):
    """Pivot at the bottom centre and object at the world origin: places cleanly on floors in Unreal."""
    apply_transforms(obj, location=False)
    me = obj.data
    if not me.vertices:
        return
    xs, ys, zs = zip(*(v.co[:] for v in me.vertices), strict=True)
    off = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs))
    for v in me.vertices:
        v.co.x -= off[0]
        v.co.y -= off[1]
        v.co.z -= off[2]
    me.update()
    obj.location = (0, 0, 0)


def join(objs, name):
    """Merge objects into one mesh (one Unreal static mesh)."""
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    obj = bpy.context.active_object
    obj.name = obj.data.name = name
    return obj


def mesh_objects():
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def triangles(objs=None):
    """Triangle count after modifiers, per object."""
    dg = bpy.context.evaluated_depsgraph_get()
    out = {}
    for o in objs or mesh_objects():
        me = o.evaluated_get(dg).to_mesh()
        me.calc_loop_triangles()
        out[o.name] = len(me.loop_triangles)
        o.evaluated_get(dg).to_mesh_clear()
    return out


def bounds(objs=None):
    """World-space AABB in metres: {"min":[..], "max":[..], "size":[..]}."""
    from mathutils import Vector
    bpy.context.view_layer.update()  # bound_box is stale after direct mesh edits
    pts = [o.matrix_world @ Vector(c) for o in (objs or mesh_objects()) for c in o.bound_box]
    if not pts:
        return {"min": [0, 0, 0], "max": [0, 0, 0], "size": [0, 0, 0]}
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return {"min": lo, "max": hi, "size": [b - a for a, b in zip(lo, hi, strict=True)]}


def export(name, fmt="fbx", objs=None):
    """Export objects (default: every mesh) to OUT_DIR/<name>.<fmt>. fmt: fbx | glb | gltf | obj.
    The Unreal asset is named after the file (prefix it: SM_Crate). Returns the file path."""
    fmt = fmt.lower()
    objs = objs or mesh_objects()
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]
    path = OUT_DIR / f"{name}.{fmt}"
    if fmt == "fbx":
        bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, mesh_smooth_type="FACE",
                                 add_leaf_bones=False, use_tspace=True, bake_anim=False)
    elif fmt in ("glb", "gltf"):
        bpy.ops.export_scene.gltf(filepath=str(path), use_selection=True,
                                  export_format="GLB" if fmt == "glb" else "GLTF_SEPARATE")
    elif fmt == "obj":
        bpy.ops.wm.obj_export(filepath=str(path), export_selected_objects=True)
    else:
        raise ValueError(f"unsupported export format {fmt!r} (fbx, glb, gltf, obj)")
    _EXPORTS.append({"name": name, "format": fmt, "path": str(path), "objects": [o.name for o in objs],
                     "triangles": sum(triangles(objs).values()),
                     "material_slots": sorted({m.name if m else "<empty>" for o in objs for m in o.data.materials})})
    return str(path)


def preview(name="preview", resolution=512, objs=None):
    """Render a 3/4 view PNG of the objects (Workbench, falls back to Cycles CPU) so the agent can
    look at the model before importing. Returns the PNG path."""
    from mathutils import Vector
    scene = bpy.context.scene
    b = bounds(objs)
    centre = Vector([(lo + hi) / 2 for lo, hi in zip(b["min"], b["max"], strict=True)])
    radius = max(Vector(b["size"]).length / 2, 0.01)  # bounding-sphere radius
    cam_data = bpy.data.cameras.new("_mcp_cam")
    cam = bpy.data.objects.new("_mcp_cam", cam_data)
    scene.collection.objects.link(cam)
    distance = radius / math.sin(cam_data.angle / 2) * 1.1
    cam.location = centre + Vector((1.0, -1.0, 0.75)).normalized() * distance
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_end = max(1000.0, radius * 20)
    scene.camera = cam
    scene.render.resolution_x = scene.render.resolution_y = resolution
    scene.render.image_settings.file_format = "PNG"
    path = OUT_DIR / f"{name}.png"
    scene.render.filepath = str(path)
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "MATERIAL"
        bpy.ops.render.render(write_still=True)
    except Exception:
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 16
        bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam)
    _PREVIEWS.append(str(path))
    return str(path)


_NS = {k: v for k, v in globals().items() if not k.startswith("_")}
_res = {"ok": True}
try:
    exec(compile(_USER_CODE, "<blender_run>", "exec"), _NS)
    _res["result"] = _NS.get("result")
except BaseException:
    _res["ok"] = False
    _res["traceback"] = _tb.format_exc()
_res["exports"] = _EXPORTS
_res["previews"] = _PREVIEWS
try:
    _RESULT_FILE.write_text(_json.dumps(_res, default=repr))
except Exception as _e:
    _RESULT_FILE.write_text(_json.dumps({"ok": False, "traceback": f"result not serializable: {_e!r}",
                                         "exports": _EXPORTS, "previews": _PREVIEWS}))
