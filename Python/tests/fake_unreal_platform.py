"""Materials (pins, struct arrays, sampler defaults, UE 5.8 setter quirks), instanced meshes and collision surface
of the fake `unreal` module. Added onto the module built by fake_unreal_api.make_module.

Quirks reproduced on purpose, because the editor scripts must cope with them:
- set_material_instance_*_parameter_value always return False (UE 5.8: bResult is never assigned) and store an
  override even for a name the parent does not have;
- delete_all_material_expressions deletes while iterating, so one pass leaves every other expression.
"""

from __future__ import annotations

import enum
import types


def add_platform(u) -> None:  # noqa: C901 - one flat namespace, like the module it fakes
    state = u._state
    Object, Name, MEL = u.Object, u.Name, u.MaterialEditingLibrary
    register = u.register_asset

    class _Enum(enum.Enum):
        def __str__(self):
            return f"{type(self).__name__}.{self.name}"

    u.MaterialProperty = _Enum("MaterialProperty", [
        "MP_BASE_COLOR", "MP_METALLIC", "MP_SPECULAR", "MP_ROUGHNESS", "MP_EMISSIVE_COLOR", "MP_OPACITY",
        "MP_OPACITY_MASK", "MP_NORMAL", "MP_AMBIENT_OCCLUSION", "MP_WORLD_POSITION_OFFSET", "MP_SUBSURFACE_COLOR",
        "MP_REFRACTION", "MP_ANISOTROPY", "MP_TANGENT", "MP_MATERIAL_ATTRIBUTES"])
    u.MaterialSamplerType = _Enum("MaterialSamplerType", [
        "SAMPLERTYPE_COLOR", "SAMPLERTYPE_GRAYSCALE", "SAMPLERTYPE_ALPHA", "SAMPLERTYPE_NORMAL", "SAMPLERTYPE_MASKS",
        "SAMPLERTYPE_LINEAR_COLOR", "SAMPLERTYPE_LINEAR_GRAYSCALE"])
    u.CustomMaterialOutputType = _Enum("CustomMaterialOutputType", ["CMOT_FLOAT1", "CMOT_FLOAT2", "CMOT_FLOAT3",
                                                                    "CMOT_FLOAT4"])

    # ---- expressions: pin names like MaterialEditingLibrary.get_material_expression_input/output_names
    u.MaterialExpression._inputs = ("A", "B", "Alpha")
    u.MaterialExpression._outputs = ("",)
    u.MaterialExpressionMultiply._inputs = ("A", "B")
    u.MaterialExpressionVectorParameter._inputs = ()
    u.MaterialExpressionVectorParameter._outputs = ("", "R", "G", "B", "A")
    u.MaterialExpressionScalarParameter._inputs = ()

    class MaterialExpressionNoise(u.MaterialExpression):
        _inputs = ("World Position", "FilterWidth")

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.scale = 1.0

    class MaterialExpressionWorldPosition(u.MaterialExpression):
        _inputs = ()

    class MaterialExpressionTextureCoordinate(u.MaterialExpression):
        _inputs = ()

    class CustomInput(Object):
        def __init__(self):
            super().__init__()
            self.input_name = Name("")

    class CustomOutput(Object):
        def __init__(self):
            super().__init__()
            self.output_name = Name("")
            self.output_type = u.CustomMaterialOutputType.CMOT_FLOAT1

    class MaterialExpressionCustom(u.MaterialExpression):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.code = ""
            self.output_type = u.CustomMaterialOutputType.CMOT_FLOAT3
            self.inputs = [CustomInput()]
            self.additional_outputs = []

        @property
        def _inputs(self):
            return tuple(str(i.input_name) for i in self.inputs)

        @property
        def _outputs(self):
            return ("",) + tuple(str(o.output_name) for o in self.additional_outputs)

    class Texture2D(Object):
        pass

    for p in ("/Engine/EngineResources/DefaultTexture", "/Engine/EngineMaterials/DefaultNormal",
              "/Engine/EngineMaterials/BaseFlattenLinearColor", "/Engine/EngineMaterials/DefaultDiffuse_TC_Masks"):
        register(Texture2D(p.rsplit("/", 1)[-1], f"{p}.{p.rsplit('/', 1)[-1]}"))

    class MaterialExpressionTextureSampleParameter2D(u.MaterialExpression):
        _inputs = ("UVs",)
        _outputs = ("RGB", "R", "G", "B", "A", "RGBA")

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.parameter_name = Name("")
            self.sampler_type = u.MaterialSamplerType.SAMPLERTYPE_COLOR
            self.texture = state["assets"]["/Engine/EngineResources/DefaultTexture"]

    for c in (MaterialExpressionNoise, MaterialExpressionWorldPosition, MaterialExpressionTextureCoordinate,
              CustomInput, CustomOutput, MaterialExpressionCustom, Texture2D,
              MaterialExpressionTextureSampleParameter2D):
        setattr(u, c.__name__, c)

    MEL.get_material_expression_input_names = staticmethod(lambda e: list(e._inputs))
    MEL.get_material_expression_output_names = staticmethod(lambda e: list(e._outputs))
    MEL.connect_material_expressions = staticmethod(
        lambda a, out, b, inp: a is not None and b is not None and (inp in b._inputs or (inp == "" and bool(b._inputs)))
        and (out in a._outputs or out == ""))

    def delete_all(mat):  # deletes while iterating: every other expression survives a pass
        mat.expressions[:] = mat.expressions[1::2]
        mat.outputs.clear()

    MEL.delete_all_material_expressions = staticmethod(delete_all)

    # ---- instance parameters
    def names(mat, kind):
        src = mat.parent if isinstance(mat, u.MaterialInstance) else mat
        if src is None or not isinstance(src, u.Material):
            return []
        cls = {"scalar": u.MaterialExpressionScalarParameter, "vector": u.MaterialExpressionVectorParameter,
               "texture": MaterialExpressionTextureSampleParameter2D}.get(kind)
        return [Name(e.parameter_name) for e in src.expressions if cls and isinstance(e, cls)]

    MEL._names = staticmethod(names)
    MEL.get_scalar_parameter_names = staticmethod(lambda m: names(m, "scalar"))
    MEL.get_vector_parameter_names = staticmethod(lambda m: names(m, "vector"))
    MEL.get_texture_parameter_names = staticmethod(lambda m: names(m, "texture"))
    MEL.get_static_switch_parameter_names = staticmethod(lambda m: [])

    def setter(kind):
        def set_(mi, n, v):
            mi.params[str(n)] = v
            mi.kinds[str(n)] = kind
            return False  # UE 5.8 never reports success
        return staticmethod(set_)

    MEL.set_material_instance_scalar_parameter_value = setter("scalar")
    MEL.set_material_instance_vector_parameter_value = setter("vector")
    MEL.set_material_instance_texture_parameter_value = setter("texture")
    MEL.set_material_instance_static_switch_parameter_value = setter("switch")
    MEL.get_material_instance_texture_parameter_value = staticmethod(lambda mi, n: mi.params.get(str(n)))
    MEL.get_material_instance_static_switch_parameter_value = staticmethod(lambda mi, n: bool(mi.params.get(str(n))))
    MEL.get_material_instance_vector_parameter_value = staticmethod(
        lambda mi, n: mi.params.get(str(n), u.LinearColor()))

    _mi_init = u.MaterialInstance.__init__

    def mi_init(self, *a, **k):
        _mi_init(self, *a, **k)
        self.kinds = {}

    u.MaterialInstance.__init__ = mi_init
    _props = {"scalar_parameter_values": "scalar", "vector_parameter_values": "vector",
              "texture_parameter_values": "texture"}
    _get, _set = Object.get_editor_property, Object.set_editor_property

    def mi_get(self, name):
        if name in _props:
            return [types.SimpleNamespace(parameter_info=types.SimpleNamespace(name=Name(n)))
                    for n, k in self.kinds.items() if k == _props[name]]
        return _get(self, name)

    def mi_set(self, name, value):
        if name in _props:
            keep = {str(x.parameter_info.name) for x in value}
            for n in [n for n, k in self.kinds.items() if k == _props[name] and n not in keep]:
                self.kinds.pop(n)
                self.params.pop(n, None)
            return None
        return _set(self, name, value)

    u.MaterialInstance.get_editor_property = mi_get
    u.MaterialInstance.set_editor_property = mi_set

    # ---- liveness / registry
    class SystemLibrary(u.SystemLibrary):
        gc_runs = 0

        @staticmethod
        def is_valid(obj):
            return obj is not None and not getattr(obj, "_dead", False)

        @staticmethod
        def collect_garbage():
            SystemLibrary.gc_runs += 1

    u.SystemLibrary = SystemLibrary
    u.EditorAssetLibrary.find_asset_data = staticmethod(
        lambda p: u.AssetData(state["assets"][p.split(".")[0]]))

    # ---- static meshes: body setup, collision subsystem
    class CollisionTraceFlag(_Enum):
        CTF_USE_DEFAULT = 0
        CTF_USE_SIMPLE_AND_COMPLEX = 1
        CTF_USE_SIMPLE_AS_COMPLEX = 2
        CTF_USE_COMPLEX_AS_SIMPLE = 3

    class ScriptCollisionShapeType(_Enum):
        BOX = 0
        SPHERE = 1
        CAPSULE = 2
        NDOP10_X = 3
        NDOP10_Y = 4
        NDOP10_Z = 5
        NDOP18 = 6
        NDOP26 = 7

    class BodyInstance(Object):
        def __init__(self):
            super().__init__()
            self.collision_profile_name = Name("Default")

    class BodySetup(Object):
        def __init__(self):
            super().__init__()
            self.collision_trace_flag = CollisionTraceFlag.CTF_USE_DEFAULT
            self.default_instance = BodyInstance()
            self.shapes = []
            self.convex = 0

    _sm_init = u.StaticMesh.__init__

    def sm_init(self, *a, **k):
        _sm_init(self, *a, **k)
        self.body_setup = BodySetup()

    u.StaticMesh.__init__ = sm_init

    class StaticMeshEditorSubsystem(u.StaticMeshEditorSubsystem):
        def remove_collisions(self, mesh):
            mesh.body_setup.shapes.clear()
            mesh.body_setup.convex = 0
            return True

        def add_simple_collisions(self, mesh, shape):
            mesh.body_setup.shapes.append(shape)
            return len(mesh.body_setup.shapes) - 1

        def set_convex_decomposition_collisions(self, mesh, hulls, verts, precision):
            mesh.body_setup.convex = hulls
            return True

        def get_simple_collision_count(self, mesh):
            return len(mesh.body_setup.shapes)

        def get_convex_collision_count(self, mesh):
            return mesh.body_setup.convex

    for c in (CollisionTraceFlag, ScriptCollisionShapeType, BodyInstance, BodySetup, StaticMeshEditorSubsystem):
        setattr(u, c.__name__, c)
    u.StaticMeshComponent.set_collision_profile_name = lambda self, n: setattr(self, "collision_profile", n)

    # ---- instanced static mesh components, added through the SubobjectDataSubsystem like the editor does
    class ComponentMobility(_Enum):
        STATIC = 0
        MOVABLE = 2

    class CollisionEnabled(_Enum):
        NO_COLLISION = 0
        QUERY_AND_PHYSICS = 3

    class Transform:
        def __init__(self, location=None, rotation=None, scale=None):
            self.translation, self.rotation, self.scale3d = location, rotation, scale

    class InstancedStaticMeshComponent(u.StaticMeshComponent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.instances = []
            self.mobility = ComponentMobility.MOVABLE
            self.instance_end_cull_distance = 0
            self.instance_start_cull_distance = 0
            self.min_lod = 0
            self.collision = None
            self.world_space = None

        def set_static_mesh(self, m):
            self.static_mesh = m
            return True

        def add_instances(self, xfs, return_indices, world_space):
            self.instances += xfs
            self.world_space = world_space
            return []

        def clear_instances(self):
            self.instances = []

        def get_instance_count(self):
            return len(self.instances)

        def set_collision_enabled(self, v):
            self.collision = v

    class HierarchicalInstancedStaticMeshComponent(InstancedStaticMeshComponent):
        pass

    class AddNewSubobjectParams:
        def __init__(self, parent_handle=None, new_class=None, blueprint_context=None):
            self.parent_handle, self.new_class = parent_handle, new_class

    class SubobjectDataSubsystem:
        def k2_gather_subobject_data_for_instance(self, actor):
            return [actor]

        def add_new_subobject(self, params):
            comp = params.new_class()
            params.parent_handle._components.append(comp)
            return comp, ""

    class SubobjectDataBlueprintFunctionLibrary:
        get_data = staticmethod(lambda h: h)
        get_object = staticmethod(lambda d: d)

    engine_subsystems = {}
    u.get_engine_subsystem = lambda cls: engine_subsystems.setdefault(cls, cls())
    for c in (ComponentMobility, CollisionEnabled, Transform, InstancedStaticMeshComponent,
              HierarchicalInstancedStaticMeshComponent, AddNewSubobjectParams, SubobjectDataSubsystem,
              SubobjectDataBlueprintFunctionLibrary):
        setattr(u, c.__name__, c)

    # ---- import: an existing asset is only overwritten with replace_existing
    tools = u.AssetToolsHelpers.get_asset_tools()

    def import_asset_tasks(tasks):
        for t in tasks:
            stem = t.filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            name = t.destination_name or stem
            path = f"{t.destination_path}/{name}"
            if path in state["assets"] and not t.replace_existing:
                t.imported_object_paths = []
                continue
            o = register(u.StaticMesh(name, f"{path}.{name}"))
            t.imported_object_paths = [o.get_path_name()]
            state.setdefault("imported", []).append((t.filename, t.replace_existing))

    tools.import_asset_tasks = import_asset_tasks
