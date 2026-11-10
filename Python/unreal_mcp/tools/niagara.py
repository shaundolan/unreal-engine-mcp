"""Niagara authoring: whole systems as JSON (niagara_export), batched atomic edits with rollback and dry run
(niagara_apply), and live particle counts / particle bounds (niagara_particles, pie_test "niagara" assertions).

The C++ side (MCPNiagaraAuthoring.cpp) drives the same stack view models as the Niagara editor and Epic's
UNiagaraExternalEditUtilities. This module resolves friendly names (templates, modules, dynamic inputs, stages)
to asset paths before sending, and turns sampled particle counts into assertion results."""

from __future__ import annotations

import re
import time
from typing import Any

from . import call

_LARGE = 590.0  # editor-side game-thread budget (compiles of big systems take minutes)

EMITTER_TEMPLATES = {
    "fountain": "Fountain",
    "burst": "OmnidirectionalBurst",
    "omnidirectional_burst": "OmnidirectionalBurst",
    "directional_burst": "DirectionalBurst",
    "sprite_burst": "SimpleSpriteBurst",
    "confetti": "ConfettiBurst",
    "mesh_burst": "UpwardMeshBurst",
    "ribbon": "LocationBasedRibbon",
    "beam": "StaticBeam",
    "static_beam": "StaticBeam",
    "dynamic_beam": "DynamicBeam",
    "hanging_particulates": "HangingParticulates",
    "dust": "HangingParticulates",
    "blowing": "BlowingParticles",
    "single": "SingleLoopingParticle",
    "recycle_in_view": "RecycleParticlesInView",
    "minimal": "Minimal",
    "empty": "Minimal",
}
TEMPLATE_ROOT = "/Niagara/DefaultAssets/Templates/Emitters/"

M = "/Niagara/Modules/"
MODULES = {  # friendly name -> module script (the engine's /Niagara content, UE 5.8)
    "SpawnRate": M + "Emitter/SpawnRate",
    "SpawnBurstInstantaneous": M + "Emitter/SpawnBurst_Instantaneous",
    "SpawnBurst": M + "Emitter/SpawnBurst_Instantaneous",
    "SpawnPerUnit": M + "Emitter/SpawnPerUnit",
    "SpawnPerFrame": M + "Emitter/SpawnPerFrame",
    "EmitterState": M + "Emitter/EmitterState",
    "SystemState": M + "System/SystemState",
    "InitializeParticle": M + "Spawn/Initialization/V2/InitializeParticle",
    "InitializeRibbon": M + "Spawn/Initialization/InitializeRibbon",
    "ShapeLocation": M + "Spawn/Location/V2/ShapeLocation",
    "BoxLocation": M + "Spawn/Location/BoxLocation",
    "SphereLocation": M + "Spawn/Location/V2/SphereLocation",
    "CylinderLocation": M + "Spawn/Location/CylinderLocation",
    "ConeLocation": M + "Spawn/Location/ConeLocation",
    "TorusLocation": M + "Spawn/Location/TorusLocation",
    "GridLocation": M + "Spawn/Location/V2/GridLocation",
    "StaticMeshLocation": M + "Spawn/Location/StaticMeshLocation",
    "AddVelocity": M + "Spawn/Velocity/AddVelocity",
    "AddVelocityInCone": M + "Spawn/Velocity/AddVelocityInCone",
    "AddVelocityFromPoint": M + "Spawn/Velocity/AddVelocityFromPoint",
    "InitialMeshOrientation": M + "Spawn/Orientation/V3/InitialMeshOrientation",
    "GravityForce": M + "Update/Forces/GravityForce",
    "Drag": M + "Update/Forces/Drag",
    "CurlNoiseForce": M + "Update/Forces/V2/CurlNoiseForce",
    "VortexForce": M + "Update/Forces/VortexForce",
    "PointAttractionForce": M + "Update/Forces/V2/PointAttractionForce",
    "WindForce": M + "Update/Forces/WindForce",
    "AccelerationForce": M + "Update/Forces/AccelerationForce",
    "LimitForce": M + "Update/Forces/LimitForce",
    "SolveForcesAndVelocity": M + "Solvers/SolveForcesAndVelocity",
    "Collision": M + "Collision/Collision",
    "ParticleState": M + "Update/Lifetime/ParticleState",
    "UpdateAge": M + "Update/Lifetime/UpdateAge",
    "KillParticlesInVolume": M + "Update/Lifetime/KillParticlesInVolume",
    "Color": M + "Update/Color/Color",
    "ScaleColor": M + "Update/Color/ScaleColor",
    "ScaleColorBySpeed": M + "Update/Color/ScaleColorBySpeed",
    "ScaleSpriteSize": M + "Update/Size/ScaleSpriteSize",
    "ScaleSpriteSizeBySpeed": M + "Update/Size/ScaleSpriteSizeBySpeed",
    "ScaleMeshSize": M + "Update/Size/ScaleMeshSize",
    "SpriteRotationRate": M + "Update/Orientation/SpriteRotationRate",
    "MeshRotationRate": M + "Update/Orientation/MeshRotationRate",
    "SubUVAnimation": M + "Update/SubUV/V2/SubUVAnimation",
    "DynamicMaterialParameters": M + "Update/Material/DynamicMaterialParameters",
    "LightAttributes": M + "Light/Light_Attributes",
    "ScaleVelocity": M + "Update/Velocity/ScaleVelocity",
    "InheritVelocity": M + "Update/Velocity/InheritVelocity",
    "VortexVelocity": M + "Update/Velocity/VortexVelocity",
    "GenerateDeathEvent": M + "Events/GenerateDeathEvent",
    "ReceiveDeathEvent": M + "Events/ReceiveDeathEvent",
    "GenerateCollisionEvent": M + "Events/GenerateCollisionEvent",
    "ReceiveCollisionEvent": M + "Events/ReceiveCollisionEvent",
    "GenerateLocationEvent": M + "Events/GenerateLocationEvent",
    "ReceiveLocationEvent": M + "Events/ReceiveLocationEvent",
    "RibbonWidth": M + "Ribbons/RibbonWidth",
    "BeamEmitterSetup": M + "Beams/BeamEmitterSetup",
    "SpawnBeam": M + "Beams/SpawnBeam",
    "UpdateBeam": M + "Beams/UpdateBeam",
    "CameraOffset": M + "Update/Camera/CameraOffset",
}

D = "/Niagara/DynamicInputs/"
DYNAMIC_INPUTS = {
    "UniformRangedFloat": D + "UniformRange/UniformRangedFloat",
    "UniformRangedInt": D + "UniformRange/UniformRangedInt",
    "UniformRangedVector": D + "UniformRange/UniformRangedVector",
    "UniformRangedVector2D": D + "UniformRange/UniformRangedVector2D",
    "UniformRangedLinearColor": D + "UniformRange/UniformRangedLinearColor",
    "UniformRangedVector4": D + "UniformRange/UniformRangedVector4",
    "RandomRangeFloat": D + "UniformRange/V2/RandomRangeFloat",
    "RandomRangeVector": D + "UniformRange/V2/RandomRangeVector",
    "RandomRangeInt": D + "UniformRange/V2/RandomRangeInt",
    "RandomRangeLinearColor": D + "UniformRange/V3/RandomRangeLinearColor",
    "FloatFromCurve": D + "ValueFromCurve/FloatFromCurve",
    "ColorFromCurve": D + "ValueFromCurve/ColorFromCurve",
    "VectorFromCurve": D + "ValueFromCurve/VectorFromCurve",
    "Vector2DFromCurve": D + "ValueFromCurve/Vector2DFromCurve",
    "ScaleFloatByCurve": D + "ScaleByCurve/ScaleFloatByCurve",
    "ScaleLinearColorByCurve": D + "ScaleByCurve/ScaleLinearColorByCurve",
    "MultiplyFloat": D + "Multiply/Multiply_Float",
    "MultiplyVectorByFloat": D + "Multiply/Multiply_VectorByFloat",
    "HueShiftLinearColor": D + "LinearColor/HueShiftLinearColor",
    "RandomVector": D + "Random/V2/RandomVector",
    "Waveform": D + "Helpers/Waveform",
    "SineWave": D + "Angles/Sine",
}

STAGES = {
    "systemspawn": "system_spawn",
    "systemupdate": "system_update",
    "emitterspawn": "emitter_spawn",
    "emitterupdate": "emitter_update",
    "particlespawn": "particle_spawn",
    "particleupdate": "particle_update",
    "spawn": "particle_spawn",
    "update": "particle_update",
}

RENDERERS = {"sprite", "mesh", "ribbon", "light", "decal", "component", "volume"}
SYSTEM_TEMPLATES = (
    "AttributeReaderTrails",
    "DirectionalBurst",
    "DirectionalBurstLightweight",
    "FountainLightweight",
    "MinimalLightweight",
    "RadialBurst",
    "SimpleExplosion",
)


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


_MOD_KEYS = {_key(k): v for k, v in MODULES.items()}
_DI_KEYS = {_key(k): v for k, v in DYNAMIC_INPUTS.items()}
_TPL_KEYS = {**{_key(k): v for k, v in EMITTER_TEMPLATES.items()}, **{_key(v): v for v in EMITTER_TEMPLATES.values()}}


def resolve_template(name: str) -> str:
    if not name or str(name).startswith("/"):
        return name
    tpl = _TPL_KEYS.get(_key(name))
    return TEMPLATE_ROOT + tpl if tpl else name  # unknown: the plugin searches the asset registry


def resolve_module(name: str) -> str:
    if not name or str(name).startswith("/"):
        return name
    return _MOD_KEYS.get(_key(name), name)


def resolve_dynamic(name: str) -> str:
    if not name or str(name).startswith("/"):
        return name
    return _DI_KEYS.get(_key(name), name)


def resolve_stage(stage: str) -> str:
    if not stage:
        return stage
    s = str(stage)
    if ":" in s:  # event:0, event:DeathEvent, simulation_stage:Name
        return s
    return STAGES.get(_key(s), s)


def _resolve_spec(spec: Any) -> Any:
    """Input value spec: resolve dynamic-input names, recursively."""
    if isinstance(spec, dict):
        out = dict(spec)
        dyn = out.get("dynamic")
        if isinstance(dyn, str):
            out["dynamic"] = resolve_dynamic(dyn)
        elif isinstance(dyn, dict):
            out["dynamic"] = {**dyn, "script": resolve_dynamic(dyn.get("script", ""))}
            if "inputs" in dyn:
                out["dynamic"]["inputs"] = {k: _resolve_spec(v) for k, v in dyn["inputs"].items()}
        if isinstance(out.get("inputs"), dict):
            out["inputs"] = {k: _resolve_spec(v) for k, v in out["inputs"].items()}
        return out
    return spec


def normalize_ops(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Friendly names -> asset paths and canonical stage keys. Raises ValueError for malformed ops."""
    out = []
    for i, op in enumerate(operations or []):
        if not isinstance(op, dict) or not op.get("op"):
            raise ValueError(f'operation {i} needs an "op"')
        o = dict(op)
        kind = o["op"]
        if "stage" in o:
            o["stage"] = resolve_stage(o["stage"])
        if kind == "add_emitter":
            o["from"] = resolve_template(o.get("from") or o.pop("template", "") or "Minimal")
        elif kind == "add_module":
            o["script"] = resolve_module(o.get("script") or o.get("module", ""))
        elif kind in ("add_renderer",) and str(o.get("type", "")).lower() not in RENDERERS and not o.get("class"):
            raise ValueError(f"operation {i}: add_renderer type must be one of {sorted(RENDERERS)}")
        if isinstance(o.get("inputs"), dict):
            o["inputs"] = {k: _resolve_spec(v) for k, v in o["inputs"].items()}
        if kind == "set_input":
            for k in ("value", "dynamic"):
                if k in o:
                    o[k] = _resolve_spec({k: o[k]})[k] if k == "dynamic" else _resolve_spec(o[k])
        out.append(o)
    return out


def resolve_create_from(create_from: str) -> str:
    if not create_from or create_from == "empty" or create_from.startswith("/"):
        return create_from
    name = create_from.split(":", 1)[1] if create_from.startswith("template:") else create_from
    if name.startswith("/"):
        return "template:" + name
    sys_hit = next((t for t in SYSTEM_TEMPLATES if _key(t) == _key(name)), None)
    if sys_hit:
        return "template:" + sys_hit
    tpl = _TPL_KEYS.get(_key(name))
    return "template:" + (tpl or name)


def finish_apply(r: dict[str, Any]) -> dict[str, Any]:
    if r.get("success") and "ok" in r:
        r["success"] = bool(r.pop("ok"))
        if not r["success"] and not r.get("error"):
            errs = [f"op {f['index']} ({f['op']}): {f['error']}" for f in r.get("failed", [])]
            comp = r.get("compile") or {}
            errs += [
                f"compile [{e.get('emitter') or 'system'}/{e.get('script')}]: {e.get('message')}"
                for e in comp.get("errors", [])
                if isinstance(e, dict)
            ]
            r["error"] = "; ".join(errs[:8]) or "niagara_apply failed"
    return r


# ---------------------------------------------------------------------------------------------- sampling


def sample_particles(
    get_conn,
    actor: str = "",
    world: str = "",
    samples: int = 1,
    interval_s: float = 0.1,
    system: str = "",
    sleep=time.sleep,
) -> dict[str, Any]:
    """Sample niagara_particles `samples` times and aggregate per component/emitter:
    particles {max, min, last, mean, runs} (runs = separate stretches with particles alive, e.g. timed bursts),
    and the union of the live particles' world-space AABB (CPU emitters)."""
    params: dict[str, Any] = {"actor": actor, "system": system}
    if world:
        params["world"] = world
    series: dict[tuple[str, str], list[int]] = {}
    bounds: dict[tuple[str, str], list[list[float]]] = {}
    last: dict[str, Any] = {}
    n = max(1, int(samples))
    for s in range(n):
        r = call(get_conn, "niagara_particles", params)
        if not r.get("success"):
            return r
        last = r
        seen = set()
        for c in r.get("components", []):
            for e in c.get("emitters", []) or [{"name": "", "particles": c.get("particles", 0)}]:
                k = (c.get("actor", ""), e.get("name", ""))
                seen.add(k)
                series.setdefault(k, [0] * s).append(int(e.get("particles", 0)))
                p = e.get("positions")
                if p:
                    b = bounds.setdefault(k, [list(p["min"]), list(p["max"])])
                    b[0] = [min(a, x) for a, x in zip(b[0], p["min"], strict=True)]
                    b[1] = [max(a, x) for a, x in zip(b[1], p["max"], strict=True)]
        for k, v in series.items():
            if k not in seen:
                v.append(0)
        if s + 1 < n:
            sleep(interval_s)

    def stats(v: list[int]) -> dict[str, Any]:
        runs, prev = 0, 0
        for x in v:
            if x > 0 and prev == 0:
                runs += 1
            prev = x
        return {
            "max": max(v),
            "min": min(v),
            "last": v[-1],
            "mean": round(sum(v) / len(v), 2),
            "runs": runs,
            "series": v,
        }

    emitters = []
    for (a, e), v in series.items():
        rec = {"actor": a, "emitter": e, **stats(v)}
        if (a, e) in bounds:
            rec["positions"] = {
                "min": [round(x, 2) for x in bounds[(a, e)][0]],
                "max": [round(x, 2) for x in bounds[(a, e)][1]],
            }
        emitters.append(rec)
    totals: list[int] = [sum(v[i] for v in series.values()) for i in range(n)] if series else [0] * n
    return {
        "success": True,
        "world": last.get("world"),
        "samples": n,
        "interval_s": interval_s,
        "total": stats(totals),
        "emitters": emitters,
        "components": [
            {k: c.get(k) for k in ("actor", "system", "active", "location")} for c in last.get("components", [])
        ],
    }


_OPS = {
    "==": lambda a, e: a == e,
    "!=": lambda a, e: a != e,
    "<": lambda a, e: a < e,
    "<=": lambda a, e: a <= e,
    ">": lambda a, e: a > e,
    ">=": lambda a, e: a >= e,
}
_AXES = {"x": 0, "y": 1, "z": 2}


def niagara_assertion(get_conn, a: dict[str, Any], index: int, sleep=time.sleep) -> dict[str, Any]:
    """pie_test assertion {"type":"niagara", "actor":"Campfire", "emitter":"Embers"?, "metric":"particles"|
    "min_z"|"max_z"|"min_x"|..., "stat":"max"|"min"|"last"|"mean"|"runs", "op":">=", "value":10,
    "samples":10, "interval_s":0.1}. metric min_z/max_z read the live particles' world AABB (CPU emitters)."""
    rec: dict[str, Any] = {"index": index, "type": "niagara"}
    if a.get("name"):
        rec["name"] = a["name"]
    try:
        r = sample_particles(
            get_conn,
            actor=a.get("actor") or a.get("label", ""),
            world=a.get("world", "pie"),
            samples=int(a.get("samples", 10)),
            interval_s=float(a.get("interval_s", 0.1)),
            system=a.get("system", ""),
            sleep=sleep,
        )
        if not r.get("success"):
            raise RuntimeError(r.get("error"))
        rows = r["emitters"]
        if a.get("actor") or a.get("label"):
            want = (a.get("actor") or a.get("label")).lower()
            rows = [x for x in rows if x["actor"].lower() == want]
            if not rows:
                raise LookupError(f"no Niagara component on actor {a.get('actor') or a.get('label')!r}")
        if a.get("emitter"):
            rows = [x for x in rows if _key(x["emitter"]) == _key(a["emitter"])]
            if not rows:
                raise LookupError(f"emitter {a['emitter']!r} not found")
        metric = a.get("metric", "particles")
        if metric == "particles":
            stat = a.get("stat", "max")
            if a.get("emitter") and len(rows) == 1:
                actual = rows[0][stat]
            else:  # several emitters/components: per-sample totals
                if not rows:
                    actual = 0
                else:
                    totals = [sum(x["series"][i] for x in rows) for i in range(len(rows[0]["series"]))]
                    runs, prev = 0, 0
                    for t in totals:
                        runs += 1 if t > 0 and prev == 0 else 0
                        prev = t
                    actual = {
                        "max": max(totals),
                        "min": min(totals),
                        "last": totals[-1],
                        "mean": sum(totals) / len(totals),
                        "runs": runs,
                    }[stat]
        else:
            m = re.fullmatch(r"(min|max)_([xyz])", metric)
            if not m:
                raise ValueError(f"unknown metric {metric!r} (particles, min_z, max_z, min_x, ...)")
            boxes = [x["positions"] for x in rows if x.get("positions")]
            if not boxes:
                raise LookupError("no CPU particle positions sampled (no live particles, or a GPU emitter)")
            ax = _AXES[m.group(2)]
            vals = [b[m.group(1)][ax] for b in boxes]
            actual = min(vals) if m.group(1) == "min" else max(vals)
        rec["actual"] = actual
        op = a.get("op", ">=")
        if op not in _OPS:
            raise ValueError(f"unknown op {op!r}")
        rec["passed"] = bool(_OPS[op](actual, a.get("value", 1)))
    except Exception as e:
        rec["passed"] = False
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def register(mcp, get_conn):
    @mcp.tool()
    def niagara_export(
        system: str,
        include_hidden: bool = False,
        only_modified: bool = False,
        emitters: list[str] | None = None,
        compile: bool = False,
    ) -> dict[str, Any]:
        """Read a whole Niagara system as JSON.
        Returns: user_parameters [{name "User.X", type, value}], fixed_bounds, warmup_time,
        system_stages [{stage: system_spawn|system_update, modules}], and emitters [{name, index, enabled,
        sim_target cpu|gpu, local_space, determinism, random_seed, bounds {mode, fixed{min,max}}, parent,
        properties (other non-default emitter settings), stages, renderers}].
        stages: [{stage: emitter_spawn|emitter_update|particle_spawn|particle_update|event:<n>|simulation_stage:<name>,
        (event: event_name, source_emitter, execution_mode, spawn_number ...), modules: [{name, index, script,
        enabled, inputs}]}] in execution order. Each input: {name, type (float, int, bool, vec2, vec3, vec4, color,
        position, quat, enum:<E>, di:<Class>), mode, modified, static_switch?, and value (local) | link
        ("Particles.NormalizedAge", "User.X") | dynamic {script, name, inputs[...]} | data {class, curves
        {RedCurve: [[t,v],...]}, properties} | object | expression | default_function}.
        renderers: [{index, type sprite|mesh|ribbon|light|..., enabled, materials, meshes, bindings
        {ColorBinding: "Particles.Color", ...}, properties (non-default)}].
        include_hidden: also inputs hidden by static switches. only_modified: just inputs changed from the module
        default (compact). compile=true adds the current compile state and stack issues."""
        return call(
            get_conn,
            "niagara_export",
            {
                "system": system,
                "include_hidden": include_hidden,
                "only_modified": only_modified,
                "emitters": emitters or [],
                "compile": compile,
                "timeout_s": _LARGE,
            },
        )

    @mcp.tool()
    def niagara_apply(
        system: str,
        operations: list[dict[str, Any]],
        create_from: str = "",
        dry_run: bool = False,
        compile: bool = True,
        save: bool = True,
        atomic: bool = True,
        wait_gpu_shaders: bool = False,
    ) -> dict[str, Any]:
        """Create or edit a Niagara system with a batch of ops: ONE undo step, compiled, and rolled back
        entirely (atomic) if any op fails or the compile has errors. dry_run validates, compiles and undoes.
        create_from (when `system` doesn't exist): "empty", "template:<Name>" (system templates SimpleExplosion,
        RadialBurst, DirectionalBurst, FountainLightweight, ...; emitter templates Fountain, OmnidirectionalBurst,
        LocationBasedRibbon, StaticBeam, HangingParticulates ... give a one-emitter system) or a system path to copy.
        Ops (emitter = name; stage = system_spawn|system_update|emitter_spawn|emitter_update|particle_spawn|
        particle_update|event:<n>; module = its name from niagara_export or an index):
          {"op":"add_emitter","name":"Flames","from":"Fountain"|"burst"|"ribbon"|"beam"|"minimal"|"/Game/FX/NE_X","index":0}
          {"op":"remove_emitter","emitter":"Flames"}   {"op":"move_emitter","emitter":"Flames","index":0}
          {"op":"set_emitter","emitter":"Flames","sim_target":"cpu","local_space":false,"enabled":true,
           "rename":"Fire","determinism":true,"random_seed":7,"bounds":{"min":[-200,-200,0],"max":[200,200,400]}|
           {"mode":"dynamic"},"properties":{"MaxGPUParticlesSpawnPerFrame":0}}
          {"op":"add_module","emitter":"Flames","stage":"particle_update","script":"Collision"|"/Niagara/...","index":2,
           "inputs":{...}}
          {"op":"remove_module"|"enable_module","emitter":..,"stage":..,"module":"Drag","enabled":false}
          {"op":"move_module","emitter":..,"stage":..,"module":"Drag","index":0}
          {"op":"set_inputs","emitter":..,"stage":"particle_spawn","module":"InitializeParticle","inputs":{
              "Lifetime": 2.0,                              constant (number, [x,y,z], [r,g,b,a], bool, enum name)
              "Color": {"link":"User.Tint"},                linked parameter
              "Velocity": {"random":[[-10,-10,100],[10,10,300]]},     Uniform Ranged <type> dynamic input
              "Sprite Size Mode": "Random Uniform",          static switches are set first
              "Lifetime": {"dynamic":"RandomRangeFloat","inputs":{"Minimum":1,"Maximum":2}},
              "Scale Color": {"curve":[[0,1,1,1,1],[1,1,0.2,0,0]]},   <Type>FromCurve over normalized age
              "Offset": {"expression":"float3(0,0,Particles.Age*10)"}, {"reset":true}, {"object":"/Game/SM_X"}}}
          {"op":"set_input","emitter":..,"stage":..,"module":..,"input":"Velocity/Maximum","value":[0,0,500]}
             (a dynamic input's own inputs use "Parent/Child" paths)
          {"op":"add_renderer","emitter":..,"type":"sprite|mesh|ribbon|light","material":"/Game/M_X","mesh":"/Engine/..",
           "bindings":{"ColorBinding":"Particles.Color"},"properties":{"Alignment":"VelocityAligned"}}
          {"op":"set_renderer"|"remove_renderer","emitter":..,"renderer":0|"sprite", ...same fields}
          {"op":"add_user_parameter","name":"SpawnRate","type":"float","value":50}  (set_/remove_user_parameter)
          {"op":"add_parameter","emitter":"Flames","name":"Emitter.Heat","type":"float","value":1}
          {"op":"add_event_handler","emitter":"Explosion","source_emitter":"Rocket","event":"DeathEvent",
           "mode":"spawned_particles","spawn_number":80}  -> stage "event:0" for its modules (ReceiveDeathEvent ...)
          {"op":"set_bounds","emitter":"Flames"?,"min":[..],"max":[..]}   {"op":"set_system","warmup_time":1}
        Returns success, applied, failed [{index, op, error}], results (assigned emitter/module names), compile
        {ok, status, errors [{emitter, script, message}], warnings, issues (stack errors/warnings)}, rolled_back.
        Recipes: skill://vfx. Inspect first with niagara_export(only_modified=true)."""
        try:
            ops = normalize_ops(operations)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        return finish_apply(
            call(
                get_conn,
                "niagara_apply",
                {
                    "system": system,
                    "operations": ops,
                    "create_from": resolve_create_from(create_from),
                    "dry_run": dry_run,
                    "compile": compile,
                    "save": save,
                    "atomic": atomic,
                    "wait_gpu_shaders": wait_gpu_shaders,
                    "timeout_s": _LARGE,
                },
            )
        )

    @mcp.tool()
    def niagara_particles(
        actor: str = "", world: str = "", samples: int = 1, interval_s: float = 0.1, system: str = ""
    ) -> dict[str, Any]:
        """Live particle counts of Niagara components (PIE world when PIE runs, else the editor world).
        actor: one actor label; system: only components using that system. samples > 1 samples every interval_s
        and returns per emitter {max, min, last, mean, runs (separate bursts), series} plus the live particles'
        world-space AABB (positions {min, max}, CPU emitters). In pie_test use {"type":"niagara", ...}."""
        return sample_particles(
            get_conn, actor=actor, world=world, samples=samples, interval_s=interval_s, system=system
        )


# ---------------------------------------------------------------------------------------------- export queries
# Used by UE-Bench checkers (bench/tasks/vfx-*.json); handy for agents reading an export too.


def nia_emitter(export: dict[str, Any], emitter: str) -> dict[str, Any] | None:
    return next((e for e in export.get("emitters", []) if _key(e["name"]) == _key(emitter)), None)


def nia_modules(
    export: dict[str, Any], emitter: str | None, stage: str, enabled_only: bool = True
) -> list[dict[str, Any]]:
    """Modules of one stage ("particle_update", "event:0", or "event" = every event stage); emitter None = system."""
    stages = (
        export.get("system_stages", []) if emitter is None else (nia_emitter(export, emitter) or {}).get("stages", [])
    )
    out: list[dict[str, Any]] = []
    for s in stages:
        if s["stage"] == stage or (stage == "event" and s["stage"].startswith("event:")):
            out += [m for m in s["modules"] if m.get("enabled", True) or not enabled_only]
    return out


def _script_name(m: dict[str, Any]) -> str:
    return m.get("script", "").split(".")[0].rsplit("/", 1)[-1]


def nia_has(export: dict[str, Any], emitter: str | None, stage: str, script: str) -> bool:
    """True when an enabled module of that script asset (e.g. "Collision") runs in the stage."""
    return any(_key(_script_name(m)) == _key(script) for m in nia_modules(export, emitter, stage))


def nia_input(export: dict[str, Any], emitter: str | None, stage: str, module: str, name: str) -> dict[str, Any] | None:
    """An input of the first enabled module whose name starts with `module` or whose script asset is `module`."""
    for m in nia_modules(export, emitter, stage):
        if _key(m["name"]).startswith(_key(module)) or _key(_script_name(m)) == _key(module):
            for i in m.get("inputs", []):
                if _key(i["name"]) == _key(name):
                    return i
    return None


def nia_value(inp: dict[str, Any] | None, which: str = "max") -> Any:
    """Numeric value of an input: its constant, or the max/min over a dynamic input's constant children
    (Uniform Ranged / Random Range), element-wise for vectors and colors. None when linked or unknown."""
    if not inp:
        return None
    if inp.get("mode") == "local":
        return inp.get("value")
    if inp.get("mode") == "dynamic":
        vals = [nia_value(c, which) for c in (inp.get("dynamic") or {}).get("inputs", [])]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        vecs = [v for v in vals if isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v)]
        pick = max if which == "max" else min
        if vecs:
            n = len(vecs[0])
            vecs = [v for v in vecs if len(v) == n]
            return [pick(v[k] for v in vecs) for k in range(n)]
        return pick(nums) if nums else None
    return None


def nia_lifetime(export: dict[str, Any], emitter: str, which: str = "max") -> float | None:
    """Particle lifetime from InitializeParticle (Lifetime, or Lifetime Max / Min in random mode)."""
    for name in ("Lifetime Max", "Lifetime") if which == "max" else ("Lifetime Min", "Lifetime"):
        v = nia_value(nia_input(export, emitter, "particle_spawn", "InitializeParticle", name), which)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None
