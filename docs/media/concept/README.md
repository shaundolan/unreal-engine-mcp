# Concept render: prompt → playable

`../prompt_to_playable_concept.jpg` shows the target for demo 1. Every mesh was built with the `blender_run` prelude helpers
(`arena_scene.py`) and rendered in Cycles. `overlay.py` adds the agent panel.

Reproduce from `Python/`:
```bash
uv run python -c "from unreal_mcp.tools.blender import run_blender; \
  print(run_blender(open('../docs/media/concept/arena_scene.py').read(), args={'samples': 256}, timeout_s=1200)['result'])"
uv run --with pillow python ../docs/media/concept/overlay.py <arena.png from the result> out.png
```
