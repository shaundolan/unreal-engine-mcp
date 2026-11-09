# PCG helpers (prepended to pcg_graph / pcg_inspect).
# ruff: noqa: F821


_PCG_ALIASES = {"getlandscapedata": "PCGGetLandscapeSettings", "getlandscape": "PCGGetLandscapeSettings",
                "getactordata": "PCGDataFromActorSettings", "getsplinedata": "PCGGetSplineSettings",
                "getvolumedata": "PCGGetVolumeSettings", "getprimitivedata": "PCGGetPrimitiveSettings",
                "spawnactor": "PCGSpawnActorSettings", "subgraph": "PCGSubgraphSettings"}


def pcg_settings_class(name):
    """'SurfaceSampler' | 'PCGSurfaceSamplerSettings' | 'StaticMeshSpawner' -> settings UClass."""
    base = name.split(".")[-1]
    base = _PCG_ALIASES.get(base.lower().replace(" ", "").replace("_", ""), base)
    for cand in (base, f"PCG{base}", f"{base}Settings", f"PCG{base}Settings"):
        cls = getattr(unreal, cand, None)
        if cls is not None and hasattr(cls, "static_class"):
            try:
                if issubclass(cls, unreal.PCGSettings):
                    return cls.static_class()
            except TypeError:
                pass
    raise LookupError(f"no PCG settings class for {name!r} (e.g. SurfaceSampler, StaticMeshSpawner, DensityFilter, "
                      f"GetLandscapeData -> PCGGetLandscapeSettings, TransformPoints, SelfPruning)")


def pin_labels(node):
    def lab(p):
        return str(p.get_editor_property("properties").get_editor_property("label"))
    return ([lab(p) for p in node.get_editor_property("input_pins")],
            [lab(p) for p in node.get_editor_property("output_pins")])


def node_key(node, graph):
    if node == graph.get_input_node():
        return "input"
    if node == graph.get_output_node():
        return "output"
    t = str(node.get_editor_property("node_title"))
    return t if t and t != "None" else node.get_name()


def short_settings(node):
    s = node.get_settings()
    if s is None:
        return ""
    n = s.get_class().get_name()
    if n.startswith("PCG"):
        n = n[3:]
    if n.endswith("Settings"):
        n = n[:-8]
    return n


def graph_edges(graph):
    out = []
    nodes = list(graph.get_editor_property("nodes")) + [graph.get_input_node(), graph.get_output_node()]
    for n in nodes:
        for p in n.get_editor_property("output_pins"):
            src = str(p.get_editor_property("properties").get_editor_property("label"))
            for e in p.get_editor_property("edges"):
                other = e.get_editor_property("output_pin")
                if other is None or other == p:
                    other = e.get_editor_property("input_pin")
                if other is None:
                    continue
                tgt_node = other.get_editor_property("node")
                dst = str(other.get_editor_property("properties").get_editor_property("label"))
                out.append({"from": f"{node_key(n, graph)}.{src}", "to": f"{node_key(tgt_node, graph)}.{dst}"})
    return out


def describe_graph(graph):
    nodes = []
    for n in [graph.get_input_node()] + list(graph.get_editor_property("nodes")) + [graph.get_output_node()]:
        ins, outs = pin_labels(n)
        pos = n.get_node_position()
        nodes.append({"id": node_key(n, graph), "type": short_settings(n) or ("Input" if n == graph.get_input_node()
                                                                             else "Output"),
                      "inputs": ins, "outputs": outs, "position": list(pos) if isinstance(pos, tuple) else pos})
    return {"path": graph.get_path_name(), "nodes": nodes, "edges": graph_edges(graph)}


def pcg_volume_info(vol):
    comp = vol.get_editor_property("pcg_component")
    g = comp.get_graph() if comp else None
    ism = 0
    comps = 0
    meshes = {}
    for c in vol.get_components_by_class(unreal.InstancedStaticMeshComponent):
        n = c.get_instance_count()
        ism += n
        comps += 1
        m = c.get_editor_property("static_mesh")
        meshes[m.get_path_name() if m else ""] = meshes.get(m.get_path_name() if m else "", 0) + n
    origin, extent = vol.get_actor_bounds(False)
    return {"label": vol.get_actor_label(), "graph": g.get_path_name() if g else None,
            "generated": bool(comp.get_editor_property("generated")) if comp else False,
            "seed": comp.get_editor_property("seed") if comp else None,
            "instances": ism, "instance_components": comps, "meshes": meshes,
            "bounds": {"origin": v3(origin), "extent": v3(extent)}}
