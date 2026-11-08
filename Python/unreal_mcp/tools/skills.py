"""Serve the workflow skills (skills/*/SKILL.md) as MCP resources and prompts, so any MCP client
gets them: skill://index lists them, skill://<name> returns one, and each is also a prompt."""

from __future__ import annotations

import os
import re
from pathlib import Path

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "skills"


def skills_dir() -> Path:
    return Path(os.environ.get("UNREAL_MCP_SKILLS_DIR") or _DEFAULT_DIR)


def load_skills(root: Path | None = None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    root = root or skills_dir()
    if not root.is_dir():
        return out
    for f in sorted(root.glob("*/SKILL.md")):
        text = f.read_text(encoding="utf-8")
        m = re.search(r"^description:\s*>?\s*\n?((?:\s{2,}.*\n)+|.*\n)", text, re.M)
        desc = " ".join(m.group(1).split()) if m else ""
        out[f.parent.name] = {"text": text, "description": desc}
    return out


def register(mcp, get_conn):
    if not hasattr(mcp, "resource") or not hasattr(mcp, "prompt"):
        return  # tool-only hosts (tests, bench)
    skills = load_skills()

    @mcp.resource("skill://index", name="skills-index", description="Workflow skills for the UnrealMCP tools",
                  mime_type="text/markdown")
    def skills_index() -> str:
        lines = ["# UnrealMCP skills", "", "Read one with skill://<name>.", ""]
        lines += [f"- **{n}**: {s['description']}" for n, s in skills.items()]
        return "\n".join(lines) + "\n"

    def add(name: str, text: str, desc: str):
        mcp.resource(f"skill://{name}", name=f"skill-{name}", description=desc[:300], mime_type="text/markdown")(
            lambda: text)
        mcp.prompt(name=name, description=desc[:300])(lambda: text)

    for name, s in skills.items():
        add(name, s["text"], s["description"])
