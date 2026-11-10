"""A fake of the plugin's niagara_export / niagara_apply / niagara_particles commands (MCPNiagaraAuthoring.cpp).

Systems are kept in the export schema. Emitter templates and module input catalogs come from
tests/data/niagara_catalog.json, which is trimmed from a live UE 5.8.4 export (scripts in that file are real
/Niagara asset paths and real input names), so recipes and bench solutions that pass here use real names.
Semantics mirrored from the C++: ops run in order inside one transaction; atomic batches stop at the first
failure and roll back (a created system is deleted); dry_run always rolls back; compile errors roll back too.
niagara_particles runs a tiny analytic simulation so PIE checkers can be exercised (see _simulate)."""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

CATALOG = json.loads((Path(__file__).parent / "data" / "niagara_catalog.json").read_text(encoding="utf-8"))
TEMPLATE_ROOT = "/Niagara/DefaultAssets/Templates/Emitters/"
SYSTEM_TEMPLATES = CATALOG.get("system_templates", {})
GROUND_Z = 0.0


def _k(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "float"
    if isinstance(value, list):
        return {2: "vec2", 3: "vec3", 4: "color"}.get(len(value), "vec4")
    return "float"


class OpError(Exception):
    pass


class FakeNiagara:
    def __init__(self, ue=None):
        self.ue = ue  # fake `unreal` module: created systems are registered as assets there
        self.systems: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.force_compile_error = ""
        self.clock = 0.0
        self.dt = 0.2

    def install(self, fake) -> None:
        fake.on("niagara_export", self.export)
        fake.on("niagara_apply", self.apply)
        fake.on("niagara_particles", self.particles)

    # ------------------------------------------------------------------------------------------ export
    def export(self, p: dict[str, Any]) -> dict[str, Any]:
        sys_ = self._system(p["system"])
        out = copy.deepcopy(sys_)
        only = [_k(e) for e in p.get("emitters") or []]
        if only:
            out["emitters"] = [e for e in out["emitters"] if _k(e["name"]) in only]
        if p.get("only_modified"):
            for e in out["emitters"]:
                for s in e["stages"]:
                    for m in s["modules"]:
                        m["inputs"] = [i for i in m["inputs"] if i.get("modified") or i["mode"] == "dynamic"]
        if p.get("compile"):
            out["compile"] = self._compile(sys_)
        return out

    def _sync(self) -> None:
        """Systems whose asset the editor deleted (bench_reset clears a task's folder) are gone."""
        if self.ue is None:
            return
        for path in [p for p in self.systems if p not in self.ue._state["assets"]]:
            del self.systems[path]

    def _system(self, path: str) -> dict[str, Any]:
        self._sync()
        key = path.split(".")[0]
        if key in self.systems:
            return self.systems[key]
        tpl = key.rsplit("/", 1)[-1]
        if key.startswith("/Niagara/DefaultAssets/Templates/Systems/") and tpl in SYSTEM_TEMPLATES:
            return SYSTEM_TEMPLATES[tpl]
        raise LookupError(f"Niagara system '{path}' not found (search_assets class NiagaraSystem)")

    # ------------------------------------------------------------------------------------------ apply
    def apply(self, p: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(p)
        self._sync()
        path = p["system"].split(".")[0]
        created = False
        if path not in self.systems:
            cf = p.get("create_from") or ""
            if not cf:
                return {
                    "success": False,
                    "error": f'Niagara system \'{path}\' not found (pass create_from: "empty", "template:<Name>" ...)',
                }
            base = self._new_system(path, cf)
            created = True
        else:
            base = self.systems[path]
        work = copy.deepcopy(base)
        failed, results, applied = [], [], 0
        for i, op in enumerate(p.get("operations") or []):
            res = {"index": i, "op": op.get("op", "")}
            try:
                self._run(work, op, res)
                res["ok"] = True
                applied += 1
            except (OpError, LookupError, ValueError, KeyError, TypeError) as e:
                res.update(ok=False, error=str(e))
                failed.append(res)
                results.append(res)
                if p.get("atomic", True):
                    break
                continue
            results.append(res)
        comp = self._compile(work) if p.get("compile", True) and (not failed or not p.get("atomic", True)) else None
        rollback = p.get("dry_run") or (p.get("atomic", True) and (failed or (comp and not comp["ok"])))
        if not rollback:
            self.systems[path] = work
            if self.ue is not None:
                self.ue.register_asset(
                    self.ue.NiagaraSystem(path.rsplit("/", 1)[-1], f"{path}.{path.rsplit('/', 1)[-1]}")
                )
        elif created:
            self.systems.pop(path, None)
        out = {
            "ok": not failed and (comp is None or comp["ok"]),
            "system": f"{path}.{path.rsplit('/', 1)[-1]}",
            "created": created and not rollback,
            "applied": applied,
            "failed": failed,
            "results": results,
            "dry_run": bool(p.get("dry_run")),
            "rolled_back": bool(rollback),
            "saved": not rollback and p.get("save", True),
        }
        if comp is not None:
            out["compile"] = comp
        return out

    def _compile(self, sys_: dict[str, Any]) -> dict[str, Any]:
        errors = []
        if self.force_compile_error:
            em, _, msg = self.force_compile_error.partition(": ")
            errors.append({"emitter": em, "script": "ParticleUpdateScript", "message": msg or em})
        for e in sys_["emitters"]:
            for s in e["stages"]:
                for m in s["modules"]:
                    for i in m["inputs"]:
                        link = i.get("link") or ""
                        if link.startswith("User.") and not any(u["name"] == link for u in sys_["user_parameters"]):
                            errors.append(
                                {
                                    "emitter": e["name"],
                                    "script": s["stage"],
                                    "message": f"{m['name']}.{i['name']} reads {link}, which is not a User parameter",
                                }
                            )
        return {"ok": not errors, "status": "error" if errors else "ok", "errors": errors, "warnings": [], "issues": []}

    def _new_system(self, path: str, create_from: str) -> dict[str, Any]:
        name = path.rsplit("/", 1)[-1]
        sys_ = {
            "system": f"{path}.{name}",
            "name": name,
            "warmup_time": 0.0,
            "fixed_bounds_enabled": False,
            "user_parameters": [],
            "system_stages": [
                {"stage": "system_spawn", "modules": []},
                {"stage": "system_update", "modules": [self._module("SystemState", 0)]},
            ],
            "emitters": [],
        }
        if create_from != "empty":
            tpl = create_from.split(":", 1)[1] if create_from.startswith("template:") else create_from
            tname = tpl.rsplit("/", 1)[-1]
            if tname in SYSTEM_TEMPLATES:
                sys_ = copy.deepcopy(SYSTEM_TEMPLATES[tname])
                sys_.update(system=f"{path}.{name}", name=name)
            elif tname in CATALOG["emitter_templates"]:
                self._add_emitter(sys_, {"from": TEMPLATE_ROOT + tname, "name": tname})
            elif tpl.split(".")[0] in self.systems:
                sys_ = copy.deepcopy(self.systems[tpl.split(".")[0]])
                sys_.update(system=f"{path}.{name}", name=name)
            else:
                raise LookupError(f"create_from '{create_from}' not found")
        self.systems[path] = sys_
        return sys_

    # ------------------------------------------------------------------------------------------ helpers
    def _module(self, key: str, index: int, name: str | None = None) -> dict[str, Any]:
        spec = CATALOG["modules"][key]
        return {
            "name": name or spec["name"],
            "index": index,
            "script": spec["script"],
            "enabled": True,
            "inputs": copy.deepcopy(spec["inputs"]),
        }

    def _module_key(self, script: str) -> str:
        want = script.split(".")[0]
        for k, v in CATALOG["modules"].items():
            if v["script"].split(".")[0] == want or _k(k) == _k(script) or _k(v["name"]) == _k(script):
                return k
        raise LookupError(f"no Niagara script named '{script}' (give a path like /Niagara/Modules/Update/Color/Color)")

    @staticmethod
    def _emitter(sys_: dict[str, Any], name: str) -> dict[str, Any]:
        for e in sys_["emitters"]:
            if _k(e["name"]) == _k(name):
                return e
        names = ", ".join(e["name"] for e in sys_["emitters"]) or "none"
        raise OpError(f"emitter '{name}' not found (emitters: {names})")

    def _stage(self, sys_: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
        st = op.get("stage")
        if not st:
            raise OpError('missing "stage"')
        stages = self._emitter(sys_, op["emitter"])["stages"] if op.get("emitter") else sys_["system_stages"]
        for s in stages:
            if s["stage"] == st:
                return s
        raise OpError(f"stage '{st}' not found (stages: {', '.join(s['stage'] for s in stages)})")

    def _find_module(self, stage: dict[str, Any], ref: Any) -> dict[str, Any]:
        mods = stage["modules"]
        if isinstance(ref, int):
            for m in mods:
                if m["index"] == ref:
                    return m
        else:
            for m in mods:
                if _k(m["name"]) == _k(ref) or _k(m["script"].rsplit(".", 1)[-1]) == _k(ref):
                    return m
        raise OpError(
            f"module '{ref}' not in {stage['stage']} (modules: {', '.join(m['name'] for m in mods) or 'none'})"
        )

    @staticmethod
    def _reindex(stage: dict[str, Any]) -> None:
        for i, m in enumerate(stage["modules"]):
            m["index"] = i

    def _find_input(self, inputs: list[dict[str, Any]], name: str) -> dict[str, Any]:
        for i in inputs:
            if _k(i["name"]) == _k(name) or _k(i.get("display", "")) == _k(name):
                return i
        raise OpError(
            f"input '{name}' not found (inputs: {', '.join(i['name'] for i in inputs if not i.get('hidden'))})"
        )

    def _set(self, inp: dict[str, Any], spec: Any, sys_: dict[str, Any]) -> None:
        for k in ("value", "link", "dynamic", "data", "object", "expression"):
            inp.pop(k, None)
        inp["modified"] = True
        t = inp.get("type", "float")
        if not isinstance(spec, dict) or ("r" in spec or "x" in spec):
            spec = {"value": spec}
        if spec.get("reset"):
            inp.update(mode="local", value=inp.get("default"), modified=False)
            return
        if "value" in spec:
            v = spec["value"]
            if t.startswith("enum:"):
                opts = CATALOG["enums"].get(t[5:], [])
                hit = next((o for o in opts if _k(o) == _k(v)), None)
                if opts and hit is None:
                    raise OpError(f"enum {t[5:]} has no value {v} (one of: {', '.join(opts)})")
                v = hit or v
            elif t in ("vec2", "vec3", "vec4", "color", "position", "quat"):
                n = {"vec2": 2, "vec3": 3, "position": 3}.get(t, 4)
                if isinstance(v, (int, float)):
                    v = [float(v)] * n
                if isinstance(v, dict):
                    v = [v.get(c, 1.0 if c == "a" else 0.0) for c in ("r", "g", "b", "a")]
                if t == "color" and len(v) == 3:
                    v = [*v, 1.0]
                if len(v) != n:
                    raise OpError(f"input '{inp['name']}' ({t}) needs {n} numbers, got {len(v)}")
            elif t == "float" and not isinstance(v, (int, float)):
                raise OpError(f"input '{inp['name']}' (float): expected a number")
            inp.update(mode="local", value=v)
            if inp.get("static_switch"):
                self._apply_switch(inp)
            return
        if "link" in spec:
            link = spec["link"] if "." in spec["link"] else "User." + spec["link"]
            inp.update(mode="linked", link=link)
            return
        if "expression" in spec:
            inp.update(mode="expression", expression=spec["expression"])
            return
        if "object" in spec:
            inp.update(mode="object", object=spec["object"])
            return
        if "random" in spec:
            lo, hi = spec["random"]
            di = {
                "float": "UniformRangedFloat",
                "int": "UniformRangedInt",
                "color": "UniformRangedLinearColor",
                "vec2": "UniformRangedVector2D",
                "vec4": "UniformRangedVector4",
            }.get(t, "UniformRangedVector")
            inp.update(
                mode="dynamic",
                dynamic={
                    "script": f"/Niagara/DynamicInputs/UniformRange/{di}.{di}",
                    "name": di,
                    "inputs": [
                        {"name": "Minimum", "type": t, "mode": "local", "value": lo, "modified": True},
                        {"name": "Maximum", "type": t, "mode": "local", "value": hi, "modified": True},
                    ],
                },
            )
            return
        if "curve" in spec:
            keys = (
                spec["curve"]["keys"] if isinstance(spec["curve"], dict) and "keys" in spec["curve"] else spec["curve"]
            )
            if any(not isinstance(k, list) or len(k) < 2 for k in keys):
                raise OpError("curve keys are [time, value, ...]")
            if t.startswith("di:"):
                inp.update(mode="data", data={"class": t[3:], "curves": {"Curve": keys}})
                return
            di = {
                "float": "FloatFromCurve",
                "color": "ColorFromCurve",
                "vec2": "Vector2DFromCurve",
                "vec4": "Vector4FromCurve",
            }.get(t, "VectorFromCurve")
            inp.update(
                mode="dynamic",
                dynamic={
                    "script": f"/Niagara/DynamicInputs/ValueFromCurve/{di}.{di}",
                    "name": di,
                    "inputs": [
                        {
                            "name": "Curve",
                            "type": "di:NiagaraDataInterfaceCurve",
                            "mode": "data",
                            "modified": True,
                            "data": {"class": "NiagaraDataInterfaceCurve", "curves": {"Curve": keys}},
                        }
                    ],
                },
            )
            return
        if "dynamic" in spec:
            d = spec["dynamic"]
            script = d if isinstance(d, str) else d.get("script", "")
            name = script.rsplit("/", 1)[-1].split(".")[0]
            children = []
            for cname, cspec in (
                spec.get("inputs") or (d.get("inputs") if isinstance(d, dict) else None) or {}
            ).items():
                child = {"name": cname, "type": inp.get("type", "float"), "mode": "local", "modified": False}
                self._set(child, cspec, sys_)
                children.append(child)
            inp.update(mode="dynamic", dynamic={"script": f"{script}.{name}", "name": name, "inputs": children})
            return
        if "data" in spec:
            inp.update(mode="data", data={"class": t[3:], "properties": spec["data"]})
            return
        raise OpError(f"cannot set input '{inp['name']}' from {spec!r}")

    @staticmethod
    def _apply_switch(inp: dict[str, Any]) -> None:
        pass  # visibility of switch-gated inputs isn't modelled; every catalog input is settable

    def _set_path(self, module: dict[str, Any], path: list[str], spec: Any, sys_) -> None:
        inputs = module["inputs"]
        inp = None
        for n, part in enumerate(path):
            inp = self._find_input(inputs, part)
            if n + 1 < len(path):
                if inp.get("mode") != "dynamic":
                    raise OpError(f"input '{part}' is not a dynamic input, so it has no child '{path[n + 1]}'")
                inputs = inp["dynamic"]["inputs"]
                if not any(_k(i["name"]) == _k(path[n + 1]) for i in inputs):
                    inputs.append({"name": path[n + 1], "type": inp["type"], "mode": "local", "modified": False})
        self._set(inp, spec, sys_)

    def _add_emitter(self, sys_: dict[str, Any], op: dict[str, Any]) -> str:
        src = (op.get("from") or TEMPLATE_ROOT + "Minimal").split(".")[0]
        tname = src.rsplit("/", 1)[-1]
        if tname not in CATALOG["emitter_templates"]:
            raise OpError(f"no emitter or emitter template named '{src}'")
        e = copy.deepcopy(CATALOG["emitter_templates"][tname])
        name = op.get("name") or tname
        if any(_k(x["name"]) == _k(name) for x in sys_["emitters"]):
            raise OpError(f"the system already has an emitter named '{name}'")
        e["name"] = name
        idx = op.get("index")
        if idx is None or idx < 0:
            sys_["emitters"].append(e)
        else:
            sys_["emitters"].insert(min(idx, len(sys_["emitters"])), e)
        for i, x in enumerate(sys_["emitters"]):
            x["index"] = i
        return name

    def _run(self, sys_: dict[str, Any], op: dict[str, Any], res: dict[str, Any]) -> None:  # noqa: C901
        kind = op.get("op")
        if kind == "add_emitter":
            res["emitter"] = self._add_emitter(sys_, op)
        elif kind == "remove_emitter":
            sys_["emitters"].remove(self._emitter(sys_, op["emitter"]))
        elif kind == "move_emitter":
            e = self._emitter(sys_, op["emitter"])
            sys_["emitters"].remove(e)
            sys_["emitters"].insert(int(op["index"]), e)
            for i, x in enumerate(sys_["emitters"]):
                x["index"] = i
        elif kind == "set_emitter" or (kind == "set_bounds" and op.get("emitter")):
            e = self._emitter(sys_, op["emitter"])
            if "sim_target" in op:
                e["sim_target"] = "gpu" if str(op["sim_target"]).lower().startswith("gpu") else "cpu"
            for k in ("local_space", "determinism", "random_seed", "enabled"):
                if k in op:
                    e[k] = op[k]
            b = op.get("bounds") or (op if kind == "set_bounds" else None)
            if b:
                if b.get("mode") == "dynamic":
                    e["bounds"] = {"mode": "dynamic"}
                elif "min" not in b or "max" not in b:
                    raise OpError('bounds need "min": [x,y,z] and "max": [x,y,z]')
                else:
                    e["bounds"] = {"mode": "fixed", "fixed": {"min": b["min"], "max": b["max"]}}
            for k, v in (op.get("properties") or {}).items():
                e.setdefault("properties", {})[k] = v
            if op.get("rename"):
                e["name"] = op["rename"]
        elif kind == "add_event_handler":
            e = self._emitter(sys_, op["emitter"])
            self._emitter(sys_, op["source_emitter"])
            n = sum(1 for s in e["stages"] if s["stage"].startswith("event:"))
            mode = _k(op.get("mode", "spawned_particles"))
            e["stages"].append(
                {
                    "stage": f"event:{n}",
                    "event_name": op.get("event", "DeathEvent"),
                    "source_emitter": op["source_emitter"],
                    "execution_mode": {"everyparticle": "EveryParticle", "singleparticle": "SingleParticle"}.get(
                        mode, "SpawnedParticles"
                    ),
                    "spawn_number": op.get("spawn_number", 0),
                    "max_events_per_frame": op.get("max_events_per_frame", 0),
                    "modules": [],
                }
            )
            res["stage"] = f"event:{n}"
        elif kind == "add_module":
            stage = self._stage(sys_, op)
            key = self._module_key(op.get("script") or op.get("module", ""))
            spec = CATALOG["modules"][key]
            allowed = spec.get("stages")
            st = stage["stage"].split(":")[0]
            if allowed and st not in allowed and not (st == "event" and "particle_update" in allowed):
                raise OpError(f"could not add {spec['script']} to {stage['stage']} (wrong stage for this module?)")
            names = {m["name"] for m in stage["modules"]}
            name, n = op.get("name") or spec["name"], 1
            while name in names:
                name, n = f"{op.get('name') or spec['name']}{n:03d}", n + 1
            m = self._module(key, 0, name)
            idx = op.get("index", -1)
            stage["modules"].insert(idx if 0 <= idx < len(stage["modules"]) else len(stage["modules"]), m)
            self._reindex(stage)
            if op.get("enabled") is False:
                m["enabled"] = False
            for k, v in (op.get("inputs") or {}).items():
                self._set_path(m, k.split("/"), v, sys_)
            res.update(module=name, stage=stage["stage"])
        elif kind in ("remove_module", "enable_module", "move_module", "set_inputs", "set_input"):
            stage = self._stage(sys_, op)
            ref = op.get("module", op.get("module_index"))
            if ref is None:
                raise OpError('missing "module"')
            m = self._find_module(stage, ref)
            if kind == "remove_module":
                stage["modules"].remove(m)
                self._reindex(stage)
            elif kind == "enable_module":
                m["enabled"] = bool(op.get("enabled", True))
            elif kind == "move_module":
                stage["modules"].remove(m)
                stage["modules"].insert(max(0, min(int(op["index"]), len(stage["modules"]))), m)
                self._reindex(stage)
            elif kind == "set_input":
                spec = (
                    op["value"]
                    if "value" in op
                    else {
                        k: op[k]
                        for k in (
                            "link",
                            "dynamic",
                            "inputs",
                            "random",
                            "curve",
                            "expression",
                            "object",
                            "data",
                            "reset",
                        )
                        if k in op
                    }
                )
                self._set_path(m, op["input"].split("/"), spec, sys_)
            else:
                for k, v in (op.get("inputs") or {}).items():
                    self._set_path(m, k.split("/"), v, sys_)
        elif kind in ("add_renderer", "set_renderer", "remove_renderer"):
            e = self._emitter(sys_, op["emitter"])
            if kind == "add_renderer":
                t = op.get("type", "sprite")
                r = copy.deepcopy(
                    CATALOG["renderers"].get(t)
                    or {
                        "type": t,
                        "class": f"Niagara{t.title()}RendererProperties",
                        "enabled": True,
                        "materials": [],
                        "bindings": {},
                        "properties": {},
                    }
                )
                e["renderers"].append(r)
            else:
                which = op.get("renderer")
                rs = e["renderers"]
                r = (
                    rs[which]
                    if isinstance(which, int) and which < len(rs)
                    else next((x for x in rs if x["type"] == which), rs[0] if which is None and len(rs) == 1 else None)
                )
                if r is None:
                    raise OpError('renderer not found; pass "renderer": index or type')
                if kind == "remove_renderer":
                    rs.remove(r)
                    for i, x in enumerate(rs):
                        x["index"] = i
                    return
            if "enabled" in op:
                r["enabled"] = op["enabled"]
            if op.get("material"):
                r["materials"] = [op["material"]]
            if op.get("mesh"):
                r["meshes"] = [op["mesh"]]
            for k, v in (op.get("bindings") or {}).items():
                if k not in r["bindings"] and k + "Binding" not in r["bindings"]:
                    raise OpError(f"{r['class']} has no binding '{k}'")
                r["bindings"][k if k in r["bindings"] else k + "Binding"] = v
            r["properties"].update(op.get("properties") or {})
            for i, x in enumerate(e["renderers"]):
                x["index"] = i
            res["renderer"] = e["renderers"].index(r)
        elif kind in ("add_user_parameter", "set_user_parameter", "remove_user_parameter") or (
            kind == "add_parameter" and str(op.get("name", "")).startswith("User.")
        ):
            name = op["name"] if op["name"].startswith("User.") else "User." + op["name"]
            ups = sys_["user_parameters"]
            cur = next((u for u in ups if u["name"] == name), None)
            if kind == "remove_user_parameter":
                if cur is None:
                    raise OpError(f"no user parameter {name}")
                ups.remove(cur)
                return
            t = op.get("type") or (cur["type"] if cur else None)
            if not t:
                raise OpError(f'user parameter {name} needs "type"')
            if cur is None:
                cur = {"name": name, "type": t, "value": op.get("value")}
                ups.append(cur)
            elif "value" in op:
                cur["value"] = op["value"]
        elif kind == "add_parameter":
            name = op["name"]
            ns, _, short = name.partition(".") if "." in name else (op.get("namespace") or "Emitter", "", name)
            stage_name = op.get("stage") or {"System": "system_spawn", "Particles": "particle_spawn"}.get(
                ns, "emitter_spawn"
            )
            stage = self._stage(sys_, {**op, "stage": stage_name})
            full = f"{ns}.{short}"
            stage["modules"].append(
                {
                    "name": "SetParameters" + ("" if not stage["modules"] else f"{len(stage['modules']):03d}"),
                    "index": len(stage["modules"]),
                    "script": "",
                    "set_parameters": True,
                    "enabled": True,
                    "inputs": [
                        {"name": full, "type": op["type"], "mode": "local", "value": op.get("value"), "modified": True}
                    ],
                }
            )
            res.update(parameter=full, module=stage["modules"][-1]["name"])
        elif kind in ("set_system", "set_bounds"):
            b = op if kind == "set_bounds" else op.get("fixed_bounds")
            if b:
                if b.get("mode") == "dynamic":
                    sys_["fixed_bounds_enabled"] = False
                else:
                    sys_.update(fixed_bounds_enabled=True, fixed_bounds={"min": b["min"], "max": b["max"]})
            if "warmup_time" in op:
                sys_["warmup_time"] = op["warmup_time"]
        else:
            raise OpError(f"unknown op '{kind}'")

    # ------------------------------------------------------------------------------------------ particles
    def particles(self, p: dict[str, Any]) -> dict[str, Any]:
        if self.ue is None:
            return {"success": False, "error": "no world"}
        state = self.ue._state
        world = p.get("world") or ("pie" if state.get("pie") else "editor")
        if world == "pie" and not state.get("pie"):
            return {"success": False, "error": "PIE is not running"}
        self.clock += self.dt
        comps = []
        for a in state["actors"]:
            comp = getattr(a, "niagara_component", None)
            if comp is None or comp.asset is None:
                continue
            label = a.get_actor_label()
            if p.get("actor") and label.lower() != p["actor"].lower():
                continue
            path = comp.asset.get_path_name().split(".")[0]
            sys_ = self.systems.get(path)
            loc = a.get_actor_location()
            emitters = self._simulate(sys_, comp, (loc.x, loc.y, loc.z)) if sys_ else []
            comps.append(
                {
                    "actor": label,
                    "component": "NiagaraComponent0",
                    "system": comp.asset.get_path_name(),
                    "active": True,
                    "location": [loc.x, loc.y, loc.z],
                    "emitters": emitters,
                    "particles": sum(e["particles"] for e in emitters),
                }
            )
        return {"world": world, "particles": sum(c["particles"] for c in comps), "components": comps}

    # A deliberately small model of what the reference VFX produce, so PIE checkers can be exercised:
    # continuous emitters hold rate*lifetime particles; bursts are alive for `lifetime` of every loop;
    # event emitters spawn while their source has a death-event generator; particles fall under gravity
    # unless an enabled Collision module stops them at the ground.
    def _simulate(self, sys_, comp, loc) -> list[dict[str, Any]]:
        out = []
        user = {u["name"]: u.get("value") for u in sys_["user_parameters"]}
        user.update({("User." + k[5:] if k.startswith("User.") else "User." + k): v[1] for k, v in comp.params.items()})
        by_name = {e["name"]: e for e in sys_["emitters"]}

        def val(inp):
            if inp is None:
                return None
            if inp.get("mode") == "linked":
                return user.get(inp["link"])
            if inp.get("mode") == "dynamic":
                vals = [val(c) for c in inp["dynamic"]["inputs"]]
                nums = [v for v in vals if isinstance(v, (int, float))]
                return max(nums) if nums else None
            return inp.get("value")

        def mod(e, stage, key):
            for s in e["stages"]:
                if s["stage"] == stage:
                    for m in s["modules"]:
                        if (
                            m["enabled"]
                            and m["script"].split(".")[0] == CATALOG["modules"][key]["script"].split(".")[0]
                        ):
                            return m
            return None

        def inp(m, role):
            if m is None:
                return None
            names = CATALOG["roles"][role]
            for name in names if isinstance(names, list) else [names]:
                hit = next((i for i in m["inputs"] if _k(i["name"]) == _k(name)), None)
                if hit is not None:
                    return hit
            return None

        def emits(e):
            return e.get("enabled", True)

        for e in sys_["emitters"]:
            life = val(inp(mod(e, "particle_spawn", "InitializeParticle"), "lifetime"))
            life = float(life) if isinstance(life, (int, float)) and life > 0 else 1.0
            rate_m = mod(e, "emitter_update", "SpawnRate")
            burst_m = mod(e, "emitter_update", "SpawnBurstInstantaneous")
            loop = val(inp(mod(e, "emitter_update", "EmitterState"), "loop_duration"))
            loop = float(loop) if isinstance(loop, (int, float)) and loop > 0 else 2.0
            count = 0
            if emits(e) and rate_m is not None:
                rate = val(inp(rate_m, "spawn_rate"))
                count = int(max(0.0, float(rate or 0)) * life)
            if emits(e) and burst_m is not None:
                n = val(inp(burst_m, "spawn_count"))
                t = self.clock % loop
                count += int(n or 0) if t < min(life, loop * 0.95) else 0
            for s in e["stages"]:
                if s["stage"].startswith("event:") and emits(e):
                    src = by_name.get(s.get("source_emitter"))
                    if (
                        src
                        and mod(src, "particle_update", "GenerateDeathEvent")
                        and int(s.get("spawn_number") or 0) > 0
                    ):
                        src_life = val(inp(mod(src, "particle_spawn", "InitializeParticle"), "lifetime")) or 1.0
                        if (self.clock % (float(src_life) + life)) >= float(src_life):
                            count += int(s["spawn_number"])
            rec = {"name": e["name"], "particles": count, "state": "Active", "sim_target": e.get("sim_target", "cpu")}
            if count and e.get("sim_target", "cpu") == "cpu":
                zmin = loc[2]
                if mod(e, "particle_update", "GravityForce"):
                    zmin = loc[2] - 0.5 * 980.0 * life * life
                    if mod(e, "particle_update", "Collision"):
                        zmin = max(zmin, GROUND_Z - 2.0)
                rec["positions"] = {
                    "min": [loc[0] - 100, loc[1] - 100, round(zmin, 2)],
                    "max": [loc[0] + 100, loc[1] + 100, loc[2] + 200],
                }
            out.append(rec)
        _ = math  # (kept for future analytic terms)
        return out
