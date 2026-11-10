# Create, fill or read a DataTable.
# ARGS: path, action: read | write, struct (row struct path, needed to create), rows {RowName: {field: value}},
#       csv (instead of rows: "Name,Damage\nSword,10"), mode: replace (default) | merge (keep other rows, update given ones)
# ruff: noqa: F821
import csv as _csv
import io as _io
import json as _json

_lib = unreal.DataTableFunctionLibrary
_path = ARGS["path"]
_eal = unreal.EditorAssetLibrary
_dt = unreal.load_asset(_path) if _eal.does_asset_exist(_path) else None
_action = ARGS.get("action") or "read"


def _read(table):
    names = [str(n) for n in _lib.get_data_table_row_names(table)]
    cols = [str(c) for c in _lib.get_data_table_column_names(table)]
    rows = {n: {} for n in names}
    for c in cols:
        values = _lib.get_data_table_column_as_string(table, c)
        for n, v in zip(names, values, strict=False):
            rows[n][c] = str(v)
    return {"row_count": len(names), "columns": cols, "rows": rows}


def _csv_rows(text):
    """CSV with a header row; the first column is the row name (any header label)."""
    reader = list(_csv.reader(_io.StringIO(text.strip())))
    if not reader:
        return {}
    header = reader[0]
    return {r[0]: dict(zip(header[1:], r[1:], strict=False)) for r in reader[1:] if r}


if _action == "read":
    if _dt is None:
        raise LookupError(f"DataTable {_path!r} not found")
    result = {"path": _dt.get_path_name(), **_read(_dt)}
elif _action == "write":
    _created = False
    if _dt is None:
        if not ARGS.get("struct"):
            raise ValueError("table does not exist; pass struct (e.g. /Game/Data/S_Item) to create it")
        _sp = ARGS["struct"]
        _struct = unreal.load_object(None, _sp)
        if _struct is None and "." not in _sp.rsplit("/", 1)[-1]:
            _struct = unreal.load_object(None, f"{_sp}.{_sp.rsplit('/', 1)[-1]}")
        if _struct is None:
            raise LookupError(f"struct {ARGS['struct']!r} not found")
        _factory = unreal.DataTableFactory()
        _factory.set_editor_property("struct", _struct)
        _pkg, _name = _path.rsplit("/", 1)
        _dt = unreal.AssetToolsHelpers.get_asset_tools().create_asset(_name.split(".")[0], _pkg,
                                                                       unreal.DataTable, _factory)
        _created = True
    _rows = dict(ARGS.get("rows") or {})
    if ARGS.get("csv"):
        _rows.update(_csv_rows(ARGS["csv"]))
    if (ARGS.get("mode") or "replace") == "merge" and not _created:
        _existing = _json.loads(_lib.export_data_table_to_json_string(_dt) or "[]")
        _merged = {r.pop("Name"): r for r in _existing}
        for _k, _v in _rows.items():
            _merged.setdefault(_k, {}).update(_v)
        _rows = _merged
    _payload = [{"Name": k, **v} for k, v in _rows.items()]
    if not _lib.fill_data_table_from_json_string(_dt, _json.dumps(_payload)):
        raise RuntimeError("fill_data_table_from_json_string failed: field names/types must match the row struct "
                           "(user struct members by their display names)")
    _eal.save_loaded_asset(_dt, only_if_is_dirty=False)
    result = {"path": _dt.get_path_name(), "created": _created, **_read(_dt)}
else:
    raise ValueError("action must be 'read' or 'write'")
