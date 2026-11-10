"""gen3d / auto-rig surface of the fake `unreal` module (real UE 5.8 names): importing GLB/FBX files the way the
engine does (static mesh + materials + textures; skinned files -> SkeletalMesh + Skeleton + PhysicsAsset), mesh
bounds read from the file, static mesh LODs, IK Rig / IK Retargeter controllers, batch retargeting, and
SkeletalMeshActor animation playback. Added onto the module built by fake_unreal_api.make_module."""

from __future__ import annotations

import enum
import json
import struct
from pathlib import Path


def _glb(data: bytes) -> dict:
    n = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + n])


def add_gen3d(u) -> None:  # noqa: C901 - one flat namespace, like the module it fakes
    state = u._state
    Object, Name, Vector = u.Object, u.Name, u.Vector
    register = u.register_asset

    class _Enum(enum.Enum):
        def __str__(self):
            return f"{type(self).__name__}.{self.name}"

    class BoxSphereBounds:
        def __init__(self, origin, extent):
            self.origin, self.box_extent = origin, extent
            self.sphere_radius = (extent.x ** 2 + extent.y ** 2 + extent.z ** 2) ** 0.5

    class MeshNaniteSettings(Object):
        def __init__(self):
            super().__init__()
            self.enabled = False

    class StaticMaterial(Object):
        def __init__(self, mat):
            super().__init__()
            self.material_interface = mat
            self.material_slot_name = Name(mat.get_name() if mat else "None")

    SkeletalMaterial = type("SkeletalMaterial", (StaticMaterial,), {})

    def _mesh_init(self, *a, **k):
        _sm_init(self, *a, **k)
        self._bounds = BoxSphereBounds(Vector(0, 0, 50), Vector(50, 50, 50))
        self._tris, self._lods = 12, 1
        self.static_materials = []
        self.nanite_settings = MeshNaniteSettings()

    _sm_init = u.StaticMesh.__init__
    u.StaticMesh.__init__ = _mesh_init
    u.StaticMesh.get_bounds = lambda self: self._bounds

    class AssetImportData(Object):
        def __init__(self, mesh):
            super().__init__()
            self._mesh = mesh

        def get_first_filename(self):
            return getattr(self._mesh, "source_file", "") or ""

    u.StaticMesh.asset_import_data = property(lambda self: AssetImportData(self))
    u.StaticMesh.get_num_triangles = lambda self, lod: max(1, int(self._tris * 0.5 ** lod))
    u.StaticMesh.get_num_lods = lambda self: self._lods

    Skeleton = u.Skeleton
    class PhysicsAsset(Object):
        pass

    _skm_init = u.SkeletalMesh.__init__

    def _skm(self, *a, **k):
        _skm_init(self, *a, **k)
        self.physics_asset, self.materials, self.bones = None, [], []
        self._bounds = BoxSphereBounds(Vector(0, 0, 90), Vector(40, 20, 90))

    u.SkeletalMesh.__init__ = _skm
    u.SkeletalMesh.get_bounds = lambda self: self._bounds

    Texture2D = getattr(u, "Texture2D", None) or type("Texture2D", (Object,), {})

    def _bounds_from_glb(doc):
        """glTF is Y-up metres; Unreal is Z-up centimetres (X forward = glTF -Z, Y right = glTF X)."""
        lo, hi = [1e9] * 3, [-1e9] * 3
        for mesh in doc.get("meshes", []):
            for prim in mesh["primitives"]:
                acc = doc["accessors"][prim["attributes"]["POSITION"]]
                lo = [min(a, b) for a, b in zip(lo, acc["min"], strict=True)]
                hi = [max(a, b) for a, b in zip(hi, acc["max"], strict=True)]
        ue_lo = Vector(-hi[2] * 100, lo[0] * 100, lo[1] * 100)
        ue_hi = Vector(-lo[2] * 100, hi[0] * 100, hi[1] * 100)
        origin = Vector((ue_lo.x + ue_hi.x) / 2, (ue_lo.y + ue_hi.y) / 2, (ue_lo.z + ue_hi.z) / 2)
        return BoxSphereBounds(origin, Vector((ue_hi.x - ue_lo.x) / 2, (ue_hi.y - ue_lo.y) / 2,
                                              (ue_hi.z - ue_lo.z) / 2))

    def _tris_from_glb(doc):
        return sum(doc["accessors"][p["indices"]]["count"] // 3 for m in doc.get("meshes", []) for p in m["primitives"]
                   if "indices" in p)

    tools = u.AssetToolsHelpers.get_asset_tools()
    u.AssetData.is_valid = lambda self: True

    def import_asset_tasks(self, tasks):
        for t in tasks:
            path = Path(t.filename)
            data = path.read_bytes() if path.is_file() else b""
            is_glb = path.suffix.lower() == ".glb" and data[:4] == b"glTF"
            stem = t.destination_name or path.stem
            dest = t.destination_path
            made = []
            if (is_glb and "skins" in _glb(data)) or (path.suffix.lower() == ".fbx" and b"Deformer" in data):
                mesh = u.SkeletalMesh(stem, f"{dest}/{stem}.{stem}")
                if t.replace_existing is False and u.EditorAssetLibrary.does_asset_exist(f"{dest}/{stem}"):
                    t.imported_object_paths = []
                    continue
                sk = register(Skeleton(f"{stem}_Skeleton", f"{dest}/{stem}_Skeleton.{stem}_Skeleton"))
                pa = register(PhysicsAsset(f"{stem}_PhysicsAsset", f"{dest}/{stem}_PhysicsAsset.{stem}_PhysicsAsset"))
                mesh.skeleton, mesh.physics_asset = sk, pa
                if is_glb:
                    doc = _glb(data)
                    mesh.bones = [doc["nodes"][j]["name"] for sk_ in doc["skins"] for j in sk_["joints"]]
                else:
                    mesh.bones = [b.decode() for b in _fbx_bones(data)]
                made += [register(mesh), sk, pa]
            else:
                if t.replace_existing is False and u.EditorAssetLibrary.does_asset_exist(f"{dest}/{stem}"):
                    t.imported_object_paths = []
                    continue
                mesh = u.StaticMesh(stem, f"{dest}/{stem}.{stem}")
                mesh.source_file = str(path)  # AssetImportData: what the FBX exporter re-exports in this fake
                if is_glb:
                    doc = _glb(data)
                    mesh._bounds, mesh._tris = _bounds_from_glb(doc), _tris_from_glb(doc)
                    for m in doc.get("materials", []):
                        mat = register(u.Material(m["name"], f"{dest}/{m['name']}.{m['name']}"))
                        mesh.static_materials.append(StaticMaterial(mat))
                        made.append(mat)
                    for i, img in enumerate(doc.get("images", [])):
                        n = img.get("name") or f"{stem}_tex{i}"
                        made.append(register(Texture2D(n, f"{dest}/{n}.{n}")))
                made.insert(0, register(mesh))
            state.setdefault("imports", []).append({"file": str(path), "replace": t.replace_existing,
                                                    "assets": [o.get_path_name() for o in made]})
            state.setdefault("imported", []).append((t.filename, t.replace_existing))
            t.imported_object_paths = [o.get_path_name() for o in made]

    tools.import_asset_tasks = lambda tasks: import_asset_tasks(tools, tasks)

    # ---- static mesh LODs / nanite (StaticMeshEditorSubsystem; real names) ----
    class StaticMeshReductionSettings(Object):
        def __init__(self):
            super().__init__()
            self.percent_triangles, self.screen_size = 1.0, 0.0

    class StaticMeshReductionOptions(Object):
        def __init__(self):
            super().__init__()
            self.auto_compute_lod_screen_size, self.reduction_settings = True, []

    sub = u.StaticMeshEditorSubsystem

    def set_lods(self, mesh, options):
        mesh._lods = len(options.reduction_settings)
        mesh.lod_fractions = [s.percent_triangles for s in options.reduction_settings]
        return mesh._lods

    sub.set_lods = set_lods
    sub.get_lod_count = lambda self, mesh: mesh._lods
    sub.set_nanite_settings = lambda self, mesh, s, apply_changes=True: setattr(mesh, "nanite_settings", s)

    class SkeletalMeshEditorSubsystem:
        @classmethod
        def regenerate_lod(cls, mesh, new_lod_count=0, regenerate_even_if_imported=False, generate_base_lod=False):
            mesh.lod_count = new_lod_count
            return True

    # ---- IK Rig / retargeting (IKRig plugin's Python API) ----
    class RetargetSourceOrTarget(_Enum):
        SOURCE = 0
        TARGET = 1

    class AutoMapChainType(_Enum):
        EXACT = 0
        FUZZY = 1
        CLEAR = 2

    class _Chain(Object):
        def __init__(self, name):
            super().__init__()
            self.chain_name = Name(name)

    _ue5_chains = {"Spine": "spine_01", "Neck": "neck_01", "Head": "head", "LeftArm": "upperarm_l",
                   "RightArm": "upperarm_r", "LeftLeg": "thigh_l", "RightLeg": "thigh_r",
                   "LeftClavicle": "clavicle_l", "RightClavicle": "clavicle_r"}

    class IKRigController:
        _by_rig: dict = {}

        @classmethod
        def get_controller(cls, rig):
            return cls._by_rig.setdefault(id(rig), cls(rig))

        def __init__(self, rig):
            self.rig, self.mesh, self.chains, self.root = rig, None, [], Name("None")

        def set_skeletal_mesh(self, mesh):
            self.mesh = mesh
            return True

        def apply_auto_generated_retarget_definition(self):
            # auto-characterisation recognises the UE5 Mannequin bone names
            bones = set(getattr(self.mesh, "bones", []) or [])
            if self.mesh is not None and self.mesh.get_path_name().startswith("/Game/Characters/Mannequins"):
                bones = set(_ue5_chains.values()) | {"pelvis"}
            found = [c for c, b in _ue5_chains.items() if b in bones]
            self.chains = [_Chain(c) for c in found]
            self.root = Name("pelvis" if "pelvis" in bones else "None")
            self.rig.chains = found
            return bool(found)

        def apply_auto_fbik(self):
            return True

        def add_retarget_chain(self, name, start, end, goal):
            self.chains.append(_Chain(name))
            return Name(name)

        def set_retarget_root(self, bone):
            self.root = Name(bone)
            return True

        def get_retarget_root(self):
            return self.root

        def get_retarget_chains(self):
            return list(self.chains)

    class IKRetargeterController:
        _by: dict = {}

        @classmethod
        def get_controller(cls, rtg):
            return cls._by.setdefault(id(rtg), cls(rtg))

        def __init__(self, rtg):
            self.rtg, self.ops, self.rigs = rtg, 0, {}

        def set_ik_rig(self, side, rig):
            self.rigs[side] = rig
            self.rtg.rigs = dict(self.rigs)

        def get_num_retarget_ops(self):
            return self.ops

        def add_default_ops(self):
            self.ops = 5

        def assign_ik_rig_to_all_ops(self, side, rig):
            pass

        def auto_map_chains(self, kind, force):
            src = getattr(self.rigs.get(RetargetSourceOrTarget.SOURCE), "chains", [])
            dst = getattr(self.rigs.get(RetargetSourceOrTarget.TARGET), "chains", [])
            self.rtg.mapped = sorted(set(src) & set(dst))

        def auto_align_all_bones(self, side):
            self.rtg.aligned = True

    class IKRetargetBatchOperation:
        @staticmethod
        def duplicate_and_retarget(assets, src_mesh, dst_mesh, rtg, search, replace, prefix, suffix, folder,
                                   remap, include_referenced, overwrite):
            if not getattr(rtg, "mapped", None):
                return []
            out = []
            for ad in assets:
                name = f"{prefix}{ad.asset_name}{suffix}"
                path = f"{folder or str(ad.package_name).rsplit('/', 1)[0]}/{name}"
                if path in state["assets"] and not overwrite:
                    continue
                a = register(u.AnimSequence(name, f"{path}.{name}"))
                a._skeleton = dst_mesh.skeleton
                out.append(u.AssetData(a))
            return out

    # ---- skeletal mesh actors playing an animation asset ----
    class AnimationMode(_Enum):
        ANIMATION_BLUEPRINT = 0
        ANIMATION_SINGLE_NODE = 1
        ANIMATION_CUSTOM_MODE = 2

    class SingleAnimationPlayData(Object):
        def __init__(self):
            super().__init__()
            self.anim_to_play, self.saved_looping, self.saved_playing = None, False, False

    class SkeletalMeshComponent(u.SceneComponent):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.skeletal_mesh_asset, self.animation_data = None, SingleAnimationPlayData()
            self._mode = AnimationMode.ANIMATION_BLUEPRINT

        def set_skeletal_mesh_asset(self, mesh):
            self.skeletal_mesh_asset = mesh

        def set_animation_mode(self, mode, force_init_anim_script_instance=True):
            self._mode = mode

        def get_animation_mode(self):
            return self._mode

        def override_animation_data(self, anim, is_looping=True, is_playing=True, position=0.0, play_rate=1.0):
            self.animation_data.anim_to_play = anim
            self.animation_data.saved_looping, self.animation_data.saved_playing = is_looping, is_playing

        def is_playing(self):
            return self.animation_data.saved_playing and self.animation_data.anim_to_play is not None

    class SkeletalMeshActor(u.Actor):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.skeletal_mesh_component = SkeletalMeshComponent()
            self._components.append(self.skeletal_mesh_component)

    # ---- exporting assets back to files (auto_rig on an Unreal asset) ----
    class AssetExportTask(Object):
        def __init__(self):
            super().__init__()
            self.object, self.filename, self.automated, self.prompt = None, "", False, True
            self.replace_identical, self.exporter, self.selected = False, None, False

    class StaticMeshExporterFBX(Object):
        pass

    class SkeletalMeshExporterFBX(Object):
        pass

    class GLTFExporter(Object):
        pass

    class Exporter:
        @staticmethod
        def run_asset_export_task(task):
            src = getattr(task.object, "source_file", None)
            if not src or Path(src).suffix.lower() != Path(task.filename).suffix.lower():
                return False  # this fake can only "export" to the format the asset was imported from
            Path(task.filename).write_bytes(Path(src).read_bytes())
            return True

    for name, obj in list(locals().items()):
        if isinstance(obj, type) and not name.startswith("_") and name not in ("Object", "Name", "Vector", "Skeleton"):
            setattr(u, name, obj)


def _fbx_bones(data: bytes) -> list[bytes]:
    """Bone names in a binary FBX: Model nodes of type LimbNode are stored as 'name\\x00\\x01Model'."""
    import re
    return [m.group(1) for m in re.finditer(rb"([A-Za-z0-9_]+)\x00\x01Model", data)]
