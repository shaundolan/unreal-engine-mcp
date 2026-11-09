# Place (or update) a SkeletalMeshActor showing a skeletal mesh, optionally looping an animation on it
# (Animation Mode "Use Animation Asset"), so it animates in the editor viewport and in PIE.
# ARGS: label, mesh (SkeletalMesh path), animation (AnimSequence path, optional), location [x,y,z], yaw, folder
# ruff: noqa: F821


def _place():
    mesh = unreal.load_asset(ARGS["mesh"])
    if not isinstance(mesh, unreal.SkeletalMesh):
        raise LookupError(f"skeletal mesh {ARGS['mesh']!r} not found")
    anim = None
    if ARGS.get("animation"):
        anim = unreal.load_asset(ARGS["animation"])
        if anim is None:
            raise LookupError(f"animation {ARGS['animation']!r} not found")
        if anim.get_skeleton() != mesh.get_editor_property("skeleton"):
            raise ValueError(f"{ARGS['animation']} uses another skeleton; retarget it to {ARGS['mesh']} first")
    label = ARGS.get("label") or mesh.get_name()
    with unreal.ScopedEditorTransaction(f"Place {label}"):
        actor = find_actor(label)
        if actor is None:
            actor = actor_sub().spawn_actor_from_class(unreal.SkeletalMeshActor.static_class(),
                                                       vec(ARGS.get("location")), rot([0, ARGS.get("yaw", 0), 0]))
            actor.set_actor_label(label)
        elif ARGS.get("location") is not None:
            actor.set_actor_location(vec(ARGS["location"]), False, False)
        if ARGS.get("folder"):
            actor.set_folder_path(ARGS["folder"])
        comp = actor.get_editor_property("skeletal_mesh_component")
        comp.set_skeletal_mesh_asset(mesh)
        if anim is not None:
            comp.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
            comp.override_animation_data(anim, True, True, 0.0, 1.0)
    return {"actor": actor.get_actor_label(), "mesh": mesh.get_path_name(),
            "animation": anim.get_path_name() if anim else None, "location": v3(actor.get_actor_location())}


result = _place()
