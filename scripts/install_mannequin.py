#!/usr/bin/env python3
"""Copy the engine's template Mannequin content (Manny/Quinn meshes, skeleton, animations, blendspace)
into a project as /Game/Characters/Mannequins, the layout the ThirdPerson template uses.

The animation and GAS skills, smoke checks and UE-Bench anim-* tasks use it as test content.
Run with the editor closed (or rescan the Content Browser afterwards).

  python scripts/install_mannequin.py [--engine <UE root>] [--project <dir with the .uproject>]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ENGINES = [
    os.environ.get("UE_ROOT", ""),
    str(Path.home() / "Downloads" / "Linux_Unreal_Engine_5.8.4"),
    r"C:\Program Files\Epic Games\UE_5.8",
]
PACK = Path("Templates") / "TemplateResources" / "High" / "Characters" / "Content" / "Mannequins"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default="", help="Unreal Engine root (the folder with Engine/ and Templates/)")
    ap.add_argument("--project", default=str(REPO / "FlopperamUnrealMCP"), help="project folder")
    args = ap.parse_args(argv)
    engines = [args.engine] if args.engine else [e for e in DEFAULT_ENGINES if e]
    src = next((Path(e) / PACK for e in engines if (Path(e) / PACK).is_dir()), None)
    if src is None:
        print(f"template Mannequin pack not found under {engines} (expected {PACK})", file=sys.stderr)
        return 1
    dst = Path(args.project) / "Content" / "Characters" / "Mannequins"
    # copyfile, not copy2: engine template files are read-only and the editor must be able to re-save them
    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=shutil.copyfile)
    count = sum(1 for _ in dst.rglob("*.uasset"))
    print(f"copied {src} -> {dst} ({count} assets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
