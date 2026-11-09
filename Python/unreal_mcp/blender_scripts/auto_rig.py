# Fit an armature to a mesh and skin it with automatic (heat) weights, then export a skeletal mesh for Unreal.
# Humanoid bones use the UE5 Mannequin names (root, pelvis, spine_01..05, neck_01/02, head, clavicle/upperarm/
# lowerarm/hand, thigh/calf/foot/ball _l/_r), so IK Rig auto-characterisation and the retargeter map them.
# The mesh must face -Y (Blender front view; image-to-3D output does after glTF import), left side at +X.
# ARGS: input, name, type humanoid|quadruped|prop, height_cm, rotate [x,y,z deg], format glb|fbx, preview,
#       fbx_options (overrides for bpy.ops.export_scene.fbx). GLB is the default: UE 5.8 imports Blender's FBX
#       armature with a 100x scale and a 90 degree rotation on the root bone (every apply_scale/bake variant).
# ruff: noqa: F821
_A = ARGS
_name = _A.get("name") or "SKM_Rigged"
_type = (_A.get("type") or "humanoid").lower()
body = join_all(import_any(_A["input"]), _name)
rotate(body, _A.get("rotate"))
scale_to_height(body, float(_A.get("height_cm") or 0))
origin_to_bottom(body)
V = [v.co.copy() for v in body.data.vertices]
H = max(v.z for v in V)
warnings = []


def _band(z0, z1, pred=lambda v: True):
    return [v for v in V if z0 * H <= v.z <= z1 * H and pred(v)]


def _median(xs, default=0.0):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else default


def _mid(xs, default=0.0):
    """Centre of the extent: stable on low-poly meshes where vertices sit only at the corners."""
    xs = list(xs)
    return (min(xs) + max(xs)) / 2 if xs else default


_bvh = None


def _torso_half_width(z, y=0.0):
    """Cast from inside the body towards +X and -X: the first hit is the torso's side wall (below the arms)."""
    global _bvh
    if _bvh is None:
        from mathutils.bvhtree import BVHTree
        _bvh = BVHTree.FromPolygons([v.co.copy() for v in body.data.vertices], [p.vertices[:] for p in body.data.polygons])
    hits = []
    for d in (1, -1):
        loc, *_ = _bvh.ray_cast(Vector((0.0, y, z * H)), Vector((d, 0.0, 0.0)))
        if loc is not None:
            hits.append(abs(loc.x))
    return min(hits) if hits else 0.12 * H


def humanoid_joints():
    hw = _torso_half_width(0.70, _mid(v.y for v in _band(0.68, 0.72)))
    up = [v for v in V if v.z > 0.35 * H]
    tip_l, tip_r = max(up, key=lambda v: v.x), min(up, key=lambda v: v.x)
    spread = tip_l.x > hw + 0.12 * H and -tip_r.x > hw + 0.12 * H
    if not spread:
        warnings.append("arms not spread away from the body (T or A pose expected): arm weights may bleed "
                        "into the torso; pose the source in a T/A pose for clean results")
    j = {}

    def cy(z0, z1):
        return _mid((v.y for v in _band(z0, z1, lambda v: abs(v.x) < hw)), 0.0)

    for bone, z in (("pelvis", 0.53), ("spine_01", 0.57), ("spine_02", 0.61), ("spine_03", 0.65),
                    ("spine_04", 0.70), ("spine_05", 0.75), ("neck_01", 0.82), ("neck_02", 0.85), ("head", 0.875)):
        j[bone] = Vector((0.0, cy(z - 0.02, z + 0.02), z * H))
    j["head_end"] = Vector((0.0, j["head"].y, H))
    for side, tip, sgn in (("l", tip_l, 1), ("r", tip_r, -1)):
        z_sh = tip.z if spread and tip.z > 0.70 * H else 0.80 * H
        sh = Vector((sgn * max(hw * 0.85, 0.06 * H), j["spine_05"].y, z_sh))
        if not spread:
            tip = Vector((sgn * (hw + 0.05 * H), sh.y, 0.47 * H))
        j[f"clavicle_{side}"] = Vector((sgn * 0.02 * H, j["spine_05"].y, z_sh - 0.02 * H))
        j[f"upperarm_{side}"] = sh
        j[f"lowerarm_{side}"] = sh.lerp(tip, 0.42) + Vector((0, 0.01 * H, 0))  # elbow bends backwards
        j[f"hand_{side}"] = sh.lerp(tip, 0.76)
        j[f"hand_{side}_end"] = sh.lerp(tip, 0.95)
        leg = _band(0.12, 0.35, lambda v, s=sgn: s * v.x > 0.005 * H)
        lx = _mid((v.x for v in leg), sgn * 0.09 * H)
        ly = _mid((v.y for v in leg), 0.0)
        feet = [v for v in V if v.z < 0.07 * H and sgn * v.x > 0]
        toe_y = min((v.y for v in feet), default=ly - 0.1 * H)
        j[f"thigh_{side}"] = Vector((lx * 0.9, ly, 0.50 * H))
        j[f"calf_{side}"] = Vector((lx, ly - 0.01 * H, 0.275 * H))
        j[f"foot_{side}"] = Vector((lx, ly, 0.045 * H))
        j[f"ball_{side}"] = Vector((lx, ly + 0.72 * (toe_y - ly), 0.012 * H))
        j[f"ball_{side}_end"] = Vector((lx, toe_y, 0.012 * H))
    return j, [
        ("root", None, Vector((0, 0, 0)), Vector((0, 0, 0.08 * H))),
        ("pelvis", "root", j["pelvis"], j["spine_01"]),
        ("spine_01", "pelvis", j["spine_01"], j["spine_02"]),
        ("spine_02", "spine_01", j["spine_02"], j["spine_03"]),
        ("spine_03", "spine_02", j["spine_03"], j["spine_04"]),
        ("spine_04", "spine_03", j["spine_04"], j["spine_05"]),
        ("spine_05", "spine_04", j["spine_05"], j["neck_01"]),
        ("neck_01", "spine_05", j["neck_01"], j["neck_02"]),
        ("neck_02", "neck_01", j["neck_02"], j["head"]),
        ("head", "neck_02", j["head"], j["head_end"]),
        *[b for s in ("l", "r") for b in (
            (f"clavicle_{s}", "spine_05", j[f"clavicle_{s}"], j[f"upperarm_{s}"]),
            (f"upperarm_{s}", f"clavicle_{s}", j[f"upperarm_{s}"], j[f"lowerarm_{s}"]),
            (f"lowerarm_{s}", f"upperarm_{s}", j[f"lowerarm_{s}"], j[f"hand_{s}"]),
            (f"hand_{s}", f"lowerarm_{s}", j[f"hand_{s}"], j[f"hand_{s}_end"]),
            (f"thigh_{s}", "pelvis", j[f"thigh_{s}"], j[f"calf_{s}"]),
            (f"calf_{s}", f"thigh_{s}", j[f"calf_{s}"], j[f"foot_{s}"]),
            (f"foot_{s}", f"calf_{s}", j[f"foot_{s}"], j[f"ball_{s}"]),
            (f"ball_{s}", f"foot_{s}", j[f"ball_{s}"], j[f"ball_{s}_end"]))],
    ]


def quadruped_joints():
    b = bounds([body])
    if b["size"][0] > 1.2 * b["size"][1]:
        warnings.append("body was longer along X: rotated 90 degrees so it runs along Y (head towards -Y); "
                        "pass rotate=[0,0,180] if the head ends up at the back")
        body.data.transform(Matrix.Rotation(math.radians(90), 4, "Z"))
        body.data.update()
        V[:] = [v.co.copy() for v in body.data.vertices]
    ys = [v.y for v in V]
    y_front, y_back = min(ys), max(ys)
    feet = [v for v in V if v.z < 0.12 * H]
    ymid = _median([v.y for v in feet], (y_front + y_back) / 2)
    legs = {}
    for name, pred in (("front_l", lambda v: v.x > 0 and v.y < ymid), ("front_r", lambda v: v.x <= 0 and v.y < ymid),
                       ("rear_l", lambda v: v.x > 0 and v.y >= ymid), ("rear_r", lambda v: v.x <= 0 and v.y >= ymid)):
        pts = [v for v in feet if pred(v)] or feet
        legs[name] = Vector((_median([v.x for v in pts]), _median([v.y for v in pts]), 0.0))
    z_sp = 0.65 * H
    y_sh = (legs["front_l"].y + legs["front_r"].y) / 2
    y_hip = (legs["rear_l"].y + legs["rear_r"].y) / 2
    pelvis, chest = Vector((0, y_hip, z_sp)), Vector((0, y_sh, z_sp))
    head_tip = min(V, key=lambda v: v.y - 0.3 * v.z)  # front-most, preferring high points
    neck = chest + Vector((0, -0.1 * H, 0.15 * H))
    bones = [("root", None, Vector((0, 0, 0)), Vector((0, 0, 0.08 * H))),
             ("pelvis", "root", pelvis, pelvis.lerp(chest, 0.25)),
             ("spine_01", "pelvis", pelvis.lerp(chest, 0.25), pelvis.lerp(chest, 0.5)),
             ("spine_02", "spine_01", pelvis.lerp(chest, 0.5), pelvis.lerp(chest, 0.75)),
             ("spine_03", "spine_02", pelvis.lerp(chest, 0.75), chest),
             ("neck_01", "spine_03", chest, neck),
             ("head", "neck_01", neck, Vector((0, head_tip.y, max(head_tip.z, neck.z))))]
    if y_back - y_hip > 0.15 * (y_back - y_front):
        tail_end = Vector((0, y_back, _median([v.z for v in V if v.y > y_back - 0.05 * H], z_sp)))
        bones += [("tail_01", "pelvis", pelvis, pelvis.lerp(tail_end, 0.5)),
                  ("tail_02", "tail_01", pelvis.lerp(tail_end, 0.5), tail_end)]
    for side in ("l", "r"):
        for kind, parent, names in (("front", "spine_03", ("upperarm", "lowerarm", "hand")),
                                    ("rear", "pelvis", ("thigh", "calf", "foot"))):
            f = legs[f"{kind}_{side}"]
            top = Vector((f.x, f.y, z_sp * 0.92))
            knee = Vector((f.x, f.y + (0.03 if kind == "front" else -0.03) * H, z_sp * 0.5))
            ankle = Vector((f.x, f.y, 0.1 * H))
            toe = Vector((f.x, f.y - 0.06 * H, 0.0))
            a, b2, c = (f"{n}_{side}" for n in names)
            bones += [(a, parent, top, knee), (b2, a, knee, ankle), (c, b2, ankle, toe)]
    return {"legs": {k: list(v) for k, v in legs.items()}}, bones


def prop_joints():
    return {}, [("root", None, Vector((0, 0, 0)), Vector((0, 0, 0.1 * H))),
                ("body", "root", Vector((0, 0, 0)), Vector((0, 0, H)))]


if _type not in ("humanoid", "quadruped", "prop"):
    raise ValueError("type must be humanoid, quadruped or prop")
joints, BONES = {"humanoid": humanoid_joints, "quadruped": quadruped_joints, "prop": prop_joints}[_type]()

arm_data = bpy.data.armatures.new("Armature")
arm = bpy.data.objects.new("Armature", arm_data)  # UE's FBX importer drops a node named Armature: root stays root
bpy.context.scene.collection.objects.link(arm)
activate(arm)
bpy.ops.object.mode_set(mode="EDIT")
for bname, parent, head, tail in BONES:
    eb = arm_data.edit_bones.new(bname)
    eb.head, eb.tail = head, tail if (tail - head).length > 1e-4 else head + Vector((0, 0, 0.02 * H))
    if parent:
        eb.parent = arm_data.edit_bones[parent]
        eb.use_connect = (eb.parent.tail - eb.head).length < 1e-5
    eb.align_roll(Vector((0, 0, 1)) if abs((tail - head).normalized().z) < 0.9 else Vector((0, -1, 0)))
bpy.ops.object.mode_set(mode="OBJECT")

# automatic (bone heat) weights; root and helpers only carry the hierarchy
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
heat_ok = True
try:
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
except RuntimeError as e:
    heat_ok = False
    warnings.append(f"heat weighting failed ({e}); fell back to nearest-bone weights")
    bpy.ops.object.parent_set(type="ARMATURE_NAME")
if "root" in body.vertex_groups and _type != "prop":
    body.vertex_groups.remove(body.vertex_groups["root"])

deform = [b for b in arm_data.bones if b.name != "root" or _type == "prop"]


def _seg_dist(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-12)))
    return (a + ab * t - p).length


unweighted = [v.index for v in body.data.vertices if not any(g.weight > 1e-4 for g in v.groups)]
if unweighted:  # heat weighting leaves vertices it cannot reach (separate islands, bad normals): nearest bone
    for b in deform:
        if b.name not in body.vertex_groups:
            body.vertex_groups.new(name=b.name)
    for i in unweighted:
        p = body.data.vertices[i].co
        best = min(deform, key=lambda b: _seg_dist(p, b.head_local, b.tail_local))
        body.vertex_groups[best.name].add([i], 1.0, "REPLACE")
if _A.get("preview", True):
    preview(objs=[body], color_type="TEXTURE" if any(n.type == "TEX_IMAGE" for m in body.data.materials if m
                                                      for n in m.node_tree.nodes) else "MATERIAL")

fmt = (_A.get("format") or "glb").lower()
path = OUT_DIR / f"{_name}.{fmt}"
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
if fmt == "fbx":
    # FBX only follows an image node wired straight into the BSDF, and only embeds images that exist on disk
    # (glTF-imported textures are packed in memory, often behind a vertex-colour multiply)
    for m in body.data.materials:
        if not (m and m.use_nodes):
            continue
        bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None and bsdf.inputs["Base Color"].is_linked:
            up = bsdf.inputs["Base Color"].links[0].from_node
            if up.type == "MIX":
                tex = next((s.links[0].from_node for s in up.inputs if s.is_linked
                            and s.links[0].from_node.type == "TEX_IMAGE"), None)
                if tex is not None:
                    m.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    for m in body.data.materials:
        for n in (m.node_tree.nodes if m and m.use_nodes else []):
            if n.type == "TEX_IMAGE" and n.image and n.image.packed_file:
                n.image.filepath_raw = str(OUT_DIR / f"{bpy.path.clean_name(n.image.name)}.png")
                n.image.file_format = "PNG"
                n.image.save()
    fbx_kw = dict(filepath=str(path), use_selection=True, object_types={"ARMATURE", "MESH"},
                  add_leaf_bones=False, bake_anim=False, mesh_smooth_type="FACE",
                  apply_scale_options="FBX_SCALE_ALL", primary_bone_axis="Y", secondary_bone_axis="X",
                  path_mode="COPY", embed_textures=True, use_tspace=True)
    fbx_kw.update(_A.get("fbx_options") or {})
    bpy.ops.export_scene.fbx(**fbx_kw)
elif fmt == "glb":
    bpy.ops.export_scene.gltf(filepath=str(path), use_selection=True, export_format="GLB", export_animations=False,
                              export_skins=True)
else:
    raise ValueError("format must be fbx or glb")
export_row = {"name": _name, "format": fmt, "path": str(path), "objects": [body.name, arm.name],
                "triangles": tri_count(body), "material_slots": [m.name for m in body.data.materials if m]}
groups = {g.name: 0 for g in body.vertex_groups}
for v in body.data.vertices:
    for g in v.groups:
        if g.weight > 1e-4:
            groups[body.vertex_groups[g.group].name] += 1
result = {"type": _type, "bones": [b[0] for b in BONES], "height_cm": round(H * 100, 2), "heat_weights": heat_ok,
          "fallback_weighted_vertices": len(unweighted), "vertices": len(body.data.vertices),
          "vertices_per_bone": groups, "warnings": warnings, "export": export_row,
          "joints_cm": {k: [round(c * 100, 1) for c in v] for k, v in joints.items() if hasattr(v, "__len__")
                        and len(v) == 3 and not isinstance(v, dict)}}
