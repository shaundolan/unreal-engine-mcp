# Post-process a generated (or any) mesh for Unreal: cleanup, decimate, UVs, PBR bake, scale, pivot, export GLB.
# ARGS: input, name, target_tris, height_cm, rotate [x,y,z deg], pivot bottom_center|center|keep,
#       uv auto|keep|smart, bake auto|always|never, texture_size, normal_map auto|always|never,
#       min_island_frac, smooth_angle, preview
# ruff: noqa: F821
_A = ARGS
_name = _A.get("name") or "SM_Generated"
_parts = import_any(_A["input"])
src = join_all(_parts, _name + "_src")
info = {"input_triangles": tri_count(src), "source": describe_materials(src)}
info["cleanup"] = cleanup(src)
info["floaters"] = remove_islands(src, float(_A.get("min_island_frac", 0.002)))
rotate(src, _A.get("rotate"))
info["scale_factor"] = scale_to_height(src, float(_A.get("height_cm") or 0))
set_pivot(src, _A.get("pivot", "bottom_center"))

_src_desc = info["source"]
_has_tex = bool(_src_desc["textures"]) and _src_desc["uv_layers"] > 0
_has_vcol = bool(_src_desc["color_attributes"])
_uv = _A.get("uv", "auto")
_bake = _A.get("bake", "auto")
_tris = tri_count(src)
_target = int(_A.get("target_tris") or 0)

lo = src.copy()
lo.data = src.data.copy()
bpy.context.scene.collection.objects.link(lo)
lo.name = lo.data.name = _name
if _target and _tris > _target:
    modifier(lo, "DECIMATE", ratio=max(_target / _tris, 0.0005), use_collapse_triangulate=True)
info["decimated_triangles"] = tri_count(lo)

if _bake == "never":
    need_bake = False
elif _bake == "always":
    need_bake = _has_tex or _has_vcol or bool(_src_desc["materials"])
else:  # auto: colour that won't survive the trip: vertex colours, or textures whose UVs we replace
    need_bake = (_has_vcol and not _has_tex) or (_has_tex and _uv == "smart")
replace_uv = need_bake or _uv == "smart" or lo.data.uv_layers.__len__() == 0
if _uv == "keep" and lo.data.uv_layers and not need_bake:
    replace_uv = False
info["uv"] = "smart" if replace_uv else "kept"
if replace_uv:
    while lo.data.uv_layers:
        lo.data.uv_layers.remove(lo.data.uv_layers[0])
    lo.data.uv_layers.new(name="UVMap")
    smart_uv(lo, angle=66.0, margin=0.004)


def _emission_from(mat, socket_name, use_vcol):
    """Rewire a source material to emit what feeds `socket_name` of its Principled BSDF (EMIT bake = no lighting)."""
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    em = nt.nodes.new("ShaderNodeEmission")
    src_sock = None
    if bsdf is not None and bsdf.inputs[socket_name].is_linked:
        src_sock = bsdf.inputs[socket_name].links[0].from_socket
    elif bsdf is not None:
        v = bsdf.inputs[socket_name].default_value
        em.inputs["Color"].default_value = tuple(v)[:3] + (1.0,) if hasattr(v, "__len__") else (v, v, v, 1.0)
    if src_sock is None and use_vcol and socket_name == "Base Color":
        ca = nt.nodes.new("ShaderNodeVertexColor")
        ca.layer_name = src.data.color_attributes[0].name
        if bsdf is not None and not bsdf.inputs["Base Color"].is_linked:
            mul = nt.nodes.new("ShaderNodeMix")
            mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
            mul.inputs[0].default_value = 1.0
            mul.inputs[6].default_value = bsdf.inputs["Base Color"].default_value
            nt.links.new(ca.outputs["Color"], mul.inputs[7])
            src_sock = mul.outputs[2]
        else:
            src_sock = ca.outputs["Color"]
    if src_sock is not None:
        nt.links.new(src_sock, em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])


def _bake_map(kind, size, non_color, socket=None):
    img = bpy.data.images.new(f"T_{_name}_{kind}", size, size, alpha=False, float_buffer=False)
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    tex = bake_mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    bake_mat.node_tree.nodes.active = tex
    if socket:
        for m in src_mats:
            _emission_from(m, socket, _has_vcol)
    bpy.ops.object.select_all(action="DESELECT")
    src.select_set(True)
    lo.select_set(True)
    bpy.context.view_layer.objects.active = lo
    d = diag(lo)
    bpy.ops.object.bake(type="EMIT" if socket else "NORMAL", use_selected_to_active=True, cage_extrusion=0.01 * d,
                        max_ray_distance=0.05 * d, margin=8, normal_space="TANGENT")
    img.pack()
    return tex, img


baked = []
if need_bake:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.render.bake.margin = 8
    size = int(_A.get("texture_size", 1024))
    if not src.data.materials:
        src.data.materials.append(bpy.data.materials.new("M_src"))
    src_mats = []
    for i, m in enumerate(src.data.materials):  # bake from copies, never touch shared datablocks
        c = (m or bpy.data.materials.new("M_src")).copy()
        c.use_nodes = True
        src.data.materials[i] = c
        src_mats.append(c)
    lo.data.materials.clear()
    _mname = f"M_{_name.removeprefix('SM_')}"
    for _m in bpy.data.materials:  # the generated file often already has a material of that name
        if _m.name == _mname:
            _m.name = _mname + "_src"
    bake_mat = bpy.data.materials.new(_mname)
    bake_mat.use_nodes = True
    lo.data.materials.append(bake_mat)
    bsdf = bake_mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = float(_A.get("roughness", 0.7))
    bsdf.inputs["Metallic"].default_value = 0.0
    tex, _ = _bake_map("BaseColor", size, False, "Base Color")
    bake_mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    baked.append("base_color")
    for key, sock in (("Roughness", "Roughness"), ("Metallic", "Metallic")):
        if key in _src_desc["linked"]:
            t, _ = _bake_map(key, size // 2, True, sock)
            bake_mat.node_tree.links.new(t.outputs["Color"], bsdf.inputs[sock])
            baked.append(key.lower())
    nm = _A.get("normal_map", "auto")
    if nm == "always" or (nm == "auto" and _tris > 2 * info["decimated_triangles"]):
        t, _ = _bake_map("Normal", size, True)
        nmap = bake_mat.node_tree.nodes.new("ShaderNodeNormalMap")
        bake_mat.node_tree.links.new(t.outputs["Color"], nmap.inputs["Color"])
        bake_mat.node_tree.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        baked.append("normal")
elif not lo.data.materials:
    material(lo, f"M_{_name.removeprefix('SM_')}", color=(0.6, 0.6, 0.6), roughness=0.7)
info["baked"] = baked
if baked:  # the colour now lives in the texture; leftover COLOR_0 would be multiplied in again on re-import
    while lo.data.color_attributes:
        lo.data.color_attributes.remove(lo.data.color_attributes[0])

bpy.data.objects.remove(src, do_unlink=True)
smooth(lo, float(_A.get("smooth_angle", 60.0)))
if _A.get("preview", True):
    preview(objs=[lo], color_type="TEXTURE" if (baked or _has_tex) else "MATERIAL")
export(_name, "glb", [lo])
b = bounds([lo])
info.update({"triangles": tri_count(lo), "size_cm": [round(s * 100, 2) for s in b["size"]],
             "min_cm": [round(s * 100, 2) for s in b["min"]], "materials": [m.name for m in lo.data.materials if m]})
result = info
