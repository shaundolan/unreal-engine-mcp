"""Splines: spline actors with mesh segments (roads, rivers, pipes, fences), landscape carving, inspect."""

from __future__ import annotations

from typing import Any

from .python_exec import run_editor_script


def register(mcp, get_conn):
    @mcp.tool()
    def spline_inspect(target: str = "", max_points: int = 200) -> dict[str, Any]:
        """Spline actors (every actor with a SplineComponent, or one target label): world points, length,
        closed, mesh segments per mesh, bounds."""
        return run_editor_script(get_conn, "spline_inspect", {"target": target, "max_points": max_points})

    @mcp.tool()
    def spline_edit(operations: list[dict[str, Any]], dry_run: bool = False,
                    stop_on_error: bool = False) -> dict[str, Any]:
        """Create, rebuild, carve and delete spline actors in ONE call and one undo step.

        operations:
          {"op":"create","label":"Road","points":[[0,0,0],[2000,500,0],[4000,0,0]],   world cm
           "point_type":"curve" (linear, curve_clamped, constant),"closed":false,
           "snap_to_ground":true,"ground_offset":5,  points onto the landscape ("snap_to":"any" = any surface)
           "mesh":"/Engine/BasicShapes/Cube.Cube","mesh_scale":[6,0.1],   cross-section scale (Y, Z)
           "forward_axis":"x","segment_length":400,"material":"/Game/M_Asphalt","collision":true,
           "folder":"Roads","replace":false}
              one SplineMeshComponent per segment (default segment = the mesh's length), bent along the spline.
              Rivers: a flattened plane/cube with a water material; pipes: Cylinder with forward_axis "z".
          {"op":"modify","target":"Road","points":[...] | "append":[[x,y,z]],"mesh":..,"mesh_scale":..}  rebuilds
          {"op":"carve","target":"Road","landscape":"Terrain","width":600,"falloff":400,
           "raise":true,"lower":true,"layer":"Road","subdivisions":20}
              deforms the landscape to the spline (road bed / river channel: put the spline points below the
              surface and raise=false) and optionally paints a landscape layer along it.
          {"op":"delete","target":"Road"}
        Spline actors are tagged mcp_spline. dry_run=true validates points/snapping without changing the level."""
        return run_editor_script(get_conn, "spline_edit", {
            "operations": operations, "dry_run": dry_run, "stop_on_error": stop_on_error}, timeout_s=300)
