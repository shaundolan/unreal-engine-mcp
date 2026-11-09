"""Foliage: scatter static meshes over a landscape or any surface (foliage instances or HISM), remove, inspect."""

from __future__ import annotations

from typing import Any

from .python_exec import run_editor_script


def register(mcp, get_conn):
    @mcp.tool()
    def foliage_inspect(mesh: str = "", sample_slopes: bool = False, max_samples: int = 2000) -> dict[str, Any]:
        """Foliage and HISM scatter in the level, grouped by mode and mesh: count, z range, scale range,
        bounds_xy, tilted_fraction (instances aligned to the surface). sample_slopes=true traces the ground
        under each instance (up to max_samples) and reports slope_deg {mean,p90,max}: use it to verify
        slope limits. mesh filters by mesh path."""
        return run_editor_script(get_conn, "foliage_inspect", {
            "mesh": mesh, "sample_slopes": sample_slopes, "max_samples": max_samples}, timeout_s=300)

    @mcp.tool()
    def foliage_scatter(operations: list[dict[str, Any]], dry_run: bool = False,
                        foliage_type_dir: str = "/Game/Foliage/Types", stop_on_error: bool = False) -> dict[str, Any]:
        """Scatter, remove or clear instanced meshes in ONE call and one undo step. Placement traces down onto
        the surface, so it works on landscapes and on any mesh with collision.

        operations:
          {"op":"scatter","mesh":"/Game/Trees/SM_Pine",
           (or "meshes":[{"mesh":..,"weight":2,"scale":[0.8,1.2]}])
           "surface":"Terrain",            label of the landscape/actor to place on; '' = the only landscape
           "area":{"min":[x,y],"max":[x,y]} | {"center":[x,y],"radius":3000},   default: the surface bounds
           "density":10,                   instances per 100 m2 (10 m x 10 m), or "count":500
           "slope_max":25,"slope_min":0,   degrees      "height_min":-100,"height_max":2000   world z cm
           "seed":7,"scale":[0.8,1.4],"align_to_normal":true (or max tilt degrees, e.g. 15),
           "random_yaw":true,"z_offset":-10,"min_distance":300,
           "mode":"foliage" (default, InstancedFoliageActor; editable with the Foliage tool) | "hism"
           (an actor tagged mcp_scatter with one HierarchicalInstancedStaticMesh per mesh), "label":"Forest"}
          {"op":"remove","mesh":"/Game/Trees/SM_Pine","area":{...},"mode":"foliage|hism","label":"Forest"}
              without mesh: every mesh; without area: every instance.
          {"op":"clear"}   all foliage instances and all HISM scatter actors.
        Each op reports placed/requested, attempts and why samples were rejected (miss, other_surface, slope,
        height, spacing). dry_run=true runs the placement (traces, filters) and reports counts without adding.
        Foliage types are created as FT_<mesh> under foliage_type_dir (reused when one exists for the mesh).
        Verify with foliage_inspect(sample_slopes=true)."""
        return run_editor_script(get_conn, "foliage_scatter", {
            "operations": operations, "dry_run": dry_run, "foliage_type_dir": foliage_type_dir,
            "stop_on_error": stop_on_error}, timeout_s=600)
