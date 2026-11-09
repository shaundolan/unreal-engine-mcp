# Blender helpers shared by gen3d_post and auto_rig (prepended after the prelude; bpy, math, ARGS are in scope).
# ruff: noqa: F821
import os as _os

import bmesh
from mathutils import Matrix, Vector

_IMPORTERS = {
    ".glb": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".gltf": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".fbx": lambda p: bpy.ops.import_scene.fbx(filepath=p),
    ".obj": lambda p: bpy.ops.wm.obj_import(filepath=p),
    ".ply": lambda p: bpy.ops.wm.ply_import(filepath=p),
    ".stl": lambda p: bpy.ops.wm.stl_import(filepath=p),
}


def activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def import_any(path):
    """Import a mesh file into an empty scene; returns its mesh objects, unparented with transforms kept."""
    ext = _os.path.splitext(path)[1].lower()
    if ext not in _IMPORTERS:
        raise ValueError(f"unsupported mesh format {ext!r}; one of {sorted(_IMPORTERS)}")
    if not _os.path.isfile(path):
        raise FileNotFoundError(path)
    clear_scene()
    _IMPORTERS[ext](path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise ValueError(f"{path} contains no mesh")
    for o in meshes:
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
    for o in [o for o in bpy.context.scene.objects if o.type != "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)
    return meshes


def join_all(objs, name):
    for o in objs:  # bake transforms into the vertices, so later maths works in world space
        apply_transforms(o, location=True)
    obj = join(objs, name) if len(objs) > 1 else objs[0]
    obj.name = obj.data.name = name
    return obj


def tri_count(obj):
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def diag(obj):
    b = bounds([obj])
    return max(Vector(b["size"]).length, 1e-6)


def remove_islands(obj, min_vert_frac=0.002, max_size_frac=0.05):
    """Drop floaters: islands with few vertices that are also small next to the whole object."""
    me = obj.data
    n = len(me.vertices)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for e in me.edges:
        a, b = find(e.vertices[0]), find(e.vertices[1])
        if a != b:
            parent[a] = b
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    d = diag(obj)
    drop, dropped = [], 0
    for verts in groups.values():
        if len(verts) >= max(4, min_vert_frac * n):
            continue
        cos = [me.vertices[i].co for i in verts]
        size = Vector([max(c[k] for c in cos) - min(c[k] for c in cos) for k in range(3)]).length
        if size < max_size_frac * d:
            drop += verts
            dropped += 1
    if drop:
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.verts[i] for i in drop], context="VERTS")
        bm.to_mesh(me)
        bm.free()
        me.update()
    return {"islands": len(groups), "removed_islands": dropped, "removed_vertices": len(drop)}


def cleanup(obj, merge_frac=1e-4):
    """Merge duplicate vertices, dissolve degenerate faces, drop loose geometry, recalc outward normals."""
    me = obj.data
    v0 = len(me.vertices)
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_frac * diag(obj))
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-7)
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return {"merged_vertices": v0 - len(me.vertices), "loose_removed": len(loose)}


def scale_to_height(obj, height_cm):
    """Uniform scale so the Z extent is height_cm (Blender metres: 1 m = 100 Unreal units)."""
    h = bounds([obj])["size"][2]
    if height_cm and h > 1e-6:
        s = (height_cm / 100.0) / h
        obj.scale = (s, s, s)
        apply_transforms(obj, location=False)
        return s
    return 1.0


def rotate(obj, degrees):
    if degrees and any(degrees):
        rx, ry, rz = (math.radians(float(d)) for d in degrees)
        obj.data.transform(Matrix.Rotation(rz, 4, "Z") @ Matrix.Rotation(ry, 4, "Y") @ Matrix.Rotation(rx, 4, "X"))
        obj.data.update()


def set_pivot(obj, mode):
    """bottom_center (props/characters stand on the floor), center, or keep."""
    if mode == "keep":
        return
    if mode == "bottom_center":
        origin_to_bottom(obj)
        return
    me = obj.data
    xs, ys, zs = zip(*(v.co[:] for v in me.vertices), strict=True)
    off = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
    me.transform(Matrix.Translation(-off))
    me.update()
    obj.location = (0, 0, 0)


def smooth(obj, angle=60.0):
    activate(obj)
    try:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle))
    except (AttributeError, RuntimeError, TypeError):
        bpy.ops.object.shade_smooth()


def describe_materials(obj):
    """What colour the mesh carries: textured UVs, vertex colours or nothing."""
    me = obj.data
    textures, linked = [], {}
    for m in me.materials:
        if not (m and m.use_nodes):
            continue
        for n in m.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image:
                textures.append(n.image.name)
        bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            for k in ("Base Color", "Roughness", "Metallic", "Normal"):
                if bsdf.inputs[k].is_linked:
                    linked[k] = True
    return {"uv_layers": len(me.uv_layers), "color_attributes": [a.name for a in me.color_attributes],
            "textures": sorted(set(textures)), "linked": sorted(linked), "materials": len(me.materials)}
