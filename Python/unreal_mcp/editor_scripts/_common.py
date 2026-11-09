# Shared helpers prepended to every editor script. Runs inside Unreal (`unreal` is imported).
# ruff: noqa: F821
import math as _math
import re

import unreal


def actor_sub():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def editor_world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def all_actors():
    return list(actor_sub().get_all_level_actors())


def find_actor(label_or_name):
    want = str(label_or_name).lower()
    by_name = None
    for a in all_actors():
        if a.get_actor_label().lower() == want:
            return a
        if by_name is None and a.get_name().lower() == want:
            by_name = a
    return by_name


def require_actor(label_or_name):
    a = find_actor(label_or_name)
    if a is None:
        raise LookupError(f"no actor labelled or named {label_or_name!r}")
    return a


def vec(v, default=(0.0, 0.0, 0.0)):
    """[x,y,z] | {"x":..} | {"X":..} | number (uniform) -> unreal.Vector"""
    if v is None:
        v = default
    if isinstance(v, (int, float)):
        return unreal.Vector(float(v), float(v), float(v))
    if isinstance(v, dict):
        g = {k.lower(): float(x) for k, x in v.items()}
        return unreal.Vector(g.get("x", 0.0), g.get("y", 0.0), g.get("z", 0.0))
    return unreal.Vector(float(v[0]), float(v[1]), float(v[2]))


def rot(v):
    """[pitch, yaw, roll] | {"pitch":..,"yaw":..,"roll":..} -> unreal.Rotator"""
    if v is None:
        return unreal.Rotator(roll=0.0, pitch=0.0, yaw=0.0)
    if isinstance(v, dict):
        g = {k.lower(): float(x) for k, x in v.items()}
        return unreal.Rotator(roll=g.get("roll", 0.0), pitch=g.get("pitch", 0.0), yaw=g.get("yaw", 0.0))
    return unreal.Rotator(roll=float(v[2]), pitch=float(v[0]), yaw=float(v[1]))


def color(v):
    if isinstance(v, dict):
        g = {k.lower(): float(x) for k, x in v.items()}
        return unreal.LinearColor(g.get("r", 0.0), g.get("g", 0.0), g.get("b", 0.0), g.get("a", 1.0))
    a = float(v[3]) if len(v) > 3 else 1.0
    return unreal.LinearColor(float(v[0]), float(v[1]), float(v[2]), a)


def v3(v):
    return [round(v.x, 3), round(v.y, 3), round(v.z, 3)]


def r3(r):
    return [round(r.pitch, 3), round(r.yaw, 3), round(r.roll, 3)]


def resolve_class(name):
    """'StaticMeshActor' | 'unreal.PointLight' | '/Script/Engine.PointLight' | '/Game/BP_X.BP_X_C'"""
    if not name:
        return None
    if name.startswith("/"):
        cls = unreal.load_class(None, name)
        if cls is None and not name.endswith("_C"):
            bp = unreal.load_asset(name)
            if bp is not None and hasattr(bp, "generated_class"):
                cls = bp.generated_class()
        return cls
    short = name.split(".")[-1]
    cls = getattr(unreal, short, None)
    if cls is None:
        return None
    return cls.static_class() if hasattr(cls, "static_class") else cls


def class_name_of(obj):
    return obj.get_class().get_name()


def is_a(actor, class_filter):
    """Class match by name, walking the actual class hierarchy is not exposed; use isinstance."""
    if not class_filter:
        return True
    cls = getattr(unreal, class_filter.split(".")[-1], None)
    if cls is not None and isinstance(cls, type):
        return isinstance(actor, cls)
    return class_name_of(actor).lower() == class_filter.lower()


def actor_summary(a, fields=("class", "location")):
    out = {"label": a.get_actor_label(), "name": a.get_name()}
    if "class" in fields:
        out["class"] = class_name_of(a)
    if "location" in fields:
        out["location"] = v3(a.get_actor_location())
    if "rotation" in fields:
        out["rotation"] = r3(a.get_actor_rotation())
    if "scale" in fields:
        out["scale"] = v3(a.get_actor_scale3d())
    if "tags" in fields:
        out["tags"] = [str(t) for t in a.tags]
    if "folder" in fields:
        out["folder"] = str(a.get_folder_path())
    if "bounds" in fields:
        origin, extent = a.get_actor_bounds(False)
        out["bounds"] = {"origin": v3(origin), "extent": v3(extent)}
    return out


def distance(a, b):
    return _math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def coerce(current, value):
    """Convert a JSON value to match the type of an existing editor property value."""
    if isinstance(current, unreal.Vector):
        return vec(value)
    if isinstance(current, unreal.Rotator):
        return rot(value)
    if isinstance(current, unreal.LinearColor):
        return color(value)
    if isinstance(current, unreal.Color) and isinstance(value, (list, dict)):
        c = color(value)
        return unreal.Color(int(c.r), int(c.g), int(c.b), int(c.a))
    if isinstance(value, str) and value.startswith("/") and (current is None or isinstance(current, unreal.Object)):
        loaded = unreal.load_asset(value)
        if loaded is None:
            raise LookupError(f"asset {value!r} not found")
        return loaded
    enum_base = getattr(unreal, "EnumBase", None)
    if enum_base is not None and isinstance(current, enum_base) and isinstance(value, str):
        member = getattr(type(current), value.split(".")[-1].upper(), None)
        if member is None:
            raise ValueError(f"{value!r} is not a member of {type(current).__name__}")
        return member
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool) and isinstance(value, (int, float)):
        return int(value)
    if isinstance(current, float) and isinstance(value, (int, float)):
        return float(value)
    return value


def set_props(obj, props):
    """Set editor properties; supports 'component.prop' one level deep on actors."""
    done = []
    for key, value in (props or {}).items():
        target, prop = obj, key
        if "." in key:
            head, prop = key.split(".", 1)
            target = obj.get_editor_property(head)
            if target is None:
                raise LookupError(f"{head!r} is None on {obj.get_name()}")
        current = target.get_editor_property(prop)
        target.set_editor_property(prop, coerce(current, value))
        done.append(key)
    return done


def asset_summary(ad):
    cls = ad.asset_class_path
    return {
        "path": f"{ad.package_name}.{ad.asset_name}",
        "name": str(ad.asset_name),
        "class": str(cls.asset_name) if hasattr(cls, "asset_name") else str(cls),
        "package": str(ad.package_name),
    }


def asset_registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def _ensure_navmesh():
    """Script-placed nav bounds: register them, drop NavMesh data sized before the bounds existed
    (it hits a tiny tile limit), and rebuild. Mirrors the manual fix: delete RecastNavMesh, Build Paths."""
    w = editor_world()
    nav = unreal.NavigationSystemV1.get_navigation_system(w)
    sub = actor_sub()
    acts = sub.get_all_level_actors()
    for v in [a for a in acts if a.get_class().get_name() == "NavMeshBoundsVolume"]:
        if nav is not None:
            nav.on_navigation_bounds_updated(v)
    for a in [a for a in acts if a.get_class().get_name() == "RecastNavMesh"]:
        sub.destroy_actor(a)
    unreal.SystemLibrary.execute_console_command(w, "RebuildNavigation")


def enum_name(v):
    """unreal enum value -> 'INSTANT' (str() gives '<GameplayEffectDurationType.INSTANT: 0>' in the editor)."""
    m = re.search(r"\.([A-Z0-9_]+)", str(v))
    return m.group(1) if m else str(v)
