"""PCG: build a graph from a declarative node/edge spec, place a PCG volume, generate, inspect."""

from __future__ import annotations

import time
from typing import Any

from .python_exec import run_editor_script


def _wait_generated(get_conn, label: str, timeout_s: float) -> dict[str, Any]:
    """Generation is scheduled on editor ticks, so poll from here (between calls the editor ticks)."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while True:
        r = run_editor_script(get_conn, "pcg_inspect", {"volume": label})
        if not r.get("success"):
            return r
        last = (r.get("volumes") or [{}])[0]
        if last.get("generated") and last.get("instances", 0) > 0:
            return {"success": True, "volume": last, "waited": True}
        if time.monotonic() > deadline:
            return {"success": True, "volume": last, "waited": True, "timed_out": True}
        time.sleep(0.5)


def register(mcp, get_conn):
    @mcp.tool()
    def pcg_inspect(graph: str = "", volume: str = "") -> dict[str, Any]:
        """A PCG graph's nodes (id, type, input/output pin labels, position) and edges ("id.Pin"), and/or
        PCG volumes: graph, generated, spawned instance count per mesh, bounds. Empty volume = every
        PCGVolume in the level. Pin labels here are what pcg_graph edges use."""
        return run_editor_script(get_conn, "pcg_inspect", {"graph": graph, "volume": volume})

    @mcp.tool()
    def pcg_graph(graph: str, nodes: list[dict[str, Any]] | None = None, edges: list[dict[str, Any]] | None = None,
                  create: bool = False, clear: bool = False, remove_nodes: list[str] | None = None,
                  volume: dict[str, Any] | None = None, wait_s: float = 30.0, dry_run: bool = False) -> dict[str, Any]:
        """Create or edit a PCG graph in ONE call, optionally place a PCG volume with it and generate.

        graph: "/Game/PCG/PCG_Forest". create=true makes it if missing; clear=true removes every node first.
        nodes (ids become node titles; reusing an id updates that node):
          {"id":"landscape","type":"GetLandscapeData"}
          {"id":"sampler","type":"SurfaceSampler","properties":{"points_per_squared_meter":0.05,
                                                              "point_extents":[50,50,50],"looseness":1.0}}
          {"id":"steep","type":"NormalToDensity"}  {"id":"filter","type":"DensityFilter",
                                                   "properties":{"lower_bound":0.9,"upper_bound":1.0}}
          {"id":"jitter","type":"TransformPoints","properties":{"rotation_max":[0,0,360],"scale_min":[0.8,0.8,0.8],
                                                               "scale_max":[1.4,1.4,1.4]}}
          {"id":"spawn","type":"StaticMeshSpawner","meshes":[{"mesh":"/Game/SM_Tree","weight":3},
                                                             {"mesh":"/Game/SM_Bush","weight":1}]}
          type: settings class without the PCG prefix/Settings suffix (SurfaceSampler, StaticMeshSpawner,
          DensityFilter, GetLandscapeData = PCGGetLandscapeSettings, TransformPoints, SelfPruning,
          NormalToDensity, FilterByAttribute, ...) or the full class name. properties: snake_case
          editor-property names of that settings class (unreal_api_doc PCGSurfaceSamplerSettings).
        edges: {"from":"landscape.Out","to":"sampler.Surface"}, {"from":"input.In","to":"sampler.Bounding Shape"},
               {"from":"sampler","to":"spawn"} (no pin = first output -> first input), {"from":"spawn","to":"output"}.
          Graph ids "input"/"output" are the graph's own Input (pin "In" = the volume) and Output nodes.
        volume: {"label":"ForestVolume","fit":"Terrain"} (cover that actor's bounds) or
                {"label":..,"location":[x,y,z],"extent":[x,y,z]} (half size cm), "seed":42, "generate":true.
          Generation is asynchronous; the tool waits up to wait_s for spawned instances.
        Every node/edge is checked (unknown type, property, pin -> errors with the valid pin list).
        dry_run=true builds the nodes and edges on a scratch graph and reports errors without saving.
        Recipe "scatter on a landscape": GetLandscapeData.Out -> SurfaceSampler.Surface,
        input.In -> SurfaceSampler.Bounding Shape, SurfaceSampler -> StaticMeshSpawner -> output."""
        r = run_editor_script(get_conn, "pcg_graph", {
            "graph": graph, "nodes": nodes or [], "edges": edges or [], "create": create, "clear": clear,
            "remove_nodes": remove_nodes or [], "volume": volume, "dry_run": dry_run}, timeout_s=300)
        if not r.get("success"):
            return r
        if r.get("errors"):
            r["success"] = False
            r["error"] = "; ".join(f"{e.get('stage')} {e.get('id') or e.get('index')}: {e['error']}"
                                   for e in r["errors"][:8])
            return r
        if volume and r.get("generation_requested") and wait_s > 0:
            w = _wait_generated(get_conn, r["volume"]["label"], wait_s)
            if w.get("success"):
                r["volume"] = w["volume"]
                if w.get("timed_out"):
                    r["warning"] = (f"no instances spawned after {wait_s}s: check the graph wiring with pcg_inspect "
                                    "and the editor_log for PCG errors")
        return r
