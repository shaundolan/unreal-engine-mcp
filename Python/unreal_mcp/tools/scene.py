"""Scene & level tools: query, brief, inspect, declarative compose, level structure."""

from __future__ import annotations

from typing import Any

from .python_exec import run_editor_script


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def register(mcp, get_conn):
    @mcp.tool()
    def scene_query(class_name: str = "", label_contains: str = "", tag: str = "", folder: str = "",
                    near: list[float] | None = None, radius: float | None = None,
                    box_min: list[float] | None = None, box_max: list[float] | None = None,
                    fields: list[str] | None = None, limit: int = 200) -> dict[str, Any]:
        """Find actors in the editor level by class, label, tag, Outliner folder and space.

        class_name: e.g. "StaticMeshActor", "PointLight", "Character" (subclasses match).
        near + radius: sphere filter in cm, results sorted by distance. box_min/box_max: AABB filter.
        fields: any of class, location, rotation, scale, tags, folder, bounds (default class, location).
        Returns labels; feed them to actor_inspect / scene_compose / property_set.
        """
        box = {"min": box_min, "max": box_max} if box_min and box_max else None
        return run_editor_script(get_conn, "scene_query", _clean({
            "class": class_name, "label_contains": label_contains, "tag": tag, "folder": folder,
            "near": near, "radius": radius, "box": box, "fields": fields, "limit": limit}))

    @mcp.tool()
    def scene_brief(top_classes: int = 25) -> dict[str, Any]:
        """Compact overview of the current level: actor count, top classes and folders, spatial
        bounds, lights and cameras. Use first to orient in an unfamiliar level."""
        return run_editor_script(get_conn, "scene_brief", {"top_classes": top_classes})

    @mcp.tool()
    def actor_inspect(target: str, properties: list[str] | None = None,
                      include_components: bool = True) -> dict[str, Any]:
        """One actor in depth: transform, tags, folder, bounds, components (meshes, materials,
        light intensity) and any requested editor properties (e.g. ["mobility",
        "static_mesh_component.cast_shadow"]). For raw reflection use object_describe."""
        return run_editor_script(get_conn, "actor_inspect", {
            "target": target, "properties": properties or [], "include_components": include_components})

    @mcp.tool()
    def scene_compose(operations: list[dict[str, Any]], stop_on_error: bool = False) -> dict[str, Any]:
        """Spawn, modify and delete many actors in ONE call and ONE undo step.

        operations, each one of:
          {"op":"spawn", "class":"PointLight" | "asset":"/Engine/BasicShapes/Cube.Cube",
           "label":"Lamp", "location":[x,y,z], "rotation":[pitch,yaw,roll], "scale":[x,y,z] | 2,
           "tags":["lamp"], "folder":"Lighting", "properties":{"light_component.intensity": 5000},
           "pattern":{"type":"grid","count":[4,4],"spacing":[300,300]}
                   | {"type":"circle","count":12,"radius":800,"face_center":true}
                   | {"type":"line","count":10,"step":[200,0,0]}}
          {"op":"modify", "target":"Lamp_3", "location":..., "rotation":..., "scale":...,
           "label":..., "tags":..., "folder":..., "hidden":true, "properties":{...}}
          {"op":"delete", "target":"Lamp_0"}
        Patterned spawns get labels "<label>_<i>". Each op reports ok/error; the rest continue
        unless stop_on_error. Property keys are Python editor-property names (snake_case),
        optionally "component.property".
        """
        return run_editor_script(get_conn, "scene_compose",
                                 {"operations": operations, "stop_on_error": stop_on_error}, timeout_s=300)

    @mcp.tool()
    def level_inspect() -> dict[str, Any]:
        """Level structure: persistent world, loaded and streaming sublevels, World Partition
        flag, key world settings (kill Z, gravity, default game mode)."""
        return run_editor_script(get_conn, "level_inspect", {})
