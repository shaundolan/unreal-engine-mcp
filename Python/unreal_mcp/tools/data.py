"""Data types: user-defined structs and enums, DataTables, DataAssets, curves and curve tables.

Structs, enums, curves and curve tables are authored by the plugin (FStructureEditorUtils / FEnumEditorUtils,
command ``data_types``). DataTable rows (JSON or CSV) and DataAssets go through editor scripts
(``data_table.py``, ``data_asset.py``), the same code the ``data_table`` tool uses.
"""

from __future__ import annotations

from typing import Any

from . import call
from .python_exec import run_editor_script

_PLUGIN_OPS = {"struct", "enum", "curve", "curve_table"}
_LARGE = 300.0


def _script_op(get_conn, op: dict[str, Any]) -> dict[str, Any]:
    kind = op.get("op")
    asset = op.get("asset") or op.get("path") or ""
    if kind == "data_table":
        r = run_editor_script(
            get_conn,
            "data_table",
            {
                "path": asset,
                "action": "write",
                "struct": op.get("struct", ""),
                "rows": op.get("rows") or {},
                "csv": op.get("csv", ""),
                "mode": op.get("mode", "replace"),
            },
        )
    else:
        r = run_editor_script(
            get_conn,
            "data_asset",
            {"path": asset, "class": op.get("class", ""), "properties": op.get("properties") or {}},
        )
    ok = bool(r.get("success"))
    out = {"op": kind, "ok": ok, "path": r.get("path", asset)}
    for k in ("created", "row_count", "columns", "set", "class"):
        if k in r:
            out[k] = r[k]
    if not ok:
        out["error"] = r.get("error") or r.get("traceback", "failed")
    return out


def register(mcp, get_conn):
    @mcp.tool()
    def data_types(operations: list[dict[str, Any]], save: bool = True, stop_on_error: bool = True) -> dict[str, Any]:
        """Create or edit data assets in order (each op creates the asset if it doesn't exist):
          {"op":"enum","asset":"/Game/Data/E_Rarity","entries":["Common","Rare","Epic"]}
              entries by position; replace=false keeps extra existing entries. Blueprints use the names
              ("Rare") as pin defaults; the internal names (NewEnumerator1) work too.
          {"op":"struct","asset":"/Game/Data/S_Item","members":[
              {"name":"Name","type":"string","default":"Sword"},
              {"name":"Damage","type":"float","default":10,"tooltip":"base damage"},
              {"name":"Rarity","type":"enum:/Game/Data/E_Rarity","default":"Rare"},
              {"name":"Icon","type":"softobject:Texture2D"}, {"name":"Tags","type":"name[]"}],
           "remove":["Old"], "rename":{"Dmg":"Damage"}}
              types as in bp_apply (int float bool string name text vector rotator linearcolor struct:<S>
              enum:<E> object:<C> class:<C> softobject:<C>, [] for arrays); existing members keep their data.
          {"op":"data_table","asset":"/Game/Data/DT_Items","struct":"/Game/Data/S_Item",
           "rows":{"Sword":{"Name":"Sword","Damage":10,"Rarity":"Rare"}}}   or "csv":"Name,Damage\\nSword,10"
              mode "replace" (default) or "merge"
          {"op":"data_asset","asset":"/Game/Data/DA_Hero","class":"PrimaryDataAsset" or a Blueprint
           subclass path, "properties":{...}}
          {"op":"curve","asset":"/Game/Data/C_Falloff","type":"float"|"vector"|"color",
           "keys":[[0,1],[1,0.2,"cubic"]], "interp":"linear"|"cubic"|"constant"}
          {"op":"curve_table","asset":"/Game/Data/CT_Stats","rows":{"Damage":[[1,10],[10,100]]}}
              or "csv":"Name,1,10\\nDamage,10,100", "interp":"linear"
        Read any of them back with data_types_export."""
        results: list[dict[str, Any]] = []
        batch: list[dict[str, Any]] = []
        failed = 0

        def flush() -> bool:
            nonlocal failed
            if not batch:
                return True
            r = call(
                get_conn,
                "data_types",
                {"operations": batch, "save": save, "stop_on_error": stop_on_error},
                timeout=_LARGE,
            )
            if not r.get("success"):
                results.append({"op": batch[0].get("op"), "ok": False, "error": r.get("error")})
                failed += 1
                batch.clear()
                return False
            for x in r.get("results", []):
                x.pop("index", None)
                results.append(x)
                failed += 0 if x.get("ok") else 1
            batch.clear()
            return not r.get("failed")

        for op in operations:
            kind = op.get("op")
            if kind in _PLUGIN_OPS:
                batch.append(op)
                continue
            if not flush() and stop_on_error:
                break
            if kind in ("data_table", "data_asset"):
                r = _script_op(get_conn, op)
            else:
                r = {
                    "op": kind,
                    "ok": False,
                    "error": f"unknown op {kind!r} (struct, enum, data_table, data_asset, curve, curve_table)",
                }
            results.append(r)
            if not r["ok"]:
                failed += 1
                if stop_on_error:
                    break
        else:
            flush()
        out: dict[str, Any] = {"success": failed == 0, "results": results, "failed": failed}
        if failed:
            out["error"] = "; ".join(
                f"{x.get('op')} {x.get('path', '')}: {x.get('error')}" for x in results if not x.get("ok")
            )
        return out

    @mcp.tool()
    def data_types_export(asset: str) -> dict[str, Any]:
        """Read a struct (members: name, type, default), enum (entries: name, display, value), DataTable
        (row_struct, rows), DataAsset (class, properties), curve (keys) or curve table (rows of keys)."""
        return call(get_conn, "data_types_export", {"asset": asset})
