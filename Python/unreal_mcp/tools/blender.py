"""Blender bridge: model/UV/export in headless Blender on the MCP server machine, then import
into Unreal over the connection (works when the editor is on another machine).

Free 3D asset pipeline: the agent writes bpy code using the prelude helpers
(unreal_mcp/blender_prelude.py), checks the preview render, and imports the export.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

from ..config import Settings
from .assets import import_assets

_OUTPUT_TAIL = 4000
_IS_WINDOWS = os.name == "nt"


def _windows_blender_installs() -> list[Path]:
    """The Blender installer doesn't add itself to PATH on Windows; look where it puts itself (newest first)."""
    roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA")]
    found = []
    for root in filter(None, roots):
        found += Path(root).glob("Blender Foundation/Blender*/blender.exe")
        found += Path(root).glob("Programs/Blender Foundation/Blender*/blender.exe")
    return sorted(set(found), key=lambda p: p.parent.name, reverse=True)


def resolve_blender(settings: Settings) -> str | None:
    p = Path(settings.blender_path).expanduser()
    if p.is_file():
        return str(p)
    found = shutil.which(settings.blender_path)
    if found or not _IS_WINDOWS or settings.blender_path != "blender":
        return found
    installs = _windows_blender_installs()
    return str(installs[0]) if installs else None


def run_blender(code: str, args: dict[str, Any] | None = None, timeout_s: float = 300.0,
                settings: Settings | None = None) -> dict[str, Any]:
    """Run bpy code (with the prelude) in `blender -b`. Never raises."""
    settings = settings or Settings.from_env()
    exe = resolve_blender(settings)
    if not exe:
        return {"success": False, "error": f"Blender not found ({settings.blender_path!r}); install it or "
                "set UNREAL_MCP_BLENDER to the executable"}
    run_dir = settings.work_dir / "blender" / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    out_dir = run_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    prelude = run_dir / "prelude.py"
    # Explicit UTF-8 everywhere: Windows' default code page would mangle non-ASCII in scripts and JSON.
    prelude.write_text(resources.files("unreal_mcp").joinpath("blender_prelude.py").read_text(encoding="utf-8"),
                       encoding="utf-8")
    (run_dir / "user.py").write_text(code, encoding="utf-8")
    (run_dir / "args.json").write_text(json.dumps(args or {}), encoding="utf-8")
    result_file = run_dir / "result.json"
    cmd = [exe, "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(prelude), "--",
           str(result_file), str(out_dir), str(run_dir / "args.json"), str(run_dir / "user.py")]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout_s, cwd=run_dir)
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Blender did not finish within {timeout_s}s", "run_dir": str(run_dir)}
    except OSError as e:
        return {"success": False, "error": f"could not start Blender: {e}"}
    output = (proc.stdout or "") + (proc.stderr or "")
    base = {"run_dir": str(run_dir), "elapsed_s": round(time.monotonic() - t0, 2),
            "output_tail": output[-_OUTPUT_TAIL:]}
    if not result_file.is_file():
        return {"success": False, "error": f"Blender exited with code {proc.returncode} before the script "
                "finished (see output_tail)", **base}
    r = json.loads(result_file.read_text(encoding="utf-8"))
    for e in r.get("exports", []):
        p = Path(e["path"])
        e["bytes"] = p.stat().st_size if p.is_file() else 0
    out = {"success": bool(r.get("ok")), "result": r.get("result"), "exports": r.get("exports", []),
           "previews": r.get("previews", []), **base}
    if not r.get("ok"):
        out["traceback"] = r.get("traceback")
    return out


def register(mcp, get_conn):
    from mcp.server.fastmcp import Image

    @mcp.tool()
    def blender_status() -> dict[str, Any]:
        """Is headless Blender available for blender_run? Returns its path and version."""
        settings = Settings.from_env()
        exe = resolve_blender(settings)
        if not exe:
            return {"success": False, "available": False,
                    "error": "Blender not found; install it or set UNREAL_MCP_BLENDER"}
        try:
            v = subprocess.run([exe, "-b", "--factory-startup", "--version"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=60).stdout.strip().splitlines()
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"success": False, "available": False, "path": exe, "error": str(e)}
        return {"success": True, "available": True, "path": exe, "version": v[0] if v else "unknown"}

    @mcp.tool()
    def blender_run(code: str, args: dict[str, Any] | None = None, import_destination: str = "",
                    replace_existing: bool = True, show_preview: bool = True,
                    timeout_s: float = 300.0) -> list[Any]:
        """Model in headless Blender (on the MCP server machine), export, and optionally import
        the exports into Unreal. Units are metres in Blender (1 m = 100 Unreal units); Z is up.

        code: bpy Python. Available without imports: bpy, math, ARGS (= args), OUT_DIR, and helpers:
          clear_scene()                                  empty scene (run first)
          primitive(kind, name, location, rotation, scale, **op_kw)
              kind: cube uv_sphere ico_sphere cylinder cone torus plane circle monkey
          mesh_from_data(name, verts, faces)             raw geometry
          modifier(obj, "BEVEL", width=0.02, segments=3) applied by default (SUBSURF, SOLIDIFY,
                                                         DECIMATE ratio=, ARRAY count=, MIRROR, ...)
          material(obj, "M_Wood", color=(r,g,b), metallic, roughness, emission=(r,g,b))
          smart_uv(obj)  shade_smooth(obj)  apply_transforms(obj)  origin_to_bottom(obj)
          join([objs], "SM_Crate")  mesh_objects()  triangles()  bounds()
          export("SM_Crate", "fbx")                      fbx | glb | gltf | obj; file name = asset name
          preview()                                      PNG 3/4 view (call after modelling)
          result = {...}                                 returned to you
        import_destination: e.g. "/Game/Props" to import every export into Unreal right away.
        show_preview: attach preview renders as images so you can check the model.
        Example: clear_scene(); c = primitive("cube", "SM_Crate", size=1); modifier(c, "BEVEL",
          width=0.04, segments=2); material(c, "M_Crate", color=(0.45,0.3,0.15)); smart_uv(c);
          origin_to_bottom(c); preview(); export("SM_Crate")
        """
        r = run_blender(code, args=args, timeout_s=timeout_s)
        if r.get("success") and import_destination and r["exports"]:
            r["import"] = import_assets(get_conn, local_files=[e["path"] for e in r["exports"]],
                                        destination=import_destination, replace_existing=replace_existing)
            r["success"] = bool(r["import"].get("success")) and not r["import"].get("failed")
        out: list[Any] = [r]
        if show_preview:
            for p in r.get("previews", [])[:4]:
                if Path(p).is_file():
                    out.append(Image(data=Path(p).read_bytes(), format="png"))
        return out
