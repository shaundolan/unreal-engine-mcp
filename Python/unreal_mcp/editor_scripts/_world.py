# World-building helpers shared by foliage / spline / pcg scripts (prepended after _common when requested).
# ruff: noqa: F821


def hit_fields(hit):
    """HitResult -> (location Vector, normal Vector, actor) across UE versions."""
    try:
        t = hit.to_tuple()
        return t[5], t[7], t[9]  # impact_point, impact_normal, hit_actor
    except Exception:
        return (hit.get_editor_property("impact_point"), hit.get_editor_property("impact_normal"),
                hit.get_editor_property("hit_actor"))


def trace_down(x, y, z_top, z_bottom, ignore=()):
    hit = unreal.SystemLibrary.line_trace_single(
        editor_world(), unreal.Vector(x, y, z_top), unreal.Vector(x, y, z_bottom),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, list(ignore), unreal.DrawDebugTrace.NONE, True)
    if hit is None:
        return None
    loc, normal, actor = hit_fields(hit)
    if loc is None:
        return None
    return loc, normal, actor


def slope_deg(normal):
    nz = max(-1.0, min(1.0, normal.z / max(1e-9, _math.sqrt(normal.x ** 2 + normal.y ** 2 + normal.z ** 2))))
    return _math.degrees(_math.acos(nz))


def landscapes():
    return [a for a in all_actors() if isinstance(a, unreal.LandscapeProxy)]


def surface_actor(label):
    """'' -> the only landscape (or None = any surface); label -> that actor."""
    if label:
        return require_actor(label)
    ls = [a for a in landscapes() if isinstance(a, unreal.Landscape)] or landscapes()
    return ls[0] if len(ls) == 1 else None


def bounds_xy(actor):
    origin, extent = actor.get_actor_bounds(False)
    return (origin.x - extent.x, origin.y - extent.y, origin.x + extent.x, origin.y + extent.y,
            origin.z - extent.z, origin.z + extent.z)


def add_instance_component(actor, cls, name=None):
    """Add an editor-visible (serialized) instance component to a placed actor via the Subobject system."""
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = sds.k2_gather_subobject_data_for_instance(actor)
    if not handles:
        raise RuntimeError(f"no subobject data for {actor.get_actor_label()}")
    parent = handles[0]
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    for h in handles:
        obj = lib.get_object(lib.get_data(h))
        if isinstance(obj, unreal.SceneComponent) and obj == actor.get_editor_property("root_component"):
            parent = h
            break
    params = unreal.AddNewSubobjectParams(parent_handle=parent, new_class=cls, blueprint_context=None)
    handle, fail = sds.add_new_subobject(params)
    if not lib.is_handle_valid(handle):
        raise RuntimeError(f"could not add {cls.get_name()}: {fail}")
    if name:
        sds.rename_subobject(handle, unreal.Text(name))
    return lib.get_object(lib.get_data(handle))


def remove_instance_components(actor, cls):
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    handles = sds.k2_gather_subobject_data_for_instance(actor)
    doomed = [h for h in handles if isinstance(lib.get_object(lib.get_data(h)), cls)]
    if doomed:
        sds.delete_subobjects(handles[0], doomed, None)
    return len(doomed)


def spawn_empty_actor(label, location=None):
    a = actor_sub().spawn_actor_from_class(unreal.Actor, location or unreal.Vector(0, 0, 0), unreal.Rotator())
    a.set_actor_label(label)
    return a
