"""Local, free 3D generation and auto-rigging.

mesh_generate: text or image -> a local generation server (servers/gen3d: TripoSR, Stable Fast 3D, Hunyuan3D 2.1,
TRELLIS.2, or a mock) -> GLB -> headless Blender post-process (cleanup, decimate, UVs, PBR bake, scale, pivot)
-> safe import into Unreal -> LODs + collision.
mesh_postprocess: the same pipeline for a mesh file you already have.
auto_rig: fit an armature with UE5 Mannequin bone names in Blender + automatic weights -> skeletal mesh in Unreal ->
IK Rig (engine auto-characterisation) + IK Retargeter from the Mannequin -> retargeted template animations.
"""

from __future__ import annotations

import base64
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..blender_scripts import source as blender_source
from ..config import Settings
from ..gen3d import Gen3DClient, Gen3DError
from .assets import editor_is_local, import_assets
from .blender import run_blender
from .python_exec import run_editor_script, run_python

MANNEQUIN_MESH = "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple"
MANNEQUIN_ANIMS = [
    "/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle",
    "/Game/Characters/Mannequins/Anims/Unarmed/Walk/MF_Unarmed_Walk_Fwd",
]
COLLISIONS = ("convex", "box", "sphere", "capsule", "kdop10x", "kdop10y", "kdop10z", "kdop18", "kdop26",
              "complex_as_simple", "simple_and_complex", "none", "")
MESH_EXTS = (".glb", ".gltf", ".fbx", ".obj", ".ply", ".stl")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def client(server_url: str = "", settings: Settings | None = None) -> Gen3DClient:
    s = settings or Settings.from_env()
    return Gen3DClient(server_url or s.gen3d_url, s.gen3d_token)


def lod_fractions(lods: int) -> list[float]:
    """3 -> [0.5, 0.25, 0.125]: each extra LOD keeps half the triangles of the previous one."""
    return [0.5 ** (i + 1) for i in range(max(0, min(int(lods), 7)))]


def _work_dir(kind: str) -> Path:
    d = Settings.from_env().work_dir / kind / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _blender(script: str, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    return run_blender(blender_source("_common") + "\n" + blender_source(script), args=args, timeout_s=timeout_s)


def _images(r: dict[str, Any], extra: list[Path], show: bool) -> list[Any]:
    out: list[Any] = [r]
    if not show:
        return out
    from mcp.server.fastmcp import Image
    for p in [*extra, *[Path(x) for x in r.get("previews", [])]][:4]:
        if p.is_file():
            out.append(Image(data=p.read_bytes(), format="png"))
    return out


def fetch_editor_file(get_conn, editor_path: str, dest: Path, chunk: int = 8 * 1024 * 1024,
                      delete: bool = True) -> Path:
    """Copy a file from the editor machine to this one (download_chunk, the reverse of asset_import's upload)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    with dest.open("wb") as fh:
        while True:
            r = run_editor_script(get_conn, "download_chunk", {"path": editor_path, "offset": offset, "size": chunk,
                                                               "delete": delete}, timeout_s=300)
            if not r.get("success"):
                raise Gen3DError(f"download of {editor_path} failed: {r.get('error') or r.get('traceback')}")
            data = base64.b64decode(r["data_b64"])
            fh.write(data)
            offset += len(data)
            if r.get("eof") or not data:
                return dest


def import_and_finalize(get_conn, file: str, destination: str, name: str, kind: str, replace_existing: bool,
                        lods: list[float], collision: str = "", nanite: bool | None = None) -> dict[str, Any]:
    """Safe (re-)import of one exported file, then rename the mesh to `name`, LODs, collision."""
    imp = import_assets(get_conn, local_files=[file], destination=destination, replace_existing=replace_existing)
    out: dict[str, Any] = {"import": imp}
    if not imp.get("success"):
        out["success"] = False
        out["error"] = imp.get("error") or "import failed"
        return out
    imported = [p for i in imp.get("imports", []) for p in i.get("imported", [])]
    fin = run_editor_script(get_conn, "gen3d_finalize", {
        "imported": imported, "destination": destination, "name": name, "kind": kind, "lods": lods,
        "nanite": nanite, "save": True}, timeout_s=900, collect=True)
    out["asset"] = fin
    if not fin.get("success"):
        out["success"] = False
        out["error"] = fin.get("error") or fin.get("traceback") or "finalize failed"
        return out
    if kind == "static" and collision and collision != "none":
        col = run_editor_script(get_conn, "mesh_collision", {
            "meshes": [fin["mesh"]], "collision": collision, "profile": "BlockAll" if collision != "none" else "",
            "replace": True, "convex": {"hull_count": 8, "max_hull_verts": 16, "hull_precision": 100000},
            "save": True}, timeout_s=900)
        out["collision"] = col
        if not col.get("success"):
            out["success"] = False
            out["error"] = f"collision: {col.get('error') or col.get('traceback')}"
            return out
    out["success"] = True
    return out


def postprocess(input_path: str, name: str, target_tris: int, height_cm: float, uv: str, bake: str,
                texture_size: int, rotate: list[float] | None, pivot: str, preview: bool,
                timeout_s: float = 900.0) -> dict[str, Any]:
    return _blender("gen3d_post", {
        "input": input_path, "name": name, "target_tris": target_tris, "height_cm": height_cm, "uv": uv,
        "bake": bake, "texture_size": texture_size, "rotate": rotate, "pivot": pivot, "preview": preview}, timeout_s)


def _validate(out_name: str, destination: str, collision: str, uv: str, bake: str, pivot: str) -> str | None:
    if not _NAME.match(out_name):
        return f"out_name {out_name!r} must be a plain asset name like SM_Barrel"
    if not destination.startswith("/"):
        return "destination must be a content path like /Game/Generated"
    if collision not in COLLISIONS:
        return f"collision must be one of {[c for c in COLLISIONS if c]}"
    if uv not in ("auto", "keep", "smart") or bake not in ("auto", "always", "never"):
        return "uv must be auto|keep|smart and bake auto|always|never"
    if pivot not in ("bottom_center", "center", "keep"):
        return "pivot must be bottom_center|center|keep"
    return None


def process_file(get_conn, file: str, out_name: str, destination: str, target_tris: int, height_cm: float,
                 collision: str, lods: int, uv: str, bake: str, texture_size: int, rotate: list[float] | None,
                 pivot: str, import_to_unreal: bool, replace_existing: bool, nanite: bool | None,
                 show_preview: bool) -> dict[str, Any]:
    post = postprocess(file, out_name, target_tris, height_cm, uv, bake, texture_size, rotate, pivot, show_preview)
    r: dict[str, Any] = {"success": bool(post.get("success")), "postprocess": post.get("result"),
                         "previews": post.get("previews", []), "exports": post.get("exports", [])}
    if not post.get("success"):
        r["error"] = "Blender post-process failed"
        r["traceback"] = post.get("traceback") or post.get("error")
        r["output_tail"] = post.get("output_tail", "")[-1500:]
        return r
    r["glb"] = post["exports"][0]["path"]
    if import_to_unreal:
        imp = import_and_finalize(get_conn, r["glb"], destination, out_name, "static", replace_existing,
                                  lod_fractions(lods), collision, nanite)
        r["unreal"] = imp
        r["success"] = bool(imp.get("success"))
        if not r["success"]:
            r["error"] = imp.get("error")
        else:
            r["asset"] = imp["asset"]["mesh"]
    return r


def _licence_block(job_result: dict[str, Any] | None) -> dict[str, Any]:
    res = job_result or {}
    lic = res.get("licence") or {}
    out = {"model": res.get("backend"), "licence": lic.get("model"), "commercial": lic.get("commercial"),
           "text_to_image": res.get("text_to_image"), "text_to_image_licence": lic.get("text_to_image")}
    warnings = list(res.get("warnings") or [])
    if lic.get("commercial") == "no":
        warnings.insert(0, f"{res.get('backend')} output may NOT be used commercially ({lic.get('model')})")
    out["warnings"] = warnings
    return out


def rig_pipeline(get_conn, mesh: str, rig_type: str, out_name: str, destination: str, height_cm: float,
                 rotate: list[float] | None, retarget: bool, mannequin_mesh: str, animations: list[str] | None,
                 place_actor: str, play_animation: str, location: list[float] | None, fmt: str,
                 import_to_unreal: bool, replace_existing: bool, show_preview: bool) -> dict[str, Any]:
    work = _work_dir("rig")
    local = Path(mesh).expanduser()
    if local.is_file():
        src = str(local.resolve())
    elif mesh.startswith("/") and Path(mesh).suffix.lower() not in MESH_EXTS:  # an Unreal asset: export it first
        ex = run_editor_script(get_conn, "mesh_export", {"asset": mesh, "format": "fbx"}, timeout_s=600)
        if not ex.get("success"):
            return {"success": False, "error": f"could not export {mesh}: {ex.get('error') or ex.get('traceback')}"}
        if ex.get("source_file") and Path(ex["path"]).is_file():  # the file it was imported from, on this machine
            src = ex["path"]
        elif editor_is_local(get_conn):
            src = ex["path"]
        else:
            src = str(fetch_editor_file(get_conn, ex["path"], work / Path(ex["path"]).name,
                                        delete=not ex.get("source_file")))
    else:
        return {"success": False, "error": f"{mesh} is neither an existing file on the MCP server machine nor "
                "an Unreal asset path (/Game/...)"}
    rig = _blender("auto_rig", {"input": src, "name": out_name, "type": rig_type, "height_cm": height_cm,
                                "rotate": rotate, "format": fmt, "preview": show_preview}, 900)
    r: dict[str, Any] = {"success": bool(rig.get("success")), "rig": rig.get("result"),
                         "previews": rig.get("previews", [])}
    if not rig.get("success"):
        r.update(error="Blender auto-rig failed", traceback=rig.get("traceback") or rig.get("error"),
                 output_tail=rig.get("output_tail", "")[-1500:])
        return r
    r["file"] = rig["result"]["export"]["path"]
    r["warnings"] = list(rig["result"].get("warnings") or [])
    if not import_to_unreal:
        return r
    if replace_existing:  # a skeleton re-import keeps the old reference pose: start clean
        wipe = run_python(get_conn, f"m = unreal.load_asset({destination + '/' + out_name!r})\n"
                          "paths = []\n"
                          "if isinstance(m, unreal.SkeletalMesh):\n"
                          "    for o in (m.get_editor_property('skeleton'),\n"
                          "              m.get_editor_property('physics_asset'), m):\n"
                          "        if o is not None:\n"
                          "            paths.append(o.get_path_name().split('.')[0])\n"
                          "del m\n"
                          "result = [p for p in paths if unreal.EditorAssetLibrary.delete_asset(p)]",
                          timeout_s=120, collect=True)
        if not wipe.get("success"):
            r.update(success=False, error=f"could not remove the previous skeletal mesh: {wipe.get('traceback')}")
            return r
    imp = import_and_finalize(get_conn, r["file"], destination, out_name, "skeletal", replace_existing, [])
    r["unreal"] = imp
    if not imp.get("success"):
        r.update(success=False, error=imp.get("error"))
        return r
    skm = imp["asset"]["mesh"]
    r["skeletal_mesh"], r["skeleton"] = skm, imp["asset"].get("skeleton")
    if rig_type != "humanoid" or not retarget:
        if rig_type != "humanoid" and retarget:
            r["warnings"].append("Mannequin retargeting is for humanoids; skipped for this rig type")
        return r
    base = out_name.removeprefix("SKM_").removeprefix("SK_").removeprefix("SM_")
    ik_target, ik_source = f"{destination}/IK_{base}", f"{destination}/IK_Mannequin_{base}"
    rtg, anim_dest = f"{destination}/RTG_Mannequin_to_{base}", f"{destination}/Anims"
    anims = list(animations) if animations is not None else list(MANNEQUIN_ANIMS)
    # re-running replaces the rig assets this tool made (anim_edit refuses to overwrite)
    clear = run_python(get_conn, "paths = " + repr([ik_target, ik_source, rtg]) + "\nresult = [p for p in paths if "
                       "unreal.EditorAssetLibrary.does_asset_exist(p) and unreal.EditorAssetLibrary.delete_asset(p)]",
                       timeout_s=120, collect=True)
    if not clear.get("success"):
        r.update(success=False, error=f"could not clear old rig assets: {clear.get('traceback')}")
        return r
    ops: list[dict[str, Any]] = [
        {"op": "create_ik_rig", "asset": ik_target, "mesh": skm, "auto": True},
        {"op": "create_ik_rig", "asset": ik_source, "mesh": mannequin_mesh, "auto": True},
        {"op": "create_retargeter", "asset": rtg, "source_rig": ik_source, "target_rig": ik_target,
         "auto_map": True, "auto_align": True},
    ]
    if anims:
        ops.append({"op": "retarget", "retargeter": rtg, "source_mesh": mannequin_mesh, "target_mesh": skm,
                    "assets": anims, "target_path": anim_dest, "suffix": f"_{base}", "overwrite": True,
                    "include_referenced": False})
    ae = run_editor_script(get_conn, "anim_edit", {"operations": ops}, timeout_s=900, collect=True)
    r["retarget"] = ae
    results = ae.get("results") or []
    failed = [x for x in results if not x.get("ok")]
    if not ae.get("success") or failed or len(results) != len(ops):
        r.update(success=False, error="IK Rig / retarget failed: " + "; ".join(
            f"{x['op']}: {x.get('error')}" for x in failed) if failed else (ae.get("error") or ae.get("traceback")))
        return r
    r["ik_rig"], r["retargeter"] = ik_target, rtg
    r["chains"] = results[0].get("chains")
    created = results[-1].get("created", []) if anims else []
    r["animations"] = created
    if anims and not created:
        r.update(success=False, error="the retargeter produced no animations: check `chains` (the IK Rig found no "
                 "Mannequin-like chains?) and rig.warnings")
        return r
    if place_actor:
        want = (play_animation or "walk").lower()
        anim = next((a for a in created if want in a.lower()), created[0] if created else "")
        pl = run_editor_script(get_conn, "rig_place", {"label": place_actor, "mesh": skm, "animation": anim,
                                                       "location": location or [0, 0, 0]}, timeout_s=120)
        r["placed"] = pl
        if not pl.get("success"):
            r.update(success=False, error=f"placing {place_actor}: {pl.get('error') or pl.get('traceback')}")
    return r


def register(mcp, get_conn):
    @mcp.tool()
    def gen3d_status(server_url: str = "", job_id: str = "") -> dict[str, Any]:
        """Is the local 3D generation server up? Returns its image->3D backend and text->image model with their
        licences, VRAM and whether they're loaded, the GPU, and the queue. job_id: poll one job instead.
        The server is servers/gen3d/gen3d_server.py (it can run on another machine, e.g. a 4090 box: set
        UNREAL_MCP_GEN3D_URL / UNREAL_MCP_GEN3D_TOKEN)."""
        c = client(server_url)
        try:
            if job_id:
                return {"success": True, **c.job(job_id)}
            info = c.info()
        except Gen3DError as e:
            return {"success": False, "available": False, "url": c.url, "error": str(e)}
        b = info.get("backend", {})
        lic = b.get("licence", {})
        t2i_warnings = info.get("text_to_image", {}).get("licence", {}).get("warnings", [])
        return {"success": True, "available": True, "url": c.url, "backend": b.get("name"),
                "licence": lic.get("name"), "commercial": lic.get("commercial"),
                "warnings": lic.get("warnings", []) + t2i_warnings,
                "text_to_image": info.get("text_to_image", {}).get("name"), "vram_gb": b.get("vram_gb"),
                "textured": b.get("textured"), "loaded": info.get("loaded"), "gpu": info.get("gpu"),
                "queue": info.get("queue")}

    @mcp.tool()
    def mesh_generate(prompt: str = "", image_path: str = "", quality: str = "standard", target_tris: int = 5000,
                      height_cm: float = 0.0, out_name: str = "SM_Generated", destination: str = "/Game/Generated",
                      collision: str = "convex", lods: int = 3, uv: str = "auto", bake: str = "auto",
                      texture_size: int = 1024, rotate: list[float] | None = None, pivot: str = "bottom_center",
                      subject: str = "object", seed: int = 0, remove_background: bool = True,
                      backend_params: dict[str, Any] | None = None, import_to_unreal: bool = True,
                      replace_existing: bool = True, nanite: bool | None = None, show_preview: bool = True,
                      server_url: str = "", timeout_s: float = 900.0) -> list[Any]:
        """Generate a 3D asset from text or an image with a LOCAL model (free, no cloud), clean it up in headless
        Blender and import it into Unreal with materials, LODs and collision.

        prompt: text -> image (FLUX.1-schnell / SDXL on the server) -> 3D. Describe ONE object: "a wooden barrel
          with iron hoops". image_path: image -> 3D (a file on the MCP server machine), preferred when you have
          one: a single object, whole, centred, plain background. Both: the image is used, the prompt is a hint.
        quality: draft | standard | high (model steps/resolution). seed: same seed + input = same mesh.
        target_tris: decimate to at most this many triangles (props ~2-8k, hero ~20-50k; 0 keeps all).
        height_cm: real-world height (Z); 0 keeps the model's arbitrary scale (about 1 m).
        out_name: Unreal asset name (SM_Barrel). destination: content folder.
        collision: convex (default; decomposition) | box | sphere | capsule | kdopNN | complex_as_simple | none.
        lods: extra LODs, each half the triangles of the previous one (engine reduction); they only render
          when Nanite is off. nanite: true/false; default keeps the importer's choice (UE 5.8's Interchange
          turns Nanite on). The result reports `nanite`. Interchange puts the materials and textures in
          <destination>/<out_name>/Materials and /Textures; the mesh is moved to <destination>/<out_name>.
        uv: auto (keep the model's UVs if it has them) | keep | smart (re-unwrap; textures are re-baked).
        bake: auto (bake vertex colours / replaced UVs to PBR textures) | always | never. texture_size: px.
        rotate: [x, y, z] degrees applied first, if the model comes in lying down or facing the wrong way.
          Unreal convention: characters face -Y in Blender terms, which is what most models output.
        pivot: bottom_center (stands on the floor) | center | keep.
        subject: object | character (text->image prompt framing; use character for creatures and people,
          then auto_rig the result).
        import_to_unreal=false: stop after Blender (returns the GLB path) to inspect it first.
        replace_existing: re-import over an existing asset safely (GC before and after).
        Returns asset path, triangles, size_cm, baked maps, LOD count, collision, the licence of the model that
        made it (CHECK warnings: some models restrict commercial use or territory) and preview images (the
        conditioning image and a render). Takes ~10 s to a few minutes depending on the backend."""
        err = _validate(out_name, destination, collision, uv, bake, pivot)
        if quality not in ("draft", "standard", "high"):
            err = "quality must be draft, standard or high"
        if not (prompt or image_path):
            err = "pass prompt (text to 3D) or image_path (image to 3D)"
        if err:
            return [{"success": False, "error": err}]
        c = client(server_url)
        work = _work_dir("gen3d")
        try:
            job = c.submit(prompt=prompt, image_path=image_path, quality=quality, seed=seed,
                           params=backend_params or {}, remove_background=remove_background,
                           subject_kind="character" if subject == "character" else "object",
                           texture=bake != "never")
            j = c.wait(job, timeout_s=timeout_s)
        except Gen3DError as e:
            return [{"success": False, "error": str(e)}]
        lic = _licence_block(j.get("result"))
        if j["status"] != "done":
            return [{"success": False, "job_id": job, "error": f"generation {j['status']}: {j.get('error')}",
                     "server_traceback": (j.get("result") or {}).get("traceback"), **lic}]
        raw = c.download(job, work / "raw.glb")
        cond = None
        try:
            cond = c.download(job, work / "input.png", "image")
        except Gen3DError:
            pass
        r = process_file(get_conn, str(raw), out_name, destination, target_tris, height_cm, collision, lods, uv,
                         bake, texture_size, rotate, pivot, import_to_unreal, replace_existing, nanite, show_preview)
        r = {"job_id": job, "generation": {k: j["result"].get(k) for k in ("backend", "quality", "seed", "source",
                                                                        "textured", "bytes")},
             "generation_s": j.get("elapsed_s"), "raw_glb": str(raw), **lic, **r}
        if subject == "character":
            r["next"] = f"auto_rig(mesh='{r.get('glb')}', type='humanoid') to rig it for the Mannequin animations"
        return _images(r, [cond] if cond else [], show_preview)

    @mcp.tool()
    def mesh_postprocess(file: str, out_name: str = "SM_Imported", destination: str = "/Game/Generated",
                         target_tris: int = 5000, height_cm: float = 0.0, collision: str = "convex", lods: int = 3,
                         uv: str = "auto", bake: str = "auto", texture_size: int = 1024,
                         rotate: list[float] | None = None, pivot: str = "bottom_center",
                         import_to_unreal: bool = True, replace_existing: bool = True, nanite: bool | None = None,
                         show_preview: bool = True) -> list[Any]:
        """mesh_generate's cleanup + import pipeline for a mesh file you already have (GLB/glTF/FBX/OBJ/PLY/STL on
        the MCP server machine): e.g. output from a model run by hand, a scan or a download. Merges duplicate
        vertices, drops floating fragments, decimates to target_tris, keeps or re-unwraps UVs, bakes vertex colours
        / old UVs into PBR textures, scales to height_cm, puts the pivot at the bottom centre, imports with
        LODs and collision. Same arguments as mesh_generate."""
        err = _validate(out_name, destination, collision, uv, bake, pivot)
        if err:
            return [{"success": False, "error": err}]
        if not Path(file).expanduser().is_file():
            return [{"success": False, "error": f"{file} not found on the MCP server machine"}]
        r = process_file(get_conn, str(Path(file).expanduser().resolve()), out_name, destination, target_tris,
                         height_cm, collision, lods, uv, bake, texture_size, rotate, pivot, import_to_unreal,
                         replace_existing, nanite, show_preview)
        return _images(r, [], show_preview)

    @mcp.tool()
    def auto_rig(mesh: str, type: str = "humanoid", out_name: str = "", destination: str = "/Game/Characters/Generated",
                 height_cm: float = 0.0, rotate: list[float] | None = None, retarget: bool = True,
                 mannequin_mesh: str = MANNEQUIN_MESH, animations: list[str] | None = None, place_actor: str = "",
                 play_animation: str = "walk", location: list[float] | None = None, format: str = "glb",
                 import_to_unreal: bool = True, replace_existing: bool = True, show_preview: bool = True) -> list[Any]:
        """Rig a mesh automatically and make the UE5 Mannequin animations play on it.

        mesh: a mesh file on the MCP server machine (GLB/FBX/OBJ, e.g. mesh_generate's `glb`) or an Unreal
          StaticMesh/SkeletalMesh asset path (exported from the editor first).
        type: humanoid (UE5 Mannequin bone names: root, pelvis, spine_01-05, neck_01/02, head, clavicle/upperarm/
          lowerarm/hand, thigh/calf/foot/ball _l/_r) | quadruped (spine, neck, head, tail, four 3-bone legs) |
          prop (root + body). Humanoids work best in a T or A pose, facing -Y in Blender (most generators do);
          fix with rotate=[0,0,180] if it comes out backwards.
        Blender fits the armature to the mesh (limb extremes, torso width by ray casts, leg centres) and skins it
        with bone-heat automatic weights (vertices heat can't reach get the nearest bone). Then in Unreal:
        skeletal mesh import, an IK Rig from the engine's auto-characterisation (retarget chains from the bone
        names), an IK Rig for mannequin_mesh, an IK Retargeter (chains auto-mapped, bones auto-aligned) and
        batch-retargeted copies of `animations` (default: MM_Idle, MF_Unarmed_Walk_Fwd; needs the template
        Mannequin: scripts/install_mannequin.py) in <destination>/Anims.
        place_actor: label of a SkeletalMeshActor to place at `location` looping the retargeted animation whose
          name contains play_animation (default "walk"), so it walks in the viewport and in PIE.
        out_name: default SKM_<file stem>. format: glb (default; clean root bone) | fbx (Blender's FBX export
          bakes a 100x scale and a 90 degree rotation into the root bone in UE 5.8, verified live).
        Re-running replaces the skeletal mesh, its skeleton and physics asset (a re-import onto an existing
        skeleton would keep the old bone positions).
        Returns skeletal_mesh, skeleton, ik_rig, retargeter, animations, per-bone vertex counts, warnings."""
        if type not in ("humanoid", "quadruped", "prop"):
            return [{"success": False, "error": "type must be humanoid, quadruped or prop"}]
        if format not in ("fbx", "glb"):
            return [{"success": False, "error": "format must be fbx or glb"}]
        name = out_name or "SKM_" + re.sub(r"[^A-Za-z0-9_]", "_", Path(mesh).stem.split(".")[0]).removeprefix("SM_")
        err = _validate(name, destination, "", "auto", "auto", "bottom_center")
        if err:
            return [{"success": False, "error": err}]
        r = rig_pipeline(get_conn, mesh, type, name, destination.rstrip("/"), height_cm, rotate, retarget,
                         mannequin_mesh, animations, place_actor, play_animation, location, format,
                         import_to_unreal, replace_existing, show_preview)
        return _images(r, [], show_preview)
