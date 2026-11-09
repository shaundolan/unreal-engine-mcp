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

        def __add__(self, o):
            return Vector(self.x + o.x, self.y + o.y, self.z + o.z)

        def __sub__(self, o):
            return Vector(self.x - o.x, self.y - o.y, self.z - o.z)

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
        """Stands in for unreal.Class: what static_class() returns. Callable so the fake
        spawn_actor_from_class can instantiate it."""

        def __init__(self, name, pytype=None):
            self._n, self._t = name, pytype

        def get_name(self):
            return self._n

        def get_path_name(self):
            return f"/Script/Engine.{self._n}"

        def __call__(self, *a, **k):
            return self._t(*a, **k)

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
            return _Class(type(self).__name__, type(self))

        @classmethod
        def static_class(cls):
            return _Class(cls.__name__, cls)

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

            self._materials = {}

        def get_materials(self):
            return [self._materials[i] for i in sorted(self._materials)]

        def get_material(self, index):
            return self._materials.get(index)

        def set_material(self, index, mat):
            self._materials[index] = mat

    class LightComponent(SceneComponent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.intensity = 5000.0
            self.light_color = Color(255, 255, 255, 255)
            self.mobility = "ComponentMobility.MOVABLE"
            self.cast_shadows = True

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
            sc = self._scale  # engine basic shapes are 100 units across
            return self._loc, Vector(50 * sc.x, 50 * sc.y, 50 * sc.z)

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

    class SkyLight(Light):
        pass

    class PlayerStart(Actor):
        pass

    class CameraActor(Actor):
        pass

    class Character(Actor):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.health = 100.0

    class StaticMesh(Object):
        pass

    class World(Object):
        def get_world_settings(self):
            if not hasattr(self, "_ws"):
                self._ws = Object("WorldSettings")
                self._ws.kill_z = -100000.0
                self._ws.world_partition = None
                self._ws.default_game_mode = None
            return self._ws

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
            if isinstance(obj, NiagaraSystem):
                a = NiagaraActor(loc, rot)
                state["actors"].append(a)
                return a
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

        def get_game_world(self):
            return world if state.get("pie") else None

    class GameplayStatics:
        @staticmethod
        def get_all_actors_of_class(w, cls):
            return [a for a in state["actors"] if isinstance(a, cls)]

        @staticmethod
        def get_player_pawn(w, index):
            return state.get("player")

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

        @staticmethod
        def list_assets(path, recursive=True, include_folder=False):
            root = path.rstrip("/") + "/"
            return sorted(p for p in state["assets"] if p.startswith(root))

        @staticmethod
        def does_directory_exist(path):
            return bool(EditorAssetLibrary.list_assets(path))

    class LevelEditorSubsystem:
        def new_level(self, asset_path, is_partitioned_world=False):
            if asset_path.split(".")[0] in state["assets"]:
                return False
            state["actors"].clear()
            name = asset_path.rsplit("/", 1)[-1]
            world._name, world._path = name, f"{asset_path}.{name}"
            _register(World(name, f"{asset_path}.{name}"))
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

    # ---- niagara ---------------------------------------------------------------------------
    class NiagaraSystem(Object):
        pass

    class NiagaraComponent(SceneComponent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.params = {}
            self.auto_activate = True

        def set_auto_activate(self, v):
            self.auto_activate = v

    for _kind in ("float", "int", "bool", "vec3", "linear_color", "object"):
        setattr(NiagaraComponent, f"set_variable_{_kind}",
                (lambda kind: lambda self, name, value: self.params.__setitem__(name, (kind, value)))(_kind))

    class NiagaraActor(Actor):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.niagara_component = NiagaraComponent()
            self._components.append(self.niagara_component)

    def _get_component_by_class(self, cls):
        return next((c for c in self._components if isinstance(c, cls)), None)

    Actor.get_component_by_class = _get_component_by_class

    # ---- sequencer -------------------------------------------------------------------------
    class FrameRate:
        def __init__(self, n, d=1):
            self.numerator, self.denominator = n, d

    class FrameNumber:
        def __init__(self, v):
            self.value = v

    class _Channel:
        def __init__(self):
            self.keys = []

        def add_key(self, frame, value):
            self.keys.append((frame.value, value))

    class _Section:
        def __init__(self, nchannels):
            self.range = None
            self.channels = [_Channel() for _ in range(nchannels)]
            self.camera = None

        def set_range_seconds(self, a, b):
            self.range = (a, b)

        def get_all_channels(self):
            return self.channels

        def set_camera_binding_id(self, bid):
            self.camera = bid

    class MovieScene3DTransformTrack:
        nch = 9

    class MovieSceneCameraCutTrack:
        nch = 0

    class _Track:
        def __init__(self, cls):
            self.cls, self.sections = cls, []

        def get_class(self):
            return _Class(self.cls.__name__, self.cls)

        def add_section(self):
            sec = _Section(self.cls.nch)
            self.sections.append(sec)
            return sec

    class _Binding:
        def __init__(self, actor):
            self.actor, self.tracks = actor, []

        def add_track(self, cls):
            t = _Track(cls)
            self.tracks.append(t)
            return t

    class LevelSequence(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.rate, self.start, self.end = None, None, None
            self.bindings, self.tracks = [], []

        def set_display_rate(self, r):
            self.rate = r

        def set_playback_start_seconds(self, s):
            self.start = s

        def set_playback_end_seconds(self, s):
            self.end = s

        def add_possessable(self, actor):
            b = _Binding(actor)
            self.bindings.append(b)
            return b

        def add_track(self, cls):
            t = _Track(cls)
            self.tracks.append(t)
            return t

        def get_portable_binding_id(self, root, binding):
            raise AttributeError("not in this version")  # exercise the fallback chain

        def get_tracks(self):
            return list(self.tracks)

        def get_bindings(self):
            return list(self.bindings)

        def get_playback_end_seconds(self):
            return self.end

        def get_binding_id(self, binding):
            return ("binding", self.bindings.index(binding))

    class LevelSequenceFactoryNew:
        pass

    class CineCameraComponent(SceneComponent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.current_focal_length = 35.0

    class CineCameraActor(Actor):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._cine = CineCameraComponent()
            self._components.append(self._cine)

        def get_cine_camera_component(self):
            return self._cine

    class LevelSequenceEditorBlueprintLibrary:
        opened = []

        @staticmethod
        def open_level_sequence(seq):
            LevelSequenceEditorBlueprintLibrary.opened.append(seq)

    # ---- data tables -----------------------------------------------------------------------
    class ScriptStruct(Object):
        def __init__(self, name, path, fields):
            super().__init__(name, path)
            self.fields = fields

    class DataTable(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.row_struct = None
            self.rows = {}

    class DataTableFactory(Object):
        def __init__(self):
            super().__init__()
            self.struct = None

    def _create_with_factory(orig):
        def create_asset(self, name, pkg, cls, factory):
            o = orig(self, name, pkg, cls, factory)
            if isinstance(o, DataTable):
                o.row_struct = factory.struct
            return o
        return create_asset

    _AssetTools.create_asset = _create_with_factory(_AssetTools.create_asset)

    def load_object(outer, path):
        return state["assets"].get(path.split(".")[0])

    class DataTableFunctionLibrary:
        @staticmethod
        def fill_data_table_from_json_string(table, text):
            import json
            rows = json.loads(text)
            fields = set(table.row_struct.fields)
            if any(set(r) - {"Name"} - fields for r in rows):
                return False
            table.rows = {r["Name"]: {k: v for k, v in r.items() if k != "Name"} for r in rows}
            return True

        @staticmethod
        def get_data_table_row_names(table):
            return [Name(n) for n in table.rows]

        @staticmethod
        def get_data_table_column_names(table):
            return [Name(f) for f in table.row_struct.fields]

        @staticmethod
        def get_data_table_column_as_string(table, col):
            return [str(r.get(str(col), "")) for r in table.rows.values()]

    # ---- enhanced input / game framework / paths / perf ----------------------------------
    class InputActionValueType(enum.Enum):
        BOOLEAN = 0
        AXIS1D = 1
        AXIS2D = 2
        AXIS3D = 3

    class InputAction(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.value_type = InputActionValueType.BOOLEAN

    class Key(Object):
        def __init__(self):
            super().__init__()
            self.key_name = ""

    class EnhancedActionKeyMapping(Object):
        def __init__(self, action, key):
            super().__init__()
            self.action, self.key, self.modifiers = action, key, []

    class InputMappingContextMappingData(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.mappings = []

    class InputMappingContext(Object):
        """UE 5.8 layout: map_key writes default_key_mappings.mappings; legacy `mappings` stays empty."""

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.mappings = []
            self.default_key_mappings = InputMappingContextMappingData()

        def unmap_all(self):
            self.default_key_mappings.mappings = []

        def map_key(self, action, key):
            m = EnhancedActionKeyMapping(action, key)
            self.default_key_mappings.mappings.append(m)
            return m

    class InputAction_Factory:
        pass

    class InputMappingContext_Factory:
        pass

    class InputModifierNegate(Object):
        pass

    class InputModifierSwizzleAxis(Object):
        pass

    def new_object(cls, outer=None):
        return cls()

    class SoftClassPath(str):
        pass

    class SoftObjectPath(str):
        pass

    class GameMapsSettings(Object):
        instance = None

        def __init__(self):
            super().__init__("GameMapsSettings")
            self.global_default_game_mode = SoftClassPath("/Script/Engine.GameModeBase")
            self.editor_startup_map = SoftObjectPath("")
            self.game_default_map = SoftObjectPath("")
            self.saved = 0

        @classmethod
        def get_game_maps_settings(cls):
            if cls.instance is None:
                cls.instance = cls()
            return cls.instance

        def save_config(self):
            self.saved += 1

    class SystemLibrary:
        commands: list = []

        @staticmethod
        def execute_console_command(world, command, player=None):
            SystemLibrary.commands.append(command)

    class Paths:
        project = "/tmp/Project/"

        @staticmethod
        def project_dir():
            return Paths.project

        @staticmethod
        def convert_relative_path_to_full(p):
            return p

    class StaticMeshEditorSubsystem:
        def get_number_triangles(self, mesh, lod):
            return getattr(mesh, "triangles", 0)

    class EditorLevelUtils:
        @staticmethod
        def get_levels(w):
            return []

    for name, obj in list(locals().items()):
        if not name.startswith("_") and name not in ("u", "state", "name", "obj", "world", "registry",
                                                    "tools", "subsystems", "enum", "types", "anchor"):
            setattr(u, name, obj)
    u.register_asset = _register
    from fake_unreal_anim import add_anim_gas
    add_anim_gas(u)
    return u
