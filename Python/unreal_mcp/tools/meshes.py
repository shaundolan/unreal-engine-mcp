"""Static mesh tools: instanced placement in bulk, collision setup."""

from __future__ import annotations

from typing import Any

from .python_exec import run_editor_script


def register(mcp, get_conn):
    @mcp.tool()
    def instanced_mesh_place(actor: str, transforms: list[Any] | None = None, mesh: str = "",
                             component: str = "", hism: bool = True, clear: bool = False,
                             cull_distance: float | None = None, cull_start: float | None = None,
                             cast_shadow: bool | None = None, collision: bool | None = None,
                             min_lod: int | None = None, materials: dict[str, str] | None = None,
                             folder: str = "", location: list[float] | None = None,
                             world_space: bool = True) -> dict[str, Any]:
        """Place thousands of copies of one static mesh as a single (Hierarchical) Instanced Static
        Mesh component: one draw call per mesh instead of one actor per copy. Use it for trees,
        lamps, cars, rocks, fences, windows.

        actor: label of the actor holding the component; created (an empty Actor at `location`,
        in `folder`) if it does not exist. mesh: "/Game/Props/SM_Lamp". component: pick an
        existing instanced component by name; by default the actor's one using `mesh`, else a new
        one (HISM when hism=true, plain ISM otherwise).
        transforms (world space unless world_space=false), any mix of:
          [x, y, z]   [x, y, z, yaw]   [x, y, z, yaw, scale]   [x, y, z, yaw, [sx, sy, sz]]
          {"location": [x,y,z], "rotation": [pitch, yaw, roll], "scale": 1.5}
        clear=true removes the existing instances first (rebuild). cull_distance / cull_start:
        per-instance cull distance in cm (0 = never). min_lod: skip the most detailed LODs.
        cast_shadow, collision (true/false), materials {"0": "/Game/M_X"}.
        Returns instances (total), added, cleared, component, created_actor/created_component.
        One undo step."""
        return run_editor_script(get_conn, "instanced_mesh", {
            "actor": actor, "transforms": transforms or [], "mesh": mesh, "component": component, "hism": hism,
            "clear": clear, "cull_distance": cull_distance, "cull_start": cull_start, "cast_shadow": cast_shadow,
            "collision": collision, "min_lod": min_lod, "materials": materials or {}, "folder": folder,
            "location": location, "world_space": world_space}, timeout_s=600)

    @mcp.tool()
    def mesh_collision(meshes: list[str] | None = None, folder: str = "", collision: str = "",
                       profile: str = "", replace: bool = True, convex: dict[str, Any] | None = None,
                       actors: list[str] | None = None, recursive: bool = True, save: bool = True) -> dict[str, Any]:
        """Set up collision on static mesh assets. Imported or generated meshes often have none, so
        the player falls through them or walks through walls.

        meshes: asset paths, and/or folder: every StaticMesh under it.
        collision: box | sphere | capsule | kdop10x | kdop10y | kdop10z | kdop18 | kdop26 (simple
          shape fitted to the bounds) | convex (convex decomposition; convex={"hull_count":4,
          "max_hull_verts":16,"hull_precision":100000}) | complex_as_simple (collide with the render
          triangles: exact, fine for static level geometry, not for physics bodies) |
          simple_and_complex | use_default | none (remove simple shapes).
          replace=false adds the shape to the existing ones.
        profile: collision profile on the mesh's body setup, e.g. BlockAll, BlockAllDynamic,
          NoCollision, OverlapAll, Pawn. actors: labels of placed actors whose static mesh
          components get the profile too.
        Returns per mesh: simple_shapes count, trace flag, profile. Any error makes success false."""
        return run_editor_script(get_conn, "mesh_collision", {
            "meshes": meshes or [], "folder": folder, "collision": collision, "profile": profile,
            "replace": replace, "convex": convex or {}, "actors": actors or [], "recursive": recursive,
            "save": save}, timeout_s=900)
