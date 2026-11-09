# Create or update a material instance. ARGS: path, parent (required when creating), parameters
#   {name: number | [r,g,b,a] | {"r":..} | "/Game/Tex.Tex" | true/false}, prune (drop overrides the parent lacks)
# Any problem (unknown parameter, value that did not stick, missing or deleted parent) makes success false.
# Why not trust the setters: UE 5.8's MaterialEditingLibrary.set_material_instance_*_parameter_value return False
# ALWAYS (bResult is never assigned), and they store an override even for a name the parent does not have. So the
# names are checked against the parent and every value is read back.
# ruff: noqa: F821
_mel = unreal.MaterialEditingLibrary
_path = ARGS["path"]


def _pkg(obj_or_path):
    p = obj_or_path if isinstance(obj_or_path, str) else obj_or_path.get_path_name()
    return p.split(".")[0]


def _alive(obj):
    return obj is not None and unreal.SystemLibrary.is_valid(obj)


def _registry_parent(mi_path):
    """The parent recorded on disk (asset registry dependencies) for an instance whose parent was deleted."""
    opts = unreal.AssetRegistryDependencyOptions(include_soft_package_references=False, include_hard_package_references=True,
                                                 include_searchable_names=False, include_soft_management_references=False,
                                                 include_hard_management_references=False)
    found = []
    for dep in asset_registry().get_dependencies(_pkg(mi_path), opts) or []:
        dep = str(dep)
        if not unreal.EditorAssetLibrary.does_asset_exist(dep):
            continue
        cls = str(unreal.EditorAssetLibrary.find_asset_data(dep).asset_class_path.asset_name)
        if cls in ("Material", "MaterialInstanceConstant"):
            found.append(dep)
    return found[0] if len(found) == 1 else None


_NAMES = {"scalar": "get_scalar_parameter_names", "vector": "get_vector_parameter_names",
          "texture": "get_texture_parameter_names", "switch": "get_static_switch_parameter_names"}


def _kind(v):
    if isinstance(v, bool):
        return "switch"
    if isinstance(v, (int, float)):
        return "scalar"
    if isinstance(v, (list, dict)):
        return "vector"
    if isinstance(v, str):
        return "texture"
    raise TypeError(f"unsupported value {v!r}")


def _close(a, b):
    return abs(float(a) - float(b)) <= 1e-3 * max(1.0, abs(float(b)))


def _run():
    errors, notes = {}, []
    mi = unreal.load_asset(_path) if unreal.EditorAssetLibrary.does_asset_exist(_path) else None
    created = False
    if mi is None:
        if not ARGS.get("parent"):
            raise ValueError("instance does not exist; pass parent to create it")
        pkg, name = _path.rsplit("/", 1)
        mi = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name.split(".")[0], pkg, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
        created = True

    # Re-resolve the parent BY PATH every time: a parent deleted and recreated at the same path leaves this instance
    # pointing at nothing (force delete nulls the reference) or at the dead object (until garbage collection).
    current = mi.get_editor_property("parent")
    want_path = ARGS.get("parent") or (_pkg(current) if current is not None else None) or _registry_parent(_path)
    parent = None
    if want_path:
        parent = unreal.load_asset(want_path)
        if parent is not None and not _alive(parent):
            parent = None
            unreal.SystemLibrary.collect_garbage()  # drop the deleted object still registered under that path
            parent = unreal.load_asset(want_path)
        if not _alive(parent):
            errors["parent"] = f"parent {want_path!r} not found (deleted?)"
            parent = None
    else:
        errors["parent"] = "the instance has no parent (it was deleted); pass parent= to set one"
    reparented = False
    if parent is not None and current is not parent:
        _mel.set_material_instance_parent(mi, parent)
        reparented = not created
        if reparented:
            notes.append(f"reparented to {parent.get_path_name()}"
                         + ("" if _alive(current) else " (previous parent was deleted)"))

    params = ARGS.get("parameters") or {}
    names = {}
    if parent is not None:
        for k, fn in _NAMES.items():
            names[k] = [str(n) for n in getattr(_mel, fn)(parent)]

    todo = []
    for n, v in params.items():
        try:
            kind = _kind(v)
            if parent is None:
                raise LookupError("no parent to take parameters from")
            if n not in names[kind]:
                other = [k for k, ns in names.items() if n in ns]
                hint = f"; it is a {other[0]} parameter" if other else f"; {kind} parameters: {names[kind]}"
                raise LookupError(f"parameter {n!r} not found on parent{hint}")
            if kind == "switch":
                _mel.set_material_instance_static_switch_parameter_value(mi, n, v)
            elif kind == "scalar":
                _mel.set_material_instance_scalar_parameter_value(mi, n, float(v))
            elif kind == "vector":
                _mel.set_material_instance_vector_parameter_value(mi, n, color(v))
            else:
                tex = unreal.load_asset(v)
                if tex is None:
                    raise LookupError(f"texture {v!r} not found")
                _mel.set_material_instance_texture_parameter_value(mi, n, tex)
                v = tex
            todo.append((n, kind, v))
        except Exception as e:
            errors[n] = f"{type(e).__name__}: {e}"

    pruned = []
    if parent is not None and ARGS.get("prune", True):
        # overrides of parameters the parent no longer has (left over from an older version of the parent)
        for prop, kind in (("scalar_parameter_values", "scalar"), ("vector_parameter_values", "vector"),
                           ("texture_parameter_values", "texture")):
            vals = list(mi.get_editor_property(prop) or [])
            keep = [x for x in vals if str(x.parameter_info.name) in names[kind]]
            if len(keep) != len(vals):
                pruned += [str(x.parameter_info.name) for x in vals if str(x.parameter_info.name) not in names[kind]]
                mi.set_editor_property(prop, keep)

    _mel.update_material_instance(mi)

    done = []
    for n, kind, v in todo:  # read back: the setters' return values say nothing
        if kind == "switch":
            got = _mel.get_material_instance_static_switch_parameter_value(mi, n)
            ok = bool(got) == bool(v)
        elif kind == "scalar":
            got = _mel.get_material_instance_scalar_parameter_value(mi, n)
            ok = _close(got, v)
        elif kind == "vector":
            got = _mel.get_material_instance_vector_parameter_value(mi, n)
            want = color(v)
            ok = all(_close(getattr(got, c), getattr(want, c)) for c in "rgba")
        else:
            got = _mel.get_material_instance_texture_parameter_value(mi, n)
            ok = got is not None and got.get_path_name() == v.get_path_name()
        if ok:
            done.append(n)
        else:
            errors[n] = f"value did not stick (reads back {got!r})"

    if parent is not None or created:
        # Not saved without a parent: the copy on disk still records the old parent path (asset registry), which is
        # how material_edit finds and reparents this instance when that material is recreated.
        unreal.EditorAssetLibrary.save_loaded_asset(mi, only_if_is_dirty=False)
    out = {"path": mi.get_path_name(), "created": created, "parent": parent.get_path_name() if parent else None,
           "set": done, "errors": errors, "reparented": reparented}
    if pruned:
        out["pruned"] = pruned
    if notes:
        out["notes"] = notes
    if errors:
        out["success"] = False
        out["error"] = "; ".join(f"{k}: {v}" for k, v in errors.items())
    return out


result = _run()
