"""Live checks of every niagara_apply op family against a running editor (docs/WINDOWS_TEST.md 8b).

Run from Python/: uv run python ../scripts/niagara_live_check.py   (UNREAL_MCP_PORT picks the editor)
Creates and deletes /Game/NiagaraLive/NS_Ops."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Python"))
from unreal_mcp.bench import collect_tools  # noqa: E402
from unreal_mcp.connection import get_unreal_connection  # noqa: E402

t = collect_tools(get_unreal_connection)
P = "/Game/NiagaraLive/NS_Ops"
t["execute_python"](
    code=f"import unreal\nL = unreal.EditorAssetLibrary\nif L.does_asset_exist('{P}'): L.delete_asset('{P}')"
)
results = {}


def check(name, r, cond=None):
    ok = bool(r.get("success")) if cond is None else bool(cond(r))
    results[name] = ok
    print(
        ("PASS " if ok else "FAIL ") + name,
        "" if ok else json.dumps({k: r.get(k) for k in ("error", "failed", "compile")})[:1500],
    )
    return r


def mod(e, emitter, stage, name):
    em = next(x for x in e["emitters"] if x["name"] == emitter)
    st = next(s for s in em["stages"] if s["stage"] == stage)
    return next((m for m in st["modules"] if m["name"] == name), None), st


def inp(m, name):
    return next(i for i in m["inputs"] if i["name"] == name)


r = check(
    "create from empty + add_emitter + inputs + renderer + user param",
    t["niagara_apply"](
        P,
        create_from="empty",
        operations=[
            {"op": "add_user_parameter", "name": "SpawnRate", "type": "float", "value": 75},
            {"op": "add_user_parameter", "name": "Tint", "type": "color", "value": [1, 0.3, 0.1, 1]},
            {"op": "add_emitter", "name": "Flames", "from": "fountain"},
            {
                "op": "set_inputs",
                "emitter": "Flames",
                "stage": "emitter_update",
                "module": "SpawnRate",
                "inputs": {"SpawnRate": {"link": "User.SpawnRate"}},
            },
            {
                "op": "set_inputs",
                "emitter": "Flames",
                "stage": "particle_spawn",
                "module": "InitializeParticle",
                "inputs": {
                    "Lifetime Mode": "Direct Set",
                    "Lifetime": 1.25,
                    "Color": {"link": "User.Tint"},
                    "Sprite Size Mode": "Uniform",
                    "Uniform Sprite Size": {"random": [8, 20]},
                },
            },
            {
                "op": "add_module",
                "emitter": "Flames",
                "stage": "particle_update",
                "script": "CurlNoiseForce",
                "index": 2,
                "inputs": {"Noise Strength": 150, "Noise Frequency": 40},
            },
            {
                "op": "set_input",
                "emitter": "Flames",
                "stage": "particle_update",
                "module": "ScaleColor",
                "input": "Scale RGB",
                "curve": [[0, 1, 1, 1], [1, 0.2, 0.05, 0.0]],
            },
            {"op": "add_renderer", "emitter": "Flames", "type": "light", "properties": {"RadiusScale": 2.0}},
            {"op": "set_emitter", "emitter": "Flames", "bounds": {"min": [-200, -200, 0], "max": [200, 200, 400]}},
        ],
    ),
)
print("compile:", json.dumps(r.get("compile", {}))[:600])
e = t["niagara_export"](P)
m, _ = mod(e, "Flames", "emitter_update", "SpawnRate")
check("link readback", e, lambda e: inp(m, "SpawnRate").get("link") == "User.SpawnRate")
m, _ = mod(e, "Flames", "particle_spawn", "InitializeParticle")
check(
    "enum by display name + local value",
    e,
    lambda e: inp(m, "Lifetime Mode")["value"] == "Direct Set" and inp(m, "Lifetime")["value"] == 1.25,
)
check("random dynamic input", e, lambda e: inp(m, "Uniform Sprite Size")["mode"] == "dynamic")
print("  uniform size:", json.dumps(inp(m, "Uniform Sprite Size"))[:400])
m, st = mod(e, "Flames", "particle_update", "ScaleColor")
check("curve via FromCurve", e, lambda e: inp(m, "Scale RGB")["mode"] == "dynamic")
print("  scale rgb:", json.dumps(inp(m, "Scale RGB"))[:500])
check("module inserted at index 2", e, lambda e: [x["name"] for x in st["modules"]].index("CurlNoiseForce") == 2)
em = e["emitters"][0]
check(
    "light renderer + bounds",
    e,
    lambda e: [x["type"] for x in em["renderers"]] == ["sprite", "light"] and em["bounds"]["mode"] == "fixed",
)
check("user params", e, lambda e: {u["name"] for u in e["user_parameters"]} == {"User.SpawnRate", "User.Tint"})

before = json.dumps(t["niagara_export"](P)["emitters"])
r = check(
    "atomic failure rolls back",
    t["niagara_apply"](
        P,
        operations=[
            {"op": "remove_module", "emitter": "Flames", "stage": "particle_update", "module": "Drag"},
            {
                "op": "set_inputs",
                "emitter": "Flames",
                "stage": "particle_update",
                "module": "GravityForce",
                "inputs": {"NoSuchInput": 1},
            },
        ],
    ),
    lambda r: r["success"] is False and r["rolled_back"],
)
print("  error:", r.get("error"))
check("state unchanged after rollback", {}, lambda _: json.dumps(t["niagara_export"](P)["emitters"]) == before)
r = check(
    "dry run",
    t["niagara_apply"](P, dry_run=True, operations=[{"op": "remove_emitter", "emitter": "Flames"}]),
    lambda r: r["success"] and r["rolled_back"],
)
check("state unchanged after dry run", {}, lambda _: json.dumps(t["niagara_export"](P)["emitters"]) == before)
r = check(
    "compile error is structured + rolled back",
    t["niagara_apply"](
        P,
        operations=[
            {
                "op": "set_input",
                "emitter": "Flames",
                "stage": "particle_update",
                "module": "Drag",
                "input": "Drag",
                "expression": "Particles.DoesNotExist * 2.0 +",
            }
        ],
    ),
    lambda r: r["success"] is False and r.get("compile"),
)
print("  compile errors:", json.dumps((r.get("compile") or {}).get("errors"))[:800])
check("state unchanged after compile error", {}, lambda _: json.dumps(t["niagara_export"](P)["emitters"]) == before)

r = check(
    "move/enable/remove module, move emitter, event handler",
    t["niagara_apply"](
        P,
        operations=[
            {"op": "move_module", "emitter": "Flames", "stage": "particle_update", "module": "Drag", "index": 0},
            {
                "op": "enable_module",
                "emitter": "Flames",
                "stage": "particle_update",
                "module": "GravityForce",
                "enabled": False,
            },
            {"op": "remove_module", "emitter": "Flames", "stage": "particle_update", "module": "CurlNoiseForce"},
            {"op": "add_emitter", "name": "Sparks", "from": "burst", "index": 0},
            {"op": "add_module", "emitter": "Flames", "stage": "particle_update", "script": "GenerateDeathEvent"},
            {
                "op": "add_event_handler",
                "emitter": "Sparks",
                "source_emitter": "Flames",
                "event": "DeathEvent",
                "spawn_number": 3,
            },
            {"op": "add_module", "emitter": "Sparks", "stage": "event:0", "script": "ReceiveDeathEvent"},
            {
                "op": "set_renderer",
                "emitter": "Flames",
                "renderer": "sprite",
                "properties": {"Alignment": "VelocityAligned"},
            },
            {"op": "add_parameter", "emitter": "Flames", "name": "Emitter.Heat", "type": "float", "value": 2.5},
            {"op": "move_emitter", "emitter": "Flames", "index": 0},
        ],
    ),
)
e = t["niagara_export"](P)
fl = next(x for x in e["emitters"] if x["name"] == "Flames")
pu = next(s for s in fl["stages"] if s["stage"] == "particle_update")
check("module order after move", e, lambda e: pu["modules"][0]["name"] == "Drag")
check("disabled module", e, lambda e: not next(m for m in pu["modules"] if m["name"] == "GravityForce")["enabled"])
check("emitter order", e, lambda e: [x["name"] for x in e["emitters"]] == ["Flames", "Sparks"])
sp = next(x for x in e["emitters"] if x["name"] == "Sparks")
ev = [s for s in sp["stages"] if s["stage"].startswith("event:")]
check(
    "event handler stage",
    e,
    lambda e: (
        ev and ev[0].get("source_emitter") == "Flames" and ev[0]["modules"][0]["name"].startswith("ReceiveDeathEvent")
    ),
)
print("  event stage:", json.dumps({k: v for k, v in (ev[0] if ev else {}).items() if k != "modules"}))
es = next(s for s in fl["stages"] if s["stage"] == "emitter_spawn")
check(
    "set parameters module", e, lambda e: any("Emitter.Heat" in [i["name"] for i in m["inputs"]] for m in es["modules"])
)
print("  emitter_spawn:", json.dumps(es)[:600])
print("  sprite renderer props:", fl["renderers"][0]["properties"])
print(f"\n{sum(results.values())}/{len(results)} passed")
