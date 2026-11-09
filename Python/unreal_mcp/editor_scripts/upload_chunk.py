# Append one base64 chunk of an uploaded file to a temp file on the editor machine (asset_import local_files
# when the editor is remote). ARGS: name (file name), dest ("" = start a new temp file), append, data_b64
# ruff: noqa: F821
import base64 as _b64
import os as _os
import tempfile as _tmp

_dest = ARGS.get("dest") or _os.path.join(_tmp.mkdtemp(prefix="unreal_mcp_upload_"),
                                          _os.path.basename(ARGS["name"]))
with open(_dest, "ab" if ARGS.get("append") else "wb") as _fh:
    _fh.write(_b64.b64decode(ARGS.get("data_b64") or ""))
result = {"path": _dest, "size": _os.path.getsize(_dest)}
del ARGS
