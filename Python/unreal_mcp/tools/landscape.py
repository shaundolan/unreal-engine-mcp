"""Landscapes: create, sculpt (procedural or heightmap), material + paint layers, auto-paint, inspect.

Geometry and raw height/weight IO go through the plugin's landscape_create / landscape_inspect /
landscape_write commands (Python can't create landscape components). Everything procedural runs here, in
unreal_mcp.heightfield, so a whole edit is one round trip: read heights once, apply every op, write once.
"""

from __future__ import annotations

from typing import Any

from .. import heightfield as hf
from . import call
from .python_exec import run_editor_script

_MUTATING = ("create", "material", "layers", "paint", "delete") + tuple(hf.SCULPT_OPS)


def _field_from_info(info: dict[str, Any], heights_b64: str) -> hf.Field:
    w, h = info["resolution"]
    return hf.Field.from_b64(heights_b64, w, h, tuple(info["location"]), tuple(info["scale"]), info.get("yaw", 0.0))


def _plan(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate op shapes up front; returns [{index, op, error}] (empty = valid)."""
    errors = []
    creates = [i for i, o in enumerate(ops) if o.get("op") == "create"]
    for i, op in enumerate(ops):
        kind = op.get("op")
        err = None
        if kind not in _MUTATING:
            err = f"unknown op {kind!r}; expected create, {', '.join(hf.SCULPT_OPS)}, material, layers, paint, delete"
        elif kind == "create":
            if i != 0:
                err = "create must be the first op"
            else:
                try:
                    hf.grid_size(op.get("component_count", [8, 8]), int(op.get("section_size", 63)),
                                 int(op.get("sections_per_component", 1)))
                except ValueError as e:
                    err = str(e)
        elif kind == "paint" and not (op.get("rules") or op.get("layer")):
            err = "paint needs rules [...] or layer (fill)"
        elif kind == "material" and not op.get("path"):
            err = "material needs path"
        elif kind == "layers" and not op.get("names"):
            err = "layers needs names [...]"
        elif kind == "import_heightmap" and not op.get("path"):
            err = "import_heightmap needs path"
        elif kind == "delete" and len(ops) > 1:
            err = "delete must be the only op"
        if err:
            errors.append({"index": i, "op": kind, "error": err})
    if len(creates) > 1:
        errors.append({"index": creates[1], "op": "create", "error": "only one create per call"})
    return errors


def _layer_names(op: dict[str, Any]) -> list[str]:
    return [lay["name"] if isinstance(lay, dict) else str(lay) for lay in op.get("layers") or []]


def register(mcp, get_conn):
    @mcp.tool()
    def landscape_inspect(target: str = "", slope_stats: bool = True) -> dict[str, Any]:
        """Inspect a landscape: label, location (corner), scale, resolution (vertices), component_count,
        section_size, sections_per_component, size_cm, bounds, material, paint layers with coverage
        (mean weight 0..1) and dominant_fraction, height_world_cm {min,max,mean}, and (slope_stats)
        slope_deg {mean,p50,p90,max}. target: label or name; empty = the only landscape in the level."""
        r = call(get_conn, "landscape_inspect", {"target": target, "include_heights": slope_stats}, timeout=120)
        if r.get("success") and slope_stats and r.get("heights"):
            f = _field_from_info(r, r.pop("heights"))
            r["slope_deg"] = f.stats()["slope_deg"]
        return r

    @mcp.tool()
    def landscape_edit(operations: list[dict[str, Any]], target: str = "", dry_run: bool = False) -> dict[str, Any]:
        """Create, sculpt and paint a landscape in ONE call (heights read once, written once, one undo step).

        All ops are validated first; on any error nothing is written. dry_run=true computes everything and
        returns the resulting stats (heights, slopes, layer coverage) without touching the editor.
        Heights and positions are WORLD cm. Positions: "center":[x,y] (world) or "center_uv":[u,v] (0..1).

        operations (in order):
          {"op":"create","label":"Terrain","location":[0,0,0],"component_count":[8,8],"section_size":63,
           "sections_per_component":1,"scale":[100,100,100]}      location = CENTRE; must be first.
              resolution = component_count*section_size*sections_per_component+1 vertices per axis;
              scale.x/y = cm per quad; heights range = location.z +/- 256*scale.z cm.
          sculpt (applied in order to the height grid):
            {"op":"flat","height":0}
            {"op":"noise","amplitude":1500,"wavelength":8000,"octaves":4,"persistence":0.5,"seed":1,
             "ridged":false,"mode":"add|set|max|min"}           rolling hills / mountains (ridged)
            {"op":"hill","center_uv":[0.5,0.5],"radius":5000,"height":2000,"shape":"gaussian|cone|dome"}
            {"op":"ramp","start_uv":[0,0.5],"end_uv":[1,0.5],"height_start":0,"height_end":3000}
            {"op":"flatten","center":[x,y],"radius":2000,"falloff":1000,"height":500}   building pad
            {"op":"smooth","iterations":2,"radius":1}   {"op":"erode","iterations":20,"talus_deg":35,"strength":0.5}
            {"op":"terrace","step":400,"sharpness":0.8}  {"op":"clamp","min":-500,"max":4000}
            {"op":"scale","factor":1.5}  {"op":"offset","value":200}
            {"op":"import_heightmap","path":"/abs/height.png","min_height":-1000,"max_height":5000}
                PNG 8/16-bit gray/RGB or RAW .r16 on the MCP server's disk; resampled to the landscape.
          {"op":"material","path":"/Game/Landscape/M_Terrain",
           "layers":[{"name":"Grass","color":[0.12,0.3,0.05]},{"name":"Rock","color":[0.3,0.28,0.25]}],
           "create":true}   builds a LandscapeLayerBlend material (one weight-blended layer per entry) if
              missing (or create=true rebuilds it), assigns it and adds the paint layers (+ layer info assets
              in layer_info_dir, default <material folder>/LayerInfos, else /Game/Landscape/LayerInfos).
          {"op":"layers","names":["Grass","Rock"],"layer_info_dir":"/Game/Landscape/LayerInfos"}
          {"op":"paint","rules":[{"layer":"Grass"},
                                 {"layer":"Rock","slope_min":30,"slope_falloff":5},
                                 {"layer":"Snow","height_min":2500,"height_falloff":300,
                                  "noise":{"amplitude":0.3,"wavelength":3000}}]}
              rules apply in order, each covering the previous by its mask (slope degrees, world-Z band,
              optional noise, strength). {"op":"paint","layer":"Grass"} fills one layer. Paint is evaluated
              on the final heights of the call; weights always sum to 255.
          {"op":"delete"}   remove the landscape (only op in the call).
        target: existing landscape label/name (empty = the only one); ignored with create.
        """
        errors = _plan(operations)
        if errors:
            return {"success": False, "error": "; ".join(f"op {e['index']} ({e['op']}): {e['error']}" for e in errors),
                    "errors": errors, "written": False}
        if not operations:
            return {"success": False, "error": "no operations"}
        if operations[0].get("op") == "delete":
            if dry_run:
                return {"success": True, "dry_run": True, "would_delete": target or "(the only landscape)"}
            info = call(get_conn, "landscape_inspect", {"target": target})
            if not info.get("success"):
                return info
            r = run_editor_script(get_conn, "scene_compose", {"operations": [{"op": "delete", "target": info["name"]}]})
            return {"success": bool(r.get("succeeded")), "deleted": info["label"], **(
                {} if r.get("succeeded") else {"error": str(r.get("results") or r.get("traceback"))})}

        create = operations[0] if operations[0].get("op") == "create" else None
        info: dict[str, Any] | None = None
        field: hf.Field | None = None
        if create:
            w, h = hf.grid_size(create.get("component_count", [8, 8]), int(create.get("section_size", 63)),
                                int(create.get("sections_per_component", 1)))
            scale = tuple(float(v) for v in (create.get("scale") or [100, 100, 100]))
            cx, cy, cz = (float(v) for v in (create.get("location") or [0, 0, 0]))
            corner = (cx - (w - 1) * scale[0] / 2.0, cy - (h - 1) * scale[1] / 2.0, cz)
            if create.get("yaw"):
                return {"success": False, "error": "create: yaw is not supported yet; rotate after creating"}
            field = hf.Field(w, h, corner, scale, 0.0)
            existing_layers: list[str] = []
        needs_heights = any(o.get("op") in hf.SCULPT_OPS or o.get("op") == "paint" for o in operations)
        if not create:
            info = call(get_conn, "landscape_inspect", {"target": target, "include_heights": needs_heights},
                        timeout=120)
            if not info.get("success"):
                return info
            if needs_heights:
                field = _field_from_info(info, info.pop("heights"))
            existing_layers = [lay["name"] for lay in info.get("layers", [])]

        results: list[dict[str, Any]] = []
        material_ops, layer_names, layer_dir = [], [], None
        rules: list[dict[str, Any]] | None = None
        sculpted = False
        try:
            for i, op in enumerate(operations):
                kind = op["op"]
                if kind == "create":
                    results.append({"index": i, "op": kind, "ok": True, "resolution": [field.width, field.height]})
                elif kind in hf.SCULPT_OPS:
                    hf.sculpt(field, op)
                    sculpted = True
                    results.append({"index": i, "op": kind, "ok": True})
                elif kind == "material":
                    material_ops.append(op)
                    layer_names += [n for n in _layer_names(op) if n not in layer_names]
                    layer_dir = op.get("layer_info_dir") or layer_dir
                    results.append({"index": i, "op": kind, "ok": True})
                elif kind == "layers":
                    layer_names += [n for n in op["names"] if n not in layer_names]
                    layer_dir = op.get("layer_info_dir") or layer_dir
                    results.append({"index": i, "op": kind, "ok": True})
                elif kind == "paint":
                    rules = op.get("rules") or [{"layer": op["layer"]}]
                    layer_dir = op.get("layer_info_dir") or layer_dir
                    results.append({"index": i, "op": kind, "ok": True})
        except (ValueError, KeyError, FileNotFoundError, OSError) as e:
            results.append({"index": i, "op": operations[i].get("op"), "ok": False,
                            "error": f"{type(e).__name__}: {e}"})
            return {"success": False, "error": f"op {i} ({operations[i].get('op')}): {e}", "results": results,
                    "written": False}

        weights = None
        if rules is not None:
            known = list(dict.fromkeys(existing_layers + layer_names))
            weights = hf.paint_weights(field, rules, known)
            for name in weights:
                if name not in layer_names:
                    layer_names.append(name)

        preview: dict[str, Any] = {}
        if field is not None:
            preview = field.stats()
            clipped = field.clipped()
            if clipped:
                preview["clipped_vertices"] = clipped
                preview["warning"] = (f"{clipped} vertices exceed the height range of scale.z={field.scale[2]} "
                                      f"(+/-{256 * field.scale[2]:.0f} cm around location.z) and were clamped")
        if weights is not None:
            preview["layer_coverage"] = {k: hf.coverage(v) for k, v in weights.items()}
        if dry_run:
            return {"success": True, "dry_run": True, "results": results, "layers": layer_names,
                    "materials": [m["path"] for m in material_ops], **preview}

        # --- material assets first (they must exist before the landscape references them)
        material_path = None
        for m in material_ops:
            r = run_editor_script(get_conn, "landscape_material", {
                "path": m["path"], "layers": m.get("layers") or [], "create": m.get("create"),
                "roughness": m.get("roughness", 0.9), "tiling": m.get("tiling")})
            if not r.get("success"):
                return {"success": False, "error": f"material {m['path']}: {r.get('error') or r.get('traceback')}",
                        "results": results, "written": False}
            material_path = r["path"]
            for n in r.get("layers", []):
                if n not in layer_names:
                    layer_names.append(n)

        if not layer_dir and material_ops:  # keep layer infos next to the material (sandbox-friendly)
            layer_dir = material_ops[-1]["path"].split(".")[0].rsplit("/", 1)[0] + "/LayerInfos"
        params: dict[str, Any] = {}
        if layer_names:
            params["layers"] = layer_names
        if layer_dir:
            params["layer_info_dir"] = layer_dir
        if material_path:
            params["material"] = material_path
        if weights is not None:
            params["weights"] = {k: hf.weights_b64(v) for k, v in weights.items()}
        if create:
            cc = create.get("component_count", [8, 8])
            params.update({"label": create.get("label", "Landscape"),
                           "location": list(create.get("location") or [0, 0, 0]), "scale": list(field.scale),
                           "component_count": [cc, cc] if isinstance(cc, int) else list(cc),
                           "section_size": int(create.get("section_size", 63)),
                           "sections_per_component": int(create.get("sections_per_component", 1))})
            if sculpted:
                params["heights"] = field.to_b64()
            r = call(get_conn, "landscape_create", params, timeout=300)
        else:
            params["target"] = info["name"]
            if sculpted:
                params["heights"] = field.to_b64()
            r = call(get_conn, "landscape_write", params, timeout=300)
        if not r.get("success"):
            return {**r, "results": results, "written": False}
        out = {**r, "results": results, "written": True}
        for k in ("slope_deg", "layer_coverage", "warning", "clipped_vertices"):
            if k in preview:
                out[k] = preview[k]
        if r.get("errors"):
            out["success"] = False
            out["error"] = "; ".join(r["errors"])
        return out
