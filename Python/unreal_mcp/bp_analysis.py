"""Static analysis of a Blueprint from its bp_export spec: what it does, and what's wrong with it.

Pure Python over the JSON that ``bp_export`` returns (graphs, nodes, pins with types and links), so it
runs in the MCP server, needs no editor-side code, and is unit-testable.

``explain(spec)``: entry points, the execution flow of each one, variables read and written, external
calls, and a dependency graph. ``review(spec)``: lint findings (unwired execs, unused variables,
Tick-heavy logic, casts in Tick, missing IsValid, infinite-loop risks). Both return JSON plus a short
markdown summary.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

ENTRY_KINDS = {"event", "custom_event", "component_event", "key_event", "function_entry", "macro_entry"}
LATENT_NODE_KINDS = {"timeline", "async_action"}
LATENT_FUNCTIONS = {
    "delay",
    "retriggerabledelay",
    "moveto",
    "aimoveto",
    "loadasset",
    "delayuntilnexttick",
    "movecomponentto",
    "playmontage",
}
EXPENSIVE_IN_TICK = {
    "getallactorsofclass": "scans every actor in the level",
    "getallactorsofclasswithtag": "scans every actor in the level",
    "getallactorswithtag": "scans every actor in the level",
    "getallactorswithinterface": "scans every actor in the level",
    "getactorofclass": "scans actors until it finds one",
    "findcomponentbyclass": "walks the actor's components",
    "getcomponentsbyclass": "walks the actor's components",
    "spawnactor": "spawns an actor every frame",
    "beginspawningactorfromclass": "spawns an actor every frame",
    "createwidget": "creates a widget every frame",
    "loadasset": "loads assets every frame",
    "loadobject": "loads assets every frame",
    "printstring": "prints every frame (debug left in?)",
    "printtext": "prints every frame (debug left in?)",
}
TICK_EVENTS = {"receivetick", "tick", "eventtick"}
TICK_NODE_BUDGET = 15  # nodes run per frame from Tick before we call it heavy


def _norm(s: str | None) -> str:
    return (s or "").replace(" ", "").replace("_", "").lower()


def _fn_base(name: str | None) -> str:
    n = _norm(name)
    return n[2:] if n.startswith("k2") else n


@dataclass
class _Node:
    raw: dict[str, Any]
    graph: str

    @property
    def id(self) -> str:
        return self.raw.get("id", "")

    @property
    def kind(self) -> str:
        return self.raw.get("kind", "node")

    @property
    def title(self) -> str:
        return self.raw.get("title") or self.raw.get("function") or self.raw.get("event") or self.kind

    @property
    def disabled(self) -> bool:
        return bool(self.raw.get("disabled"))

    @property
    def pins(self) -> list[dict[str, Any]]:
        return self.raw.get("pins") or []

    def exec_ins(self) -> list[dict[str, Any]]:
        return [p for p in self.pins if p.get("type") == "exec" and p.get("dir") == "in"]

    def exec_outs(self) -> list[dict[str, Any]]:
        return [p for p in self.pins if p.get("type") == "exec" and p.get("dir") == "out"]

    def data_ins(self) -> list[dict[str, Any]]:
        return [p for p in self.pins if p.get("type") != "exec" and p.get("dir") == "in"]

    @property
    def pure(self) -> bool:
        if "pure" in self.raw:
            return bool(self.raw["pure"])
        return not self.exec_ins() and not self.exec_outs()

    @property
    def is_entry(self) -> bool:
        return self.kind in ENTRY_KINDS or (self.kind == "node" and "event" in _norm(self.raw.get("class")))

    @property
    def latent(self) -> bool:
        return (
            self.kind in LATENT_NODE_KINDS
            or bool(self.raw.get("latent"))
            or (self.kind == "call_function" and _fn_base(self.raw.get("function")) in LATENT_FUNCTIONS)
        )

    def label(self) -> str:
        k, r = self.kind, self.raw
        if k in ("get_var", "set_var"):
            return f"{'Get' if k == 'get_var' else 'Set'} {r.get('variable')}"
        if k == "call_function":
            return f"{r.get('function')}"
        if k in ("event", "component_event"):
            comp = f"{r.get('component')}." if r.get("component") else ""
            return f"Event {comp}{r.get('event')}"
        if k == "custom_event":
            return f"Custom Event {r.get('name')}"
        if k == "cast":
            return f"Cast to {str(r.get('class', '?')).rsplit('.', 1)[-1]}"
        if k in ("call_dispatcher", "bind_dispatcher", "unbind_dispatcher", "clear_dispatcher"):
            return f"{k.split('_')[0].capitalize()} {r.get('dispatcher')}"
        if k == "interface_message":
            return f"{r.get('function')} (Message)"
        if k == "timeline":
            return f"Timeline {r.get('name')}"
        if k == "macro":
            return str(r.get("macro"))
        return self.title


def _split_link(link: str) -> tuple[str, str]:
    node, _, pin = link.partition(".")
    return node, pin


@dataclass
class _Graph:
    nodes: dict[str, _Node] = field(default_factory=dict)
    graph_of: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, spec: dict[str, Any]) -> _Graph:
        g = cls()
        for graph in spec.get("graphs") or []:
            for raw in graph.get("nodes") or []:
                n = _Node(raw, graph.get("name", "?"))
                g.nodes[n.id] = n
        return g

    def exec_targets(self, n: _Node) -> list[tuple[str, _Node]]:
        out = []
        for p in n.exec_outs():
            for link in p.get("links") or []:
                tid, _ = _split_link(link)
                if tid in self.nodes:
                    out.append((p.get("name", ""), self.nodes[tid]))
        return out

    def data_sources(self, n: _Node) -> list[tuple[dict[str, Any], _Node, str]]:
        """(input pin, source node, source pin) for each linked data input."""
        out = []
        for p in n.data_ins():
            for link in p.get("links") or []:
                sid, spin = _split_link(link)
                if sid in self.nodes:
                    out.append((p, self.nodes[sid], spin))
        return out

    def pure_closure(self, n: _Node, seen: set[str] | None = None) -> list[_Node]:
        """Pure nodes that feed n's data inputs (evaluated when n runs), transitively."""
        seen = set() if seen is None else seen
        out: list[_Node] = []
        for _, src, _ in self.data_sources(n):
            if src.id in seen or not src.pure:
                continue
            seen.add(src.id)
            out.append(src)
            out.extend(self.pure_closure(src, seen))
        return out


@dataclass
class _Flow:
    entry: _Node
    steps: list[tuple[int, str, _Node]] = field(default_factory=list)  # depth, via pin, node
    back_edges: list[tuple[_Node, _Node]] = field(default_factory=list)
    evaluated: list[_Node] = field(default_factory=list)  # pure nodes pulled by the steps


def _walk(g: _Graph, entry: _Node) -> _Flow:
    flow = _Flow(entry)
    visited: set[str] = set()
    on_path: list[str] = []

    def visit(n: _Node, depth: int, via: str) -> None:
        if n.id in on_path:
            flow.back_edges.append((g.nodes[on_path[-1]], n))
            return
        if n.id in visited:
            return
        visited.add(n.id)
        on_path.append(n.id)
        flow.steps.append((depth, via, n))
        targets = g.exec_targets(n)
        for pin, t in targets:
            visit(t, depth + (1 if len(targets) > 1 else 0), pin)
        on_path.pop()

    visit(entry, 0, "")
    seen: set[str] = set()
    for _, _, n in flow.steps:
        for p in g.pure_closure(n, seen):
            flow.evaluated.append(p)
    return flow


def _entry_name(n: _Node) -> str:
    r = n.raw
    if n.kind == "function_entry":
        return f"function {n.graph}"
    if n.kind == "macro_entry":
        return f"macro {n.graph}"
    if n.kind == "key_event":
        return f"key {r.get('key')}"
    if n.kind == "component_event":
        return f"{r.get('component')}.{r.get('event')}"
    if n.kind == "custom_event":
        return str(r.get("name"))
    return str(r.get("event") or n.title)


def _is_tick(n: _Node) -> bool:
    return n.kind == "event" and _norm(n.raw.get("event")) in TICK_EVENTS


def _call_target(n: _Node) -> str | None:
    r = n.raw
    if n.kind == "call_function":
        return r.get("class")
    if n.kind == "interface_message":
        return r.get("interface")
    if n.kind in ("call_dispatcher", "bind_dispatcher", "unbind_dispatcher", "clear_dispatcher"):
        return r.get("class")
    if n.kind in ("cast", "make_struct", "break_struct"):
        return r.get("class") or r.get("struct")
    if n.kind == "get_data_table_row":
        return r.get("table")
    return None


def _short(path: str | None) -> str:
    return (path or "").rsplit("/", 1)[-1].rsplit(".", 1)[-1]


def explain(spec: dict[str, Any]) -> dict[str, Any]:
    g = _Graph.build(spec)
    bp_name = _short(spec.get("path"))
    own = {_norm(bp_name), _norm(bp_name + "_C"), "", "self"}
    entries = [n for n in g.nodes.values() if n.is_entry and not n.disabled]
    flows = [_walk(g, e) for e in entries]

    var_reads: dict[str, set[str]] = defaultdict(set)
    var_writes: dict[str, set[str]] = defaultdict(set)
    entry_out = []
    for f in flows:
        name = _entry_name(f.entry)
        nodes = [n for _, _, n in f.steps] + f.evaluated
        reads = sorted({n.raw.get("variable") for n in nodes if n.kind == "get_var" and not n.raw.get("local")})
        writes = sorted({n.raw.get("variable") for n in nodes if n.kind == "set_var" and not n.raw.get("local")})
        for v in reads:
            var_reads[v].add(name)
        for v in writes:
            var_writes[v].add(name)
        calls = []
        for n in nodes:
            if n.kind in (
                "call_function",
                "interface_message",
                "call_dispatcher",
                "cast",
                "spawn_actor",
                "async_action",
            ):
                tgt = _call_target(n)
                calls.append(n.label() + (f" ({_short(tgt)})" if tgt and _norm(_short(tgt)) not in own else ""))
        steps = [{"depth": d, "via": via, "id": n.id, "kind": n.kind, "label": n.label()} for d, via, n in f.steps[1:]]
        summary = " → ".join(s["label"] for s in steps[:8]) + (" → …" if len(steps) > 8 else "")
        entry_out.append(
            {
                "name": name,
                "kind": f.entry.kind,
                "graph": f.entry.graph,
                "id": f.entry.id,
                "steps": steps,
                "flow": summary or "(does nothing)",
                "reads": reads,
                "writes": writes,
                "calls": list(dict.fromkeys(calls)),
                "latent": any(n.latent for _, _, n in f.steps),
            }
        )

    # Variables: declared ones plus inherited ones that nodes touch.
    declared = {v["name"]: v for v in spec.get("variables") or []}
    all_reads = {n.raw.get("variable") for n in g.nodes.values() if n.kind == "get_var" and not n.raw.get("local")}
    all_writes = {n.raw.get("variable") for n in g.nodes.values() if n.kind == "set_var" and not n.raw.get("local")}
    variables = []
    for name in sorted(set(declared) | all_reads | all_writes, key=str):
        variables.append(
            {
                "name": name,
                "type": declared.get(name, {}).get("type", "inherited"),
                "declared": name in declared,
                "read": name in all_reads,
                "written": name in all_writes,
                "read_by": sorted(var_reads.get(name, ())),
                "written_by": sorted(var_writes.get(name, ())),
            }
        )

    # External calls and the dependency graph (this Blueprint -> classes / assets it uses).
    deps: dict[str, set[str]] = defaultdict(set)
    external: dict[str, set[str]] = defaultdict(set)
    if spec.get("parent_class"):
        deps[spec["parent_class"]].add("parent")
    for i in spec.get("interfaces") or []:
        deps[i].add("implements")
    for c in spec.get("components") or []:
        if c.get("class"):
            deps[c["class"]].add("component")
    for n in g.nodes.values():
        tgt = _call_target(n)
        if tgt and _norm(_short(tgt)) not in own:
            rel = {
                "call_function": "calls",
                "interface_message": "messages",
                "cast": "casts",
                "get_data_table_row": "reads table",
            }.get(n.kind, "uses")
            if n.kind.endswith("dispatcher"):
                rel = "dispatcher"
            deps[tgt].add(rel)
            if n.kind in ("call_function", "interface_message") or n.kind.endswith("dispatcher"):
                external[_short(tgt)].add(n.raw.get("function") or n.raw.get("dispatcher") or n.label())
        for p in n.pins:
            d = str(p.get("default") or "")
            if d.startswith("/Game/"):
                deps[d].add("asset")
    dependency_graph = {
        "nodes": [spec.get("path", bp_name)] + sorted(deps),
        "edges": [
            {"from": spec.get("path", bp_name), "to": t, "kind": ",".join(sorted(k))} for t, k in sorted(deps.items())
        ],
    }
    out = {
        "blueprint": spec.get("path"),
        "parent_class": spec.get("parent_class"),
        "status": spec.get("status"),
        "blueprint_type": spec.get("blueprint_type", "normal"),
        "counts": {
            "graphs": len(spec.get("graphs") or []),
            "nodes": len(g.nodes),
            "variables": len(declared),
            "components": len(spec.get("components") or []),
            "entry_points": len(entry_out),
        },
        "interfaces": spec.get("interfaces") or [],
        "dispatchers": [d.get("name") for d in spec.get("dispatchers") or []],
        "timelines": [t.get("name") for t in spec.get("timelines") or []],
        "components": [{"name": c.get("name"), "class": c.get("class")} for c in spec.get("components") or []],
        "entry_points": entry_out,
        "variables": variables,
        "external_calls": {k: sorted(v) for k, v in sorted(external.items())},
        "dependencies": dependency_graph,
    }
    out["markdown"] = _explain_md(out)
    return out


def _explain_md(e: dict[str, Any]) -> str:
    c = e["counts"]
    lines = [
        f"### {_short(e['blueprint'])} ({_short(e['parent_class'])}, {e['status']})",
        f"{c['nodes']} nodes in {c['graphs']} graphs, {c['variables']} variables, {c['components']} components.",
    ]
    if e["components"]:
        lines.append("Components: " + ", ".join(f"{x['name']} ({x['class']})" for x in e["components"][:12]))
    if e["interfaces"]:
        lines.append("Implements: " + ", ".join(_short(i) for i in e["interfaces"]))
    if e["dispatchers"] or e["timelines"]:
        extra = [
            f"dispatchers {', '.join(e['dispatchers'])}" if e["dispatchers"] else "",
            f"timelines {', '.join(e['timelines'])}" if e["timelines"] else "",
        ]
        lines.append("Has " + "; ".join(x for x in extra if x) + ".")
    lines.append("")
    lines.append("**Entry points**")
    for ep in e["entry_points"]:
        rw = []
        if ep["reads"]:
            rw.append("reads " + ", ".join(ep["reads"]))
        if ep["writes"]:
            rw.append("writes " + ", ".join(ep["writes"]))
        lines.append(f"- `{ep['name']}`: {ep['flow']}" + (f" ({'; '.join(rw)})" if rw else ""))
    if e["external_calls"]:
        lines.append("")
        lines.append(
            "**Calls out to** " + "; ".join(f"{k}: {', '.join(v[:6])}" for k, v in e["external_calls"].items())
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------------------------


def _finding(rule: str, severity: str, message: str, node: _Node | None = None, **extra: Any) -> dict[str, Any]:
    f = {"rule": rule, "severity": severity, "message": message}
    if node is not None:
        f.update(graph=node.graph, node=node.id, node_label=node.label())
    f.update(extra)
    return f


def _is_valid_guard(n: _Node) -> bool:
    return (n.kind == "macro" and _norm(n.raw.get("macro")) == "isvalid") or (
        n.kind == "call_function"
        and _fn_base(n.raw.get("function")) in ("isvalid", "isvalidclass", "isvalidsoftobjectreference")
    )


def review(spec: dict[str, Any], tick_budget: int = TICK_NODE_BUDGET) -> dict[str, Any]:
    g = _Graph.build(spec)
    e = explain(spec)
    findings: list[dict[str, Any]] = []
    entries = [n for n in g.nodes.values() if n.is_entry and not n.disabled]
    flows = {n.id: _walk(g, n) for n in entries}
    reached = {n.id for f in flows.values() for _, _, n in f.steps}
    pulled = {n.id for f in flows.values() for n in f.evaluated}

    if spec.get("status") == "error":
        findings.append(_finding("compile_error", "error", "the Blueprint does not compile: fix compile errors first"))

    # Unwired execs: an impure node nothing executes never runs (and its outputs stay empty).
    for n in g.nodes.values():
        if n.disabled or n.is_entry or n.pure or not n.exec_ins():
            continue
        if not any(p.get("links") for p in n.exec_ins()):
            findings.append(
                _finding("unwired_exec", "warning", f"'{n.label()}' has no exec input wired: it never runs", n)
            )
    # Pure nodes that feed nothing.
    for n in g.nodes.values():
        if n.pure and not n.disabled and n.id not in pulled and n.kind not in ("self",):
            if not any(p.get("links") for p in n.pins if p.get("dir") == "out"):
                findings.append(_finding("dead_node", "info", f"'{n.label()}' is not connected to anything", n))

    # Variables never read / never used.
    for v in e["variables"]:
        if not v["declared"]:
            continue
        if not v["read"] and not v["written"]:
            findings.append(
                _finding("unused_variable", "warning", f"variable '{v['name']}' is never used", variable=v["name"])
            )
        elif v["written"] and not v["read"]:
            findings.append(
                _finding(
                    "write_only_variable",
                    "info",
                    f"variable '{v['name']}' is set but never read in this Blueprint",
                    variable=v["name"],
                )
            )

    # Tick.
    for f in flows.values():
        if not _is_tick(f.entry):
            continue
        nodes = [n for _, _, n in f.steps[1:]] + f.evaluated
        if len(nodes) > tick_budget:
            findings.append(
                _finding(
                    "tick_heavy",
                    "warning",
                    f"Event Tick runs {len(nodes)} nodes every frame (budget {tick_budget}): move work to events, "
                    "timers (Set Timer by Event) or a lower tick interval",
                    f.entry,
                    node_count=len(nodes),
                )
            )
        for n in nodes:
            fn = _fn_base(n.raw.get("function"))
            if n.kind == "cast":
                findings.append(
                    _finding(
                        "cast_in_tick",
                        "warning",
                        f"'{n.label()}' runs every frame: cast once (BeginPlay) and store the result in a variable",
                        n,
                    )
                )
            elif n.kind == "call_function" and fn in EXPENSIVE_IN_TICK:
                findings.append(
                    _finding("expensive_in_tick", "warning", f"'{n.label()}' in Tick {EXPENSIVE_IN_TICK[fn]}", n)
                )
            elif n.kind == "spawn_actor":
                findings.append(
                    _finding("expensive_in_tick", "warning", "Spawn Actor in Tick spawns an actor every frame", n)
                )
            elif n.latent:
                findings.append(
                    _finding(
                        "latent_in_tick",
                        "warning",
                        f"'{n.label()}' is latent: in Tick it restarts or is ignored every frame",
                        n,
                    )
                )

    # Object references used as a call target without an IsValid check.
    # Components (and inherited ones like CharacterMovement / Mesh) are created with the actor: always valid.
    guarded: set[str] = {c.get("name") for c in spec.get("components") or []}
    declared_vars = {v.get("name") for v in spec.get("variables") or []}
    for n in g.nodes.values():
        if _is_valid_guard(n):
            for _, src, _ in g.data_sources(n):
                if src.kind == "get_var":
                    guarded.add(src.raw.get("variable"))
    for n in g.nodes.values():
        if n.disabled or n.pure or n.id not in reached:
            continue
        for pin, src, spin in g.data_sources(n):
            if _norm(pin.get("name")) not in ("self", "target") or src.kind != "get_var":
                continue
            var = src.raw.get("variable")
            src_pin = next((p for p in src.pins if p.get("dir") == "out" and p.get("name") == spin), {})
            if not str(src_pin.get("type", "")).startswith(("object", "interface")) or var in guarded:
                continue
            if var not in declared_vars and str(src_pin.get("type", "")).endswith("Component"):
                continue  # an inherited component
            findings.append(
                _finding(
                    "missing_is_valid",
                    "warning",
                    f"'{n.label()}' uses '{var}' as its target without an IsValid check: if it is None "
                    "(never set, or the actor was destroyed) this fails with 'Accessed None'",
                    n,
                    variable=var,
                )
            )
            guarded.add(var)  # once per variable

    # Infinite-loop risks.
    for f in flows.values():
        for frm, to in f.back_edges:
            path_nodes = [n for _, _, n in f.steps]
            if not any(n.latent for n in path_nodes):
                findings.append(
                    _finding(
                        "infinite_loop_risk",
                        "error",
                        f"exec loops from '{frm.label()}' back to '{to.label()}' with no latent node (Delay, "
                        "Timeline) in between: it can hang the game thread",
                        to,
                    )
                )
        for _, _, n in f.steps:
            if n.kind == "macro" and _norm(n.raw.get("macro")) == "whileloop":
                findings.append(
                    _finding(
                        "infinite_loop_risk",
                        "warning",
                        "While Loop: make sure its condition becomes false inside the loop body "
                        "(the runaway-loop limit stops it at 1,000,000 iterations)",
                        n,
                    )
                )
            if n.kind == "macro" and _norm(n.raw.get("macro")) in ("forloop", "forloopwithbreak"):
                last = next((p.get("default") for p in n.pins if _norm(p.get("name")) == "lastindex"), None)
                try:
                    if last is not None and int(float(last)) > 100000:
                        findings.append(
                            _finding("infinite_loop_risk", "warning", f"For Loop to {last} iterations in one frame", n)
                        )
                except ValueError:
                    pass
        if f.entry.kind == "custom_event":
            me = _norm(f.entry.raw.get("name"))
            for _, _, n in f.steps:
                if (
                    n.kind == "call_function"
                    and _norm(n.raw.get("function")) == me
                    and not any(x.latent for _, _, x in f.steps)
                ):
                    findings.append(
                        _finding(
                            "infinite_loop_risk",
                            "error",
                            f"custom event '{f.entry.raw.get('name')}' calls itself with no delay: unbounded recursion",
                            n,
                        )
                    )

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda x: (order.get(x["severity"], 3), x["rule"]))
    counts = {s: sum(1 for x in findings if x["severity"] == s) for s in ("error", "warning", "info")}
    out = {
        "blueprint": spec.get("path"),
        "findings": findings,
        "counts": counts,
        "ok": counts["error"] == 0 and counts["warning"] == 0,
        "explain": {k: e[k] for k in ("counts", "entry_points", "variables", "external_calls")},
    }
    out["markdown"] = _review_md(out, e)
    return out


def _review_md(r: dict[str, Any], e: dict[str, Any]) -> str:
    c = r["counts"]
    lines = [f"### Review of {_short(r['blueprint'])}: {c['error']} errors, {c['warning']} warnings, {c['info']} notes"]
    if not r["findings"]:
        lines.append("No issues found.")
    for f in r["findings"][:30]:
        where = f" ({f['graph']})" if f.get("graph") else ""
        lines.append(f"- **{f['severity']}** `{f['rule']}`{where}: {f['message']}")
    if len(r["findings"]) > 30:
        lines.append(f"- … {len(r['findings']) - 30} more in `findings`")
    lines.append("")
    lines.append(_explain_md(e))
    return "\n".join(lines)
