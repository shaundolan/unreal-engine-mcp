"""A tiny in-memory stand-in for the editor's `unreal` Python module.

Covers only the API surface used by unreal_mcp/editor_scripts, with the same names and
call shapes as UE 5.5. It lets the scripts execute for real in tests (logic, argument
handling, result shapes). It does NOT prove the real API behaves identically; the live
smoke test does that.
"""

from __future__ import annotations

import enum
import types


def make_module() -> types.ModuleType:
    u = types.ModuleType("unreal")
    state = {"actors": [], "assets": {}, "deps": {}, "transactions": 0, "counter": 0}
    u._state = state

    class Name(str):
        pass

    class Vector:
        def __init__(self, x=0.0, y=0.0, z=0.0):
            self.x, self.y, self.z = float(x), float(y), float(z)

        def to_tuple(self):
            return (self.x, self.y, self.z)

    class Rotator:
        def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
            self.roll, self.pitch, self.yaw = float(roll), float(pitch), float(yaw)

        def to_tuple(self):
            return (self.roll, self.pitch, self.yaw)

    class LinearColor:
        def __init__(self, r=0.0, g=0.0, b=0.0, a=1.0):
            self.r, self.g, self.b, self.a = r, g, b, a

        def to_tuple(self):
            return (self.r, self.g, self.b, self.a)

    class Color(LinearColor):
        pass

    class _Class:
        def __init__(self, name):
            self._n = name

        def get_name(self):
            return self._n

    class Object:
        def __init__(self, name=None, path=None):
            state["counter"] += 1
            self._name = name or f"{type(self).__name__}_{state['counter']}"
            self._path = path or f"/Temp/Untitled.Untitled:PersistentLevel.{self._name}"

        def get_name(self):
            return self._name

        def get_path_name(self):
            return self._path

        def get_class(self):
            return _Class(type(self).__name__)

        def get_editor_property(self, name):
            if not hasattr(self, name):
                raise AttributeError(f"{type(self).__name__} has no property {name!r}")
            return getattr(self, name)

        def set_editor_property(self, name, value):
            if not hasattr(self, name):
                raise AttributeError(f"{type(self).__name__} has no property {name!r}")
            setattr(self, name, value)

    class ActorComponent(Object):
        pass

    class SceneComponent(ActorComponent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.relative_location = Vector()

    class StaticMeshComponent(SceneComponent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.static_mesh = None
            self.cast_shadow = True

        def get_materials(self):
            return []

    class LightComponent(SceneComponent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.intensity = 5000.0
            self.light_color = Color(255, 255, 255, 255)

    class Actor(Object):
        def __init__(self, location=None, rotation=None):
            super().__init__()
            self._label = self._name
            self._loc = location or Vector()
            self._rot = rotation or Rotator()
            self._scale = Vector(1, 1, 1)
            self.tags = []
            self._folder = Name("")
            self._hidden = False
            self._components = []

        def get_actor_label(self):
            return self._label

        def set_actor_label(self, label):
            self._label = label

        def get_actor_location(self):
            return self._loc

        def set_actor_location(self, v, sweep, teleport):
            self._loc = v
            return True

        def get_actor_rotation(self):
            return self._rot

        def set_actor_rotation(self, r, teleport):
            self._rot = r
            return True

        def get_actor_scale3d(self):
            return self._scale

        def set_actor_scale3d(self, s):
            self._scale = s

        def get_folder_path(self):
            return self._folder

        def set_folder_path(self, p):
            self._folder = Name(p)

        def get_actor_bounds(self, only_colliding):
            return self._loc, Vector(50, 50, 50)

        def set_is_temporarily_hidden_in_editor(self, h):
            self._hidden = h

        def is_hidden_ed(self):
            return self._hidden

        def get_components_by_class(self, cls):
            return [c for c in self._components if isinstance(c, cls)]

    class StaticMeshActor(Actor):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.static_mesh_component = StaticMeshComponent()
            self._components.append(self.static_mesh_component)

    class Light(Actor):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.light_component = LightComponent()
            self._components.append(self.light_component)

    class PointLight(Light):
        pass

    class DirectionalLight(Light):
        pass

    class CameraActor(Actor):
        pass

    class StaticMesh(Object):
        pass

    class World(Object):
        def get_world_settings(self):
            ws = Object("WorldSettings")
            ws.kill_z = -100000.0
            ws.world_partition = None
            return ws

    world = World("Untitled", "/Temp/Untitled.Untitled")
    world.streaming_levels = []

    class EditorActorSubsystem:
        def get_all_level_actors(self):
            return list(state["actors"])

        def spawn_actor_from_class(self, cls, loc, rot):
            a = cls(loc, rot)
            state["actors"].append(a)
            return a

        def spawn_actor_from_object(self, obj, loc, rot):
            a = StaticMeshActor(loc, rot)
            a.static_mesh_component.static_mesh = obj
            state["actors"].append(a)
            return a

        def destroy_actor(self, a):
            state["actors"].remove(a)
            return True

    class UnrealEditorSubsystem:
        def get_editor_world(self):
            return world

    subsystems = {}

    def get_editor_subsystem(cls):
        return subsystems.setdefault(cls, cls())

    class ScopedEditorTransaction:
        def __init__(self, desc):
            self.desc = desc

        def __enter__(self):
            state["transactions"] += 1
            return self

        def __exit__(self, *exc):
            return False

    def load_asset(path):
        return state["assets"].get(path.split(".")[0])

    def load_class(outer, path):
        return None

    class TopLevelAssetPath:
        def __init__(self, package, asset):
            self.package_name, self.asset_name = Name(package), Name(asset)

    class AssetData:
        def __init__(self, obj):
            pkg = obj.get_path_name().split(".")[0]
            self.package_name = Name(pkg)
            self.asset_name = Name(pkg.rsplit("/", 1)[-1])
            self.asset_class_path = TopLevelAssetPath("/Script/Engine", type(obj).__name__)

    class AssetRegistryDependencyOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Registry:
        def get_assets_by_path(self, path, recursive=True):
            return [AssetData(o) for p, o in state["assets"].items() if p.startswith(path.rstrip("/") + "/")]

        def get_dependencies(self, name, opts):
            return [Name(d) for d in state["deps"].get(str(name), [])]

        def get_referencers(self, name, opts):
            return [Name(k) for k, v in state["deps"].items() if str(name) in v]

    registry = _Registry()

    class AssetRegistryHelpers:
        @staticmethod
        def get_asset_registry():
            return registry

    def _register(obj):
        state["assets"][obj.get_path_name().split(".")[0]] = obj
        return obj

    class EditorAssetLibrary:
        @staticmethod
        def does_asset_exist(path):
            return path.split(".")[0] in state["assets"]

        @staticmethod
        def duplicate_asset(src, dst):
            o = state["assets"].get(src.split(".")[0])
            if o is None or dst in state["assets"]:
                return None
            n = type(o)(dst.rsplit("/", 1)[-1], f"{dst}.{dst.rsplit('/', 1)[-1]}")
            return _register(n)

        @staticmethod
        def rename_asset(src, dst):
            o = state["assets"].pop(src.split(".")[0], None)
            if o is None:
                return False
            o._path = f"{dst}.{dst.rsplit('/', 1)[-1]}"
            _register(o)
            return True

        @staticmethod
        def delete_asset(path):
            return state["assets"].pop(path.split(".")[0], None) is not None

        @staticmethod
        def save_asset(path, only_if_is_dirty=True):
            return True

        @staticmethod
        def save_loaded_asset(obj, only_if_is_dirty=True):
            return True

        @staticmethod
        def save_directory(path, only_if_is_dirty=True, recursive=True):
            return True

        @staticmethod
        def make_directory(path):
            return True

    # ---- materials -----------------------------------------------------------------------
    class MaterialProperty(enum.Enum):
        MP_BASE_COLOR = 0
        MP_METALLIC = 1
        MP_SPECULAR = 2
        MP_ROUGHNESS = 3
        MP_EMISSIVE_COLOR = 4
        MP_OPACITY = 5
        MP_OPACITY_MASK = 6
        MP_NORMAL = 7
        MP_AMBIENT_OCCLUSION = 8
        MP_WORLD_POSITION_OFFSET = 9

    class BlendMode(enum.Enum):
        BLEND_OPAQUE = 0
        BLEND_TRANSLUCENT = 2

    class MaterialInterface(Object):
        pass

    class Material(MaterialInterface):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.expressions = []
            self.outputs = {}
            self.blend_mode = BlendMode.BLEND_OPAQUE
            self.two_sided = False

    class MaterialInstance(MaterialInterface):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.parent = None
            self.params = {}

    class MaterialInstanceConstant(MaterialInstance):
        pass

    class MaterialExpression(Object):
        pass

    class MaterialExpressionMultiply(MaterialExpression):
        pass

    class MaterialExpressionVectorParameter(MaterialExpression):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.parameter_name = Name("")
            self.default_value = LinearColor()

    class MaterialExpressionScalarParameter(MaterialExpression):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.parameter_name = Name("")
            self.default_value = 0.0

    class MaterialFactoryNew:
        pass

    class MaterialInstanceConstantFactoryNew:
        pass

    class AssetImportTask(Object):
        def __init__(self):
            super().__init__()
            self.filename = ""
            self.destination_path = ""
            self.destination_name = ""
            self.automated = False
            self.replace_existing = False
            self.save = False
            self.imported_object_paths = []

    class _AssetTools:
        def create_asset(self, name, pkg, cls, factory):
            return _register(cls(name, f"{pkg}/{name}.{name}"))

        def import_asset_tasks(self, tasks):
            for t in tasks:
                stem = t.filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
                name = t.destination_name or stem
                o = _register(StaticMesh(name, f"{t.destination_path}/{name}.{name}"))
                t.imported_object_paths = [o.get_path_name()]

    tools = _AssetTools()

    class AssetToolsHelpers:
        @staticmethod
        def get_asset_tools():
            return tools

    class MaterialEditingLibrary:
        @staticmethod
        def create_material_expression(mat, cls, x=0, y=0):
            e = cls()
            mat.expressions.append(e)
            return e

        @staticmethod
        def connect_material_expressions(a, out, b, inp):
            return a is not None and b is not None and inp in ("", "A", "B", "Alpha")

        @staticmethod
        def connect_material_property(e, out, prop):
            e_mat = next(m for m in state["assets"].values() if isinstance(m, Material) and e in m.expressions)
            e_mat.outputs[prop.name] = e
            return True

        @staticmethod
        def delete_all_material_expressions(mat):
            mat.expressions.clear()
            mat.outputs.clear()

        @staticmethod
        def layout_material_expressions(mat):
            pass

        @staticmethod
        def recompile_material(mat):
            pass

        @staticmethod
        def get_num_material_expressions(mat):
            return len(mat.expressions)

        @staticmethod
        def get_material_property_input_node(mat, prop):
            return mat.outputs.get(prop.name)

        @staticmethod
        def _names(mat, kind):
            src = mat.parent if isinstance(mat, MaterialInstance) else mat
            if src is None:
                return []
            cls = {"scalar": MaterialExpressionScalarParameter, "vector": MaterialExpressionVectorParameter}.get(kind)
            return [Name(e.parameter_name) for e in src.expressions if cls and isinstance(e, cls)]

        get_scalar_parameter_names = staticmethod(lambda m: MaterialEditingLibrary._names(m, "scalar"))
        get_vector_parameter_names = staticmethod(lambda m: MaterialEditingLibrary._names(m, "vector"))
        get_texture_parameter_names = staticmethod(lambda m: [])
        get_static_switch_parameter_names = staticmethod(lambda m: [])
        get_used_textures = staticmethod(lambda m: [])

        @staticmethod
        def set_material_instance_parent(mi, parent):
            mi.parent = parent

        @staticmethod
        def _set(mi, name, value, kind):
            if str(name) not in [str(n) for n in MaterialEditingLibrary._names(mi, kind)]:
                return False
            mi.params[str(name)] = value
            return True

        set_material_instance_scalar_parameter_value = staticmethod(
            lambda mi, n, v: MaterialEditingLibrary._set(mi, n, v, "scalar"))
        set_material_instance_vector_parameter_value = staticmethod(
            lambda mi, n, v: MaterialEditingLibrary._set(mi, n, v, "vector"))
        set_material_instance_texture_parameter_value = staticmethod(lambda mi, n, v: False)
        set_material_instance_static_switch_parameter_value = staticmethod(lambda mi, n, v: False)
        get_material_instance_scalar_parameter_value = staticmethod(lambda mi, n: mi.params.get(str(n), 0.0))
        get_material_instance_vector_parameter_value = staticmethod(lambda mi, n: mi.params.get(str(n)))

        @staticmethod
        def update_material_instance(mi):
            pass

    class EditorLevelUtils:
        @staticmethod
        def get_levels(w):
            return []

    for name, obj in list(locals().items()):
        if not name.startswith("_") and name not in ("u", "state", "name", "obj", "world", "registry",
                                                    "tools", "subsystems", "enum", "types"):
            setattr(u, name, obj)
    u.register_asset = _register
    return u
