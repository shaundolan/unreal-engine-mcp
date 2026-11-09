# Create/edit a PCG graph from a declarative node/edge spec, place a PCG volume and generate.
# include: _pcg
# ARGS: graph (/Game/.../PCG_Forest), create, clear, nodes [...], edges [...], remove_nodes [ids],
#       volume {label, location, extent | fit, pad_z, seed, generate}, dry_run
#   node: {id, type: "SurfaceSampler" | "PCGSurfaceSamplerSettings", properties {...}, position [x, y],
#          meshes [{mesh, weight}] (StaticMeshSpawner only)}
#   edge: {from: "id.Pin" | "id", to: "id.Pin" | "id"}   ids "input" / "output" are the graph's own nodes
# Atomic: the whole spec is first built on a scratch graph; the asset is only touched if that had no errors.
# Node ids are stored as node titles so later calls (and pcg_inspect) can address them.
# ruff: noqa: F821
_eal = unreal.EditorAssetLibrary
_dry = bool(ARGS.get("dry_run"))
_path = ARGS["graph"].split(".")[0]
_exists = _eal.does_asset_exist(_path)
if not _exists and not ARGS.get("create"):
    raise LookupError(f"PCG graph {_path!r} not found (pass create=true)")
_real = unreal.load_asset(_path) if _exists else None
if _exists and not isinstance(_real, unreal.PCGGraph):
    raise TypeError(f"{_path!r} is not a PCGGraph")


def _by_id(graph, key):
    if graph is None:
        return None
    if key == "input":
        return graph.get_input_node()
    if key == "output":
        return graph.get_output_node()
    for n in graph.get_editor_property("nodes"):
        if node_key(n, graph) == key:
            return n
    return None


def _set_meshes(settings, meshes):
    sel = settings.get_editor_property("mesh_selector_parameters")
    if sel is None or not hasattr(sel, "mesh_entries"):
        settings.set_mesh_selector_type(unreal.PCGMeshSelectorWeighted.static_class())
        sel = settings.get_editor_property("mesh_selector_parameters")
    entries = []
    for m in meshes:
        mesh = unreal.load_asset(m["mesh"])
        if not isinstance(mesh, unreal.StaticMesh):
            raise LookupError(f"static mesh {m['mesh']!r} not found")
        e = unreal.PCGMeshSelectorWeightedEntry()
        d = e.get_editor_property("descriptor")  # PCGSoftISMComponentDescriptor
        d.set_editor_property("static_mesh", mesh)
        e.set_editor_property("descriptor", d)
        e.set_editor_property("weight", int(m.get("weight", 1)))
        entries.append(e)
    sel.set_editor_property("mesh_entries", entries)


def _apply_props(settings, props):
    done = []
    for k, v in (props or {}).items():
        cur = settings.get_editor_property(k)
        settings.set_editor_property(k, coerce(cur, v))
        done.append(k)
    return done


def _build(g, lookup):
    """Apply the spec to graph g. lookup(id) also finds nodes of the real graph (for the scratch pass)."""
    errors, ops = [], []
    if ARGS.get("clear"):
        for n in list(g.get_editor_property("nodes")):
            g.remove_node(n)
    for rid in ARGS.get("remove_nodes") or []:
        n = _by_id(g, rid)
        if rid in ("input", "output") or (n is None and lookup(rid) is None):
            errors.append({"stage": "remove_nodes", "id": rid, "error": "no such node"})
        elif n is not None:
            g.remove_node(n)
            ops.append({"removed": rid})
    x = 300
    for i, spec in enumerate(ARGS.get("nodes") or []):
        try:
            nid = spec.get("id") or f"node{i}"
            if nid in ("input", "output"):
                raise ValueError("'input' and 'output' are reserved ids")
            cls = pcg_settings_class(spec.get("type", ""))
            n = _by_id(g, nid)
            if n is not None and n.get_settings().get_class() != cls:
                g.remove_node(n)
                n = None
            added = n is None
            if added:
                n, s = g.add_node_of_type(cls)
                n.set_editor_property("node_title", nid)
            else:
                s = n.get_settings()
            done = _apply_props(s, spec.get("properties"))
            if spec.get("meshes"):
                _set_meshes(s, spec["meshes"])
                done.append("meshes")
            pos = spec.get("position") or [x, 0]
            n.set_node_position(int(pos[0]), int(pos[1]))
            x += 300
            ops.append({"node": nid, "added": added, "properties_set": done})
        except Exception as e:
            errors.append({"stage": "nodes", "index": i, "id": spec.get("id"), "error": f"{type(e).__name__}: {e}"})
    for i, ed in enumerate(ARGS.get("edges") or []):
        try:
            a_id, _, a_pin = ed["from"].partition(".")
            b_id, _, b_pin = ed["to"].partition(".")
            a, b = _by_id(g, a_id) or lookup(a_id), _by_id(g, b_id) or lookup(b_id)
            if a is None or b is None:
                raise LookupError(f"unknown node {(a_id if a is None else b_id)!r}")
            outs, ins = pin_labels(a)[1], pin_labels(b)[0]
            a_pin = a_pin or (outs[0] if outs else "Out")
            b_pin = b_pin or (ins[0] if ins else "In")
            if a_pin not in outs:
                raise LookupError(f"{a_id} has no output pin {a_pin!r} (outputs: {outs})")
            if b_pin not in ins:
                raise LookupError(f"{b_id} has no input pin {b_pin!r} (inputs: {ins})")
            if a.get_graph() == g and b.get_graph() == g:
                g.add_edge(a, a_pin, b, b_pin)
            ops.append({"edge": f"{a_id}.{a_pin} -> {b_id}.{b_pin}"})
        except Exception as e:
            errors.append({"stage": "edges", "index": i, "error": f"{type(e).__name__}: {e}"})
    return errors, ops


_scratch = unreal.new_object(unreal.PCGGraph)
_keep_existing = _real is not None and not ARGS.get("clear")
_errors, _ops = _build(_scratch, (lambda k: _by_id(_real, k)) if _keep_existing else (lambda k: None))
result = {"graph": {"path": _path, "nodes": describe_graph(_scratch)["nodes"]}, "applied": [], "errors": _errors,
          "dry_run": _dry, "created": False}
if not _errors and not _dry:
    if _real is None:
        _pkg, _name = _path.rsplit("/", 1)
        _real = unreal.AssetToolsHelpers.get_asset_tools().create_asset(_name, _pkg, unreal.PCGGraph,
                                                                         unreal.PCGGraphFactory())
        result["created"] = True
    _errors2, _ops = _build(_real, lambda k: None)
    _real.force_notification_for_editor()
    _eal.save_loaded_asset(_real, only_if_is_dirty=False)
    result.update(graph=describe_graph(_real), applied=_ops, errors=_errors2)
elif _dry:
    result["applied"] = _ops

_vol = ARGS.get("volume")
if _vol and not _dry and not result["errors"]:
    _label = _vol.get("label") or "PCGVolume"
    _v = find_actor(_label)
    if _v is None:
        _v = actor_sub().spawn_actor_from_class(unreal.PCGVolume, unreal.Vector(0, 0, 0), unreal.Rotator())
        _v.set_actor_label(_label)
    if _vol.get("fit"):
        _o, _ext = require_actor(_vol["fit"]).get_actor_bounds(False)
        _pad = float(_vol.get("pad_z", 1000.0))
        _loc, _ext = _o, unreal.Vector(_ext.x, _ext.y, _ext.z + _pad)
    else:
        _loc = vec(_vol.get("location"))
        _ext = vec(_vol.get("extent") or [5000, 5000, 2000])
    _v.set_actor_location(_loc, False, False)
    _v.set_actor_scale3d(unreal.Vector(_ext.x / 100.0, _ext.y / 100.0, _ext.z / 100.0))  # default brush is 200 cm
    _c = _v.get_editor_property("pcg_component")
    _c.set_graph(_real)
    if _vol.get("seed") is not None:
        _c.set_editor_property("seed", int(_vol["seed"]))
    if _vol.get("generate", True):
        _c.cleanup(True)
        _c.generate(True)
    result["volume"] = pcg_volume_info(_v)
    result["generation_requested"] = bool(_vol.get("generate", True))
