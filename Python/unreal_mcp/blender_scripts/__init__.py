"""Blender-side scripts (bpy) run through tools.blender.run_blender, after the prelude's helpers.

Never imported by the server: their text is sent to `blender -b`. Each reads ARGS and sets `result`.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def source(name: str) -> str:
    path = _DIR / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"no Blender script {name!r}")
    return path.read_text(encoding="utf-8")
