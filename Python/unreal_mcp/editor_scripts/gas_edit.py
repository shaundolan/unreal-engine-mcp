# Gameplay Ability System assets: GameplayEffect and GameplayAbility Blueprints (class defaults:
# duration, period, modifiers, tags, policies, cost/cooldown) and reading them back.
# Needs the GameplayAbilities plugin. Tags must exist (the gas_edit tool adds them via gameplay_tags first).
# ARGS: operations [{op: create_effect | set_effect | create_ability | set_ability | inspect, ...}]
# ruff: noqa: F821
_eal = unreal.EditorAssetLibrary
_tools = unreal.AssetToolsHelpers.get_asset_tools()

if getattr(unreal, "GameplayEffect", None) is None:
    raise RuntimeError("GameplayAbilities plugin is not enabled in this project (add it to the .uproject Plugins "
                       "list or enable it in Edit > Plugins, then restart the editor)")

_MOD_OPS = {"add": "AddBase", "addbase": "AddBase", "additive": "AddBase", "add_final": "AddFinal",
            "addfinal": "AddFinal", "multiply": "MultiplyAdditive", "multiplyadditive": "MultiplyAdditive",
            "multiply_compound": "MultiplyCompound", "multiplycompound": "MultiplyCompound",
            "divide": "DivideAdditive", "divideadditive": "DivideAdditive", "override": "Override"}


def _split(path):
    pkg, name = path.rsplit("/", 1)
    return pkg, name.split(".")[0]


def _bp_and_cdo(path, create_parent=None):
    """Load (or create with create_parent) a Blueprint; return it and its class default object."""
    bp = unreal.load_asset(path) if _eal.does_asset_exist(path.split(".")[0]) else None
    created = False
    if bp is None:
        if create_parent is None:
            raise LookupError(f"Blueprint {path!r} not found")
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", create_parent)
        pkg, name = _split(path)
        bp = _tools.create_asset(name, pkg, unreal.Blueprint, factory)
        if bp is None:
            raise RuntimeError(f"could not create {path}")
        created = True
    gen = bp.generated_class()
    return bp, unreal.get_default_object(gen), created


def _prop(obj, names, value=None, set_it=False):
    """get/set the first property name that exists (Python names differ for acronym-led C++ names)."""
    last = None
    for n in names:
        try:
            if set_it:
                obj.set_editor_property(n, value)
                return value
            return obj.get_editor_property(n)
        except Exception as e:  # noqa: PERF203
            last = e
    raise AttributeError(f"{obj.get_class().get_name()} has none of {names}: {last}")


def _attr_text(spec):
    """'AbilitySystemTestAttributeSet.Health' | '/Script/Module.Class:Health' -> FGameplayAttribute text."""
    s = str(spec)
    if ":" in s:
        cls_path, attr = s.rsplit(":", 1)
    elif "." in s:
        cls_name, attr = s.rsplit(".", 1)
        cls = resolve_class(cls_name)
        if cls is None:
            raise LookupError(f"AttributeSet class {cls_name!r} not found (C++ AttributeSets only; see the gas skill)")
        cls_path = cls.get_path_name()
    else:
        raise ValueError(f"attribute {s!r} must be 'AttributeSetClass.Attribute'")
    return f'(AttributeName="{attr}",Attribute={cls_path}:{attr},AttributeOwner="{cls_path}")', f"{cls_path}:{attr}"


def _magnitude_text(value):
    if isinstance(value, dict) and value.get("set_by_caller"):
        tag = value["set_by_caller"]
        return f'(MagnitudeCalculationType=SetByCaller,SetByCallerMagnitude=(DataTag=(TagName="{tag}")))'
    v = float(value["value"] if isinstance(value, dict) else value)
    return f"(MagnitudeCalculationType=ScalableFloat,ScalableFloatMagnitude=(Value={v:.6f}))"


def _struct(cls_name, text):
    s = getattr(unreal, cls_name)()
    if not s.import_text(text):
        raise ValueError(f"{cls_name} rejected {text!r}")
    return s


def _tags_text(tags):
    return "(GameplayTags=(" + ",".join(f'(TagName="{t}")' for t in tags) + "))"


def _tag_container(tags):
    c = _struct("GameplayTagContainer", _tags_text(tags))
    got = _tags_of(c)
    missing = [t for t in tags if t not in got]
    if missing:
        raise LookupError(f"gameplay tags {missing} are not registered (gameplay_tags action=add, or let gas_edit add them)")
    return c


def _tags_of(container):
    text = container.export_text() if container is not None else ""
    out = []
    for part in text.split('TagName="')[1:]:
        out.append(part.split('"', 1)[0])
    return out


def _modifiers(cdo, mods):
    infos = []
    for m in mods:
        attr_text, attr_key = _attr_text(m["attribute"])
        mod_op = _MOD_OPS.get(str(m.get("op", "add")).lower().replace(" ", ""))
        if mod_op is None:
            raise ValueError(f"modifier op {m.get('op')!r}: use add, multiply, divide, override, add_final, multiply_compound")
        info = _struct("GameplayModifierInfo", f"(Attribute={attr_text},ModifierOp={mod_op},"
                                               f"ModifierMagnitude={_magnitude_text(m.get('magnitude', 0))})")
        if attr_key.split(":")[-1] not in info.export_text():
            raise ValueError(f"attribute {m['attribute']!r} did not resolve (is the AttributeSet class loaded?)")
        infos.append(info)
    _prop(cdo, ["modifiers"], infos, set_it=True)


def _ge_component(cdo, cls_name):
    comps = list(_prop(cdo, ["ge_components", "gecomponents", "g_e_components"]) or [])
    for c in comps:
        if c is not None and c.get_class().get_name() == cls_name:
            return c
    comp = unreal.new_object(getattr(unreal, cls_name), cdo)
    comps.append(comp)
    _prop(cdo, ["ge_components", "gecomponents", "g_e_components"], comps, set_it=True)
    return comp


def _inherited(tags):
    return _struct("InheritedTagContainer", f"(Added={_tags_text(tags)})")


def _effect(op, create):
    bp, cdo, created = _bp_and_cdo(op["asset"], unreal.GameplayEffect.static_class() if create else None)
    dur = op.get("duration")
    if dur is not None:
        if isinstance(dur, str) and dur.lower() in ("instant", "infinite"):
            policy = getattr(unreal.GameplayEffectDurationType, dur.upper())
            _prop(cdo, ["duration_policy"], policy, set_it=True)
        else:
            _prop(cdo, ["duration_policy"], unreal.GameplayEffectDurationType.HAS_DURATION, set_it=True)
            _prop(cdo, ["duration_magnitude"], _struct("GameplayEffectModifierMagnitude", _magnitude_text(dur)), set_it=True)
    if op.get("period") is not None:
        _prop(cdo, ["period"], _struct("ScalableFloat", f"(Value={float(op['period']):.6f})"), set_it=True)
        if "execute_on_application" in op:
            _prop(cdo, ["execute_periodic_effect_on_application"], bool(op["execute_on_application"]), set_it=True)
    if op.get("modifiers") is not None:
        _modifiers(cdo, op["modifiers"])
    if op.get("asset_tags"):
        comp = _ge_component(cdo, "AssetTagsGameplayEffectComponent")
        _prop(comp, ["inheritable_asset_tags"], _inherited(op["asset_tags"]), set_it=True)
    if op.get("granted_tags"):
        comp = _ge_component(cdo, "TargetTagsGameplayEffectComponent")
        _prop(comp, ["inheritable_granted_tags_container"], _inherited(op["granted_tags"]), set_it=True)
    if op.get("stack_limit") is not None:
        _prop(cdo, ["stacking_type"], unreal.GameplayEffectStackingType.AGGREGATE_BY_TARGET, set_it=True)
        _prop(cdo, ["stack_limit_count"], int(op["stack_limit"]), set_it=True)
    for k, v in (op.get("properties") or {}).items():
        set_props(cdo, {k: v})
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    _eal.save_loaded_asset(bp, only_if_is_dirty=False)
    return {"created": created, **_inspect_effect(bp)}


def _inspect_effect(bp):
    cdo = unreal.get_default_object(bp.generated_class())
    mods = []
    for m in _prop(cdo, ["modifiers"]) or []:
        text = m.export_text()
        attr = text.split("Attribute=", 2)[-1].split(",")[0].split(")")[0] if "Attribute=" in text else ""
        mag = None
        if "ScalableFloatMagnitude=(Value=" in text:
            mag = float(text.split("ScalableFloatMagnitude=(Value=", 1)[1].split(",")[0].split(")")[0])
        mods.append({"attribute": attr.split(".")[-1].replace(":", "."),
                     "op": text.split("ModifierOp=", 1)[1].split(",")[0].split(")")[0] if "ModifierOp=" in text else "",
                     "magnitude": mag})
    dur_policy = str(_prop(cdo, ["duration_policy"])).split(".")[-1]
    out = {"path": bp.get_path_name(), "class": "GameplayEffect", "duration_policy": dur_policy, "modifiers": mods}
    if dur_policy == "HAS_DURATION":
        text = _prop(cdo, ["duration_magnitude"]).export_text()
        if "ScalableFloatMagnitude=(Value=" in text:
            out["duration"] = float(text.split("ScalableFloatMagnitude=(Value=", 1)[1].split(",")[0].split(")")[0])
    period = _prop(cdo, ["period"]).export_text()
    if "Value=" in period:
        out["period"] = float(period.split("Value=", 1)[1].split(",")[0].split(")")[0])
    tags = {}
    for c in _prop(cdo, ["ge_components", "gecomponents", "g_e_components"]) or []:
        if c is None:
            continue
        n = c.get_class().get_name()
        if n == "AssetTagsGameplayEffectComponent":
            tags["asset_tags"] = _tags_of(_prop(c, ["inheritable_asset_tags"]))
        elif n == "TargetTagsGameplayEffectComponent":
            tags["granted_tags"] = _tags_of(_prop(c, ["inheritable_granted_tags_container"]))
    out.update(tags)
    return out


_ABILITY_TAGS = {"ability_tags": ["ability_tags", "asset_tags"], "activation_owned_tags": ["activation_owned_tags"],
                 "activation_required_tags": ["activation_required_tags"],
                 "activation_blocked_tags": ["activation_blocked_tags"],
                 "cancel_abilities_with_tag": ["cancel_abilities_with_tag"],
                 "block_abilities_with_tag": ["block_abilities_with_tag"]}


def _ability(op, create):
    parent = unreal.GameplayAbility.static_class()
    if op.get("parent"):
        parent = resolve_class(op["parent"])
        if parent is None:
            raise LookupError(f"parent class {op['parent']!r} not found")
    bp, cdo, created = _bp_and_cdo(op["asset"], parent if create else None)
    for key, names in _ABILITY_TAGS.items():
        if op.get(key) is not None:
            _prop(cdo, names, _tag_container(op[key]), set_it=True)
    for key, names in (("cost_effect", ["cost_gameplay_effect_class"]), ("cooldown_effect", ["cooldown_gameplay_effect_class"])):
        if op.get(key):
            cls = resolve_class(op[key] if op[key].endswith("_C") else op[key].split(".")[0] + "." + op[key].split("/")[-1].split(".")[0] + "_C")
            if cls is None:
                raise LookupError(f"{key} {op[key]!r} not found")
            _prop(cdo, names, cls, set_it=True)
    if op.get("instancing"):
        _prop(cdo, ["instancing_policy"], getattr(unreal.GameplayAbilityInstancingPolicy, str(op["instancing"]).upper()), set_it=True)
    if op.get("net_execution"):
        _prop(cdo, ["net_execution_policy"], getattr(unreal.GameplayAbilityNetExecutionPolicy, str(op["net_execution"]).upper()), set_it=True)
    for k, v in (op.get("properties") or {}).items():
        set_props(cdo, {k: v})
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    _eal.save_loaded_asset(bp, only_if_is_dirty=False)
    return {"created": created, **_inspect_ability(bp)}


def _inspect_ability(bp):
    cdo = unreal.get_default_object(bp.generated_class())
    out = {"path": bp.get_path_name(), "class": "GameplayAbility",
           "generated_class": bp.generated_class().get_path_name()}
    for key, names in _ABILITY_TAGS.items():
        try:
            out[key] = _tags_of(_prop(cdo, names))
        except AttributeError:
            pass
    for key, names in (("cost_effect", ["cost_gameplay_effect_class"]), ("cooldown_effect", ["cooldown_gameplay_effect_class"])):
        c = _prop(cdo, names)
        out[key] = c.get_path_name() if c is not None else None
    out["instancing"] = str(_prop(cdo, ["instancing_policy"])).split(".")[-1]
    return out


def _inspect(op):
    bp = unreal.load_asset(op["asset"])
    if bp is None:
        raise LookupError(f"{op['asset']!r} not found")
    cdo = unreal.get_default_object(bp.generated_class())
    if isinstance(cdo, unreal.GameplayEffect):
        return _inspect_effect(bp)
    if isinstance(cdo, unreal.GameplayAbility):
        return _inspect_ability(bp)
    raise TypeError(f"{op['asset']!r} is not a GameplayEffect or GameplayAbility Blueprint")


_OPS = {"create_effect": lambda o: _effect(o, True), "set_effect": lambda o: _effect(o, False),
        "create_ability": lambda o: _ability(o, True), "set_ability": lambda o: _ability(o, False),
        "inspect": _inspect}

_results = []
for _i, _op in enumerate(ARGS.get("operations") or []):
    _kind = _op.get("op")
    _rec = {"index": _op.get("_index", _i), "op": _kind}
    try:
        if _kind not in _OPS:
            raise ValueError(f"unknown op {_kind!r}")
        with unreal.ScopedEditorTransaction(f"MCP gas_edit {_kind}"):
            _rec.update(_OPS[_kind](_op))
        _rec["ok"] = True
    except Exception as _e:
        _rec.update(ok=False, error=f"{type(_e).__name__}: {_e}")
    _results.append(_rec)
result = {"results": _results}
