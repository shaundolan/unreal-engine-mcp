# Instanced static meshes in bulk: add an ISM/HISM component to an actor (created if missing) and add instances.
# ARGS: actor (label), mesh (asset path), component (name; default: the actor's instanced component using
#       mesh, else a new one), hism (bool), transforms [...], clear (bool), cull_distance, cull_start, cast_shadow,
#       collision (bool), min_lod, materials {slot index: path}, folder, location (new actor), world_space
#   transform: [x,y,z] | [x,y,z,yaw] | [x,y,z,yaw,scale or [sx,sy,sz]] |
#              {"location":[..], "rotation":[pitch,yaw,roll], "scale": n | [..]}
# ruff: noqa: F821


def _xf(t):
    if isinstance(t, dict):
        s = t.get("scale", 1.0)
        return unreal.Transform(location=vec(t.get("location")), rotation=rot(t.get("rotation")),
                                scale=vec(s, (1.0, 1.0, 1.0)))
    t = list(t)
    if len(t) < 3:
        raise ValueError(f"transform {t!r}: need at least [x, y, z]")
    yaw = float(t[3]) if len(t) > 3 else 0.0
    s = t[4] if len(t) > 4 else 1.0
    return unreal.Transform(location=vec(t[:3]), rotation=unreal.Rotator(roll=0.0, pitch=0.0, yaw=yaw), scale=vec(s))


def _new_component(actor, cls):
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    root = sds.k2_gather_subobject_data_for_instance(actor)[0]
    handle, fail = sds.add_new_subobject(unreal.AddNewSubobjectParams(parent_handle=root, new_class=cls,
                                                                      blueprint_context=None))
    if fail and str(fail):
        raise RuntimeError(f"could not add {cls.__name__}: {fail}")
    return lib.get_object(lib.get_data(handle))


def _place():
    label = ARGS.get("actor")
    if not label:
        raise ValueError("actor (label) is required")
    cls = unreal.HierarchicalInstancedStaticMeshComponent if ARGS.get("hism", True) else unreal.InstancedStaticMeshComponent
    mesh = None
    if ARGS.get("mesh"):
        mesh = unreal.load_asset(ARGS["mesh"])
        if not isinstance(mesh, unreal.StaticMesh):
            raise LookupError(f"static mesh {ARGS['mesh']!r} not found")
    created_actor = False
    actor = find_actor(label)
    if actor is None:
        actor = actor_sub().spawn_actor_from_class(unreal.Actor, vec(ARGS.get("location")), rot(None))
        actor.set_actor_label(label)
        created_actor = True
    if ARGS.get("folder"):
        actor.set_folder_path(ARGS["folder"])
    comps = list(actor.get_components_by_class(unreal.InstancedStaticMeshComponent))
    comp = None
    if ARGS.get("component"):
        comp = next((c for c in comps if c.get_name().lower() == str(ARGS["component"]).lower()), None)
    elif mesh is not None:
        comp = next((c for c in comps if c.get_editor_property("static_mesh") == mesh), None)
    elif len(comps) == 1:
        comp = comps[0]
    created_comp = False
    if comp is None:
        if mesh is None:
            raise ValueError("mesh is required to create a new instanced component")
        comp = _new_component(actor, cls)
        created_comp = True
        comp.set_editor_property("mobility", unreal.ComponentMobility.STATIC)
    if mesh is not None and comp.get_editor_property("static_mesh") != mesh:
        comp.set_static_mesh(mesh)
    cleared = 0
    if ARGS.get("clear"):
        cleared = comp.get_instance_count()
        comp.clear_instances()
    if ARGS.get("cull_distance") is not None:
        comp.set_editor_property("instance_end_cull_distance", int(ARGS["cull_distance"]))
    if ARGS.get("cull_start") is not None:
        comp.set_editor_property("instance_start_cull_distance", int(ARGS["cull_start"]))
    if ARGS.get("cast_shadow") is not None:
        comp.set_editor_property("cast_shadow", bool(ARGS["cast_shadow"]))
    if ARGS.get("min_lod") is not None:
        comp.set_editor_property("min_lod", int(ARGS["min_lod"]))
    if ARGS.get("collision") is not None:
        comp.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS if ARGS["collision"]
                                   else unreal.CollisionEnabled.NO_COLLISION)
    for idx, path in (ARGS.get("materials") or {}).items():
        m = unreal.load_asset(path)
        if m is None:
            raise LookupError(f"material {path!r} not found")
        comp.set_material(int(idx), m)
    xfs = [_xf(t) for t in ARGS.get("transforms") or []]
    if xfs:
        comp.add_instances(xfs, False, bool(ARGS.get("world_space", True)))
    sm = comp.get_editor_property("static_mesh")
    return {"actor": actor.get_actor_label(), "component": comp.get_name(), "class": class_name_of(comp),
            "mesh": sm.get_path_name() if sm else None,
            "added": len(xfs), "cleared": cleared, "instances": comp.get_instance_count(),
            "created_actor": created_actor, "created_component": created_comp}


with unreal.ScopedEditorTransaction("MCP instanced_mesh_place"):
    result = _place()
