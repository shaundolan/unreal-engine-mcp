"""Editor-side scripts: Python that runs INSIDE Unreal via execute_python.

Each ``<name>.py`` here reads its inputs from ``ARGS`` (a dict) and assigns its output to
``result``. ``render`` prepends ``ARGS`` and the shared helpers from ``_common.py`` so a
script is fully self-contained: the editor needs nothing from this package installed.

These files are never imported by the MCP server; they are text sent to the editor.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def source(name: str) -> str:
    path = _DIR / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"no editor script {name!r}")
    return path.read_text(encoding="utf-8")


def render(name: str, args: dict) -> str:
    return "\n".join([
        f"ARGS = __import__('json').loads({json.dumps(json.dumps(args))})",
        source("_common"),
        source(name),
    ])


def names() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.py") if not p.stem.startswith("_"))
