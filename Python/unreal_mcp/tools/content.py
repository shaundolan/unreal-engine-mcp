"""VFX, cinematics and data tools: Niagara, Level Sequences, DataTables."""

from __future__ import annotations

from typing import Any

from .python_exec import run_editor_script


def register(mcp, get_conn):
    @mcp.tool()
    def niagara_edit(operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Place Niagara systems and set their User parameters (one undo step).
          {"op":"spawn","system":"/Game/FX/NS_Fire","label":"Campfire","location":[0,0,0],
           "parameters":{"SpawnRate":80,"Tint":[1,0.4,0.1,1],"Wind":[50,0,0],"int:Count":3,"Enabled":true}}
          {"op":"set","target":"Campfire","parameters":{"SpawnRate":10}}
        Find systems with search_assets(class_name="NiagaraSystem"). Authoring emitter/module
        graphs is not supported here; duplicate a template system and expose User parameters."""
        return run_editor_script(get_conn, "niagara", {"operations": operations})

    @mcp.tool()
    def sequence_edit(path: str, operations: list[dict[str, Any]] | None = None, create: bool = False,
                      fps: int = 0, duration_s: float | None = None, open: bool = False) -> dict[str, Any]:
        """Create or extend a Level Sequence (cinematic) in one call.

        path: e.g. "/Game/Cinematics/LS_Intro"; create=true makes it (default 30 fps).
        operations (binding ids are local to this call):
          {"op":"spawn_camera","id":"cam","label":"IntroCam","location":[-800,0,300],"rotation":[-15,0,0],
           "focal_length":35}
          {"op":"bind","id":"hero","actor":"Hero"}
          {"op":"transform_keys","binding":"cam","keys":[{"t":0,"location":[-800,0,300]},
                                                        {"t":5,"location":[-200,400,200],"rotation":[-10,-60,0]}]}
          {"op":"camera_cut","binding":"cam","start_s":0,"end_s":5}
        Rotations are [pitch, yaw, roll]. open=true opens it in Sequencer."""
        return run_editor_script(get_conn, "sequence_edit", {
            "path": path, "operations": operations or [], "create": create, "fps": fps or None,
            "duration_s": duration_s, "open": open}, timeout_s=300)

    @mcp.tool()
    def data_table(path: str, action: str = "read", rows: dict[str, dict[str, Any]] | None = None,
                   struct: str = "", csv: str = "", mode: str = "replace") -> dict[str, Any]:
        """Read or write a DataTable.
        action="read": rows as {RowName: {Column: value-as-string}}.
        action="write": rows {"Sword": {"Damage": 10, "Name": "Sword"}} and/or csv ("Name,Damage\nSword,10",
        first column = row name). mode="replace" (default) replaces all rows; "merge" keeps the others.
        Creates the table if missing (needs struct, e.g. "/Game/Data/S_Item"; make one with data_types).
        Field names must match the row struct's (user structs: the member names you gave them)."""
        return run_editor_script(get_conn, "data_table", {
            "path": path, "action": action, "rows": rows or {}, "struct": struct, "csv": csv, "mode": mode})
