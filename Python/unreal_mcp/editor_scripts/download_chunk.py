# Read one chunk of a file on the editor machine as base64 (the reverse of upload_chunk).
# ARGS: path, offset, size, delete (remove the file and its temp dir once the last chunk is read)
# ruff: noqa: F821
import base64 as _b64
import os as _os

with open(ARGS["path"], "rb") as _fh:
    _fh.seek(int(ARGS.get("offset", 0)))
    _data = _fh.read(int(ARGS.get("size", 8 * 1024 * 1024)))
_total = _os.path.getsize(ARGS["path"])
result = {"data_b64": _b64.b64encode(_data).decode(), "total": _total,
          "eof": int(ARGS.get("offset", 0)) + len(_data) >= _total}
if result["eof"] and ARGS.get("delete"):
    try:
        _os.remove(ARGS["path"])
        _os.rmdir(_os.path.dirname(ARGS["path"]))
    except OSError:
        pass
del _data, ARGS
