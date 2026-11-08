"""Material tools: inspect, build/edit graphs, material instances."""

from __future__ import annotations

from typing import Any

from .python_exec import run_editor_script


def register(mcp, get_conn):
    @mcp.tool()
    def material_inspect(path: str) -> dict[str, Any]:
        """Inspect a Material or Material Instance: parameters (with values for instances),
        parent, which outputs are connected (BaseColor, Roughness, ...), blend mode, shading
        model, textures used."""
        return run_editor_script(get_conn, "material_inspect", {"path": path})

    @mcp.tool()
    def material_edit(path: str, operations: list[dict[str, Any]] | None = None, create: bool = False,
                      clear: bool = False, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build or edit a material's expression graph in ONE call, then recompile and save.

        path: e.g. "/Game/Materials/M_Lava". create=true makes it if missing; clear=true wipes
        existing expressions first (rebuild). settings: material properties, e.g.
        {"blend_mode":"BLEND_TRANSLUCENT","two_sided":true,"shading_model":"MSM_UNLIT"}.
        operations (ids are local to this call):
          {"op":"add","id":"tint","class":"VectorParameter","x":-600,"y":0,
           "properties":{"parameter_name":"Tint","default_value":[1,0.3,0,1]}}
          {"op":"add","id":"noise","class":"Noise","properties":{"scale":0.01}}
          {"op":"add","id":"mul","class":"Multiply"}
          {"op":"connect","from":"tint","to":"mul","to_input":"A"}
          {"op":"connect","from":"noise","to":"mul","to_input":"B"}
          {"op":"output","from":"mul","property":"EmissiveColor"}
        class: MaterialExpression* class with or without the prefix (Constant, Constant3Vector,
        ScalarParameter, VectorParameter, TextureSampleParameter2D, TextureCoordinate, Multiply,
        Add, Lerp, Fresnel, Panner, Time, Sine, ...). Outputs: BaseColor, Metallic, Specular,
        Roughness, EmissiveColor, Opacity, OpacityMask, Normal, AmbientOcclusion,
        WorldPositionOffset. Pin names: "" = default; multi-output nodes use "R","G","B","A" or
        "RGB"; inputs like "A","B","Alpha","UVs","Exponent".
        """
        return run_editor_script(get_conn, "material_edit", {
            "path": path, "operations": operations or [], "create": create, "clear": clear,
            "settings": settings or {}}, timeout_s=300)

    @mcp.tool()
    def material_instance(path: str, parent: str = "", parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create (pass parent) or update a Material Instance and set parameters.
        parameters: {"Roughness": 0.2, "Tint": [1,0,0,1], "BaseTex": "/Game/T_Rock.T_Rock",
        "UseDetail": true}: numbers->scalar, lists/dicts->vector, asset paths->texture,
        booleans->static switch. Apply it with scene_compose properties or apply_material_to_actor."""
        return run_editor_script(get_conn, "material_instance", {
            "path": path, "parent": parent, "parameters": parameters or {}})
