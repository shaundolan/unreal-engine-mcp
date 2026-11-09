"""Animation + GAS surface of the fake `unreal` module (real UE 5.8 names and call shapes), used by
anim_edit / anim_inspect / gas_edit tests. Added onto the module built by fake_unreal_api.make_module."""

from __future__ import annotations

import enum
import re


def add_anim_gas(u) -> None:  # noqa: C901 - one flat namespace, like the module it fakes
    state = u._state
    Object, Name, Vector = u.Object, u.Name, u.Vector
    register = u.register_asset

    class _Enum(enum.Enum):
        def __str__(self):
            return f"{type(self).__name__}.{self.name}"

    class RawCurveTrackTypes(_Enum):
        RCT_FLOAT = 0
        RCT_VECTOR = 1
        RCT_TRANSFORM = 2

    class RootMotionRootLock(_Enum):
        REF_POSE = 0
        ANIM_FIRST_FRAME = 1
        ZERO = 2

    class AnimInterpolationType(_Enum):
        LINEAR = 0
        STEP = 1

    class Skeleton(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.curves = []

    class SkeletalMesh(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.skeleton = None

    class AnimationAsset(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._skeleton = None

        def get_skeleton(self):
            return self._skeleton

    class AnimSequenceBase(AnimationAsset):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.length, self.rate_scale, self.notifies, self.curves = 1.0, 1.0, [], {}

    class AnimSequence(AnimSequenceBase):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.root_motion, self.root_lock, self.force_lock = False, RootMotionRootLock.REF_POSE, False
            self.interp, self.loop = AnimInterpolationType.LINEAR, False

    class AnimMontage(AnimSequenceBase):
        pass

    class BlendParameter(Object):
        def __init__(self):
            super().__init__()
            self.display_name, self.min, self.max, self.grid_num = "None", 0.0, 100.0, 4
            self.snap_to_grid, self.wrap_input = False, False

    class BlendSample(Object):
        def __init__(self):
            super().__init__()
            self.animation, self.sample_value, self.rate_scale = None, Vector(), 1.0

    class BlendSpace(AnimationAsset):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.blend_parameters = [BlendParameter(), BlendParameter(), BlendParameter()]
            self.sample_data = []

        def set_editor_property(self, name, value):
            if name == "sample_data":
                for s in value:  # like ValidateSampleData: samples need an animation of this skeleton
                    if s.animation is None or s.animation.get_skeleton() is not self._skeleton:
                        raise ValueError("sample animation is missing or uses another skeleton")
            super().set_editor_property(name, list(value))

    class BlendSpace1D(BlendSpace):
        pass

    class BlendSpaceFactory1D(Object):
        def __init__(self):
            super().__init__()
            self.target_skeleton, self.preview_skeletal_mesh = None, None

    class BlendSpaceFactoryNew(BlendSpaceFactory1D):
        pass

    class AnimNotifyEvent(Object):
        def __init__(self, name, time, notify=None):
            super().__init__()
            self.notify_name, self.time, self.notify, self.notify_state_class = Name(name), time, notify, None

    class AnimationLibrary:
        @staticmethod
        def set_rate_scale(s, v):
            s.rate_scale = v

        @staticmethod
        def get_rate_scale(s):
            return s.rate_scale

        @staticmethod
        def set_root_motion_enabled(s, v):
            s.root_motion = v

        @staticmethod
        def is_root_motion_enabled(s):
            return s.root_motion

        @staticmethod
        def set_root_motion_lock_type(s, v):
            s.root_lock = v

        @staticmethod
        def get_root_motion_lock_type(s):
            return s.root_lock

        @staticmethod
        def set_is_root_motion_lock_forced(s, v):
            s.force_lock = v

        @staticmethod
        def is_root_motion_lock_forced(s):
            return s.force_lock

        @staticmethod
        def set_animation_interpolation_type(s, v):
            s.interp = v

        @staticmethod
        def does_curve_exist(s, name, kind):
            return str(name) in s.curves

        @staticmethod
        def add_curve(s, name, kind=RawCurveTrackTypes.RCT_FLOAT, meta=False):
            s.curves.setdefault(str(name), [])

        @staticmethod
        def add_float_curve_keys(s, name, times, values):
            s.curves[str(name)] += list(zip(times, values, strict=True))

        @staticmethod
        def get_animation_curve_names(s, kind):
            return [Name(n) for n in s.curves]

        @staticmethod
        def get_animation_notify_events(s):
            return list(s.notifies)

        @staticmethod
        def get_anim_notify_event_trigger_time(ev):
            return ev.time

        @staticmethod
        def get_anim_notify_event_duration(ev):
            return 0.0

        @staticmethod
        def get_sequence_length(s):
            return s.length

        @staticmethod
        def get_num_frames(s):
            return int(s.length * 30)

        @staticmethod
        def get_curve_meta_data_names(sk):
            return [Name(c) for c in sk.curves]

    class IKRigDefinition(Object):
        pass

    class IKRetargeter(Object):
        pass

    class IKRigDefinitionFactory(Object):
        pass

    class IKRetargetFactory(Object):
        pass

    class AnimBlueprint(Object):
        pass

    # ---- GAS ------------------------------------------------------------------------------
    class _Struct:
        """StructBase stand-in: import_text/export_text round-trip the text form."""

        def __init__(self):
            self._text = "()"

        def import_text(self, text):
            if text.count("(") != text.count(")"):
                return False
            for tag in re.findall(r'TagName="([^"]+)"', text):
                if tag not in state.setdefault("tags", set()):
                    text = text.replace(f'(TagName="{tag}")', "")  # unknown tags are dropped, like UE
            self._text = text
            return True

        def export_text(self):
            return self._text

    for _n in (
        "GameplayModifierInfo",
        "GameplayEffectModifierMagnitude",
        "ScalableFloat",
        "GameplayTagContainer",
        "InheritedTagContainer",
    ):
        setattr(u, _n, type(_n, (_Struct,), {}))

    class GameplayEffectDurationType(_Enum):
        INSTANT = 0
        INFINITE = 1
        HAS_DURATION = 2

    class GameplayAbilityInstancingPolicy(_Enum):
        NON_INSTANCED = 0
        INSTANCED_PER_ACTOR = 1
        INSTANCED_PER_EXECUTION = 2

    class GameplayEffect(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.duration_policy = GameplayEffectDurationType.INSTANT
            self.duration_magnitude = u.GameplayEffectModifierMagnitude()
            self.period = u.ScalableFloat()
            self.modifiers = []
            self.ge_components = []

    class GameplayAbility(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            for t in (
                "ability_tags",
                "activation_owned_tags",
                "activation_required_tags",
                "activation_blocked_tags",
                "cancel_abilities_with_tag",
                "block_abilities_with_tag",
            ):
                setattr(self, t, u.GameplayTagContainer())
            self.cost_gameplay_effect_class = None
            self.cooldown_gameplay_effect_class = None
            self.instancing_policy = GameplayAbilityInstancingPolicy.INSTANCED_PER_ACTOR

    class AssetTagsGameplayEffectComponent(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.inheritable_asset_tags = u.InheritedTagContainer()

    class TargetTagsGameplayEffectComponent(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.inheritable_granted_tags_container = u.InheritedTagContainer()

    class AbilitySystemTestAttributeSet(Object):
        @classmethod
        def static_class(cls):
            c = super().static_class()
            c.get_path_name = lambda: "/Script/GameplayAbilities.AbilitySystemTestAttributeSet"
            return c

    class _GenClass:
        def __init__(self, bp, parent):
            self.bp, self.parent = bp, parent
            self.cdo = parent()

        def get_path_name(self):
            return self.bp.get_path_name() + "_C"

    class Blueprint(Object):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._gen = None

        def generated_class(self):
            return self._gen

    class BlueprintFactory(Object):
        def __init__(self):
            super().__init__()
            self.parent_class = None

    class BlueprintEditorLibrary:
        compiled: list = []

        @staticmethod
        def compile_blueprint(bp):
            BlueprintEditorLibrary.compiled.append(bp.get_path_name())

    def get_default_object(cls):
        return cls.cdo

    tools = u.AssetToolsHelpers.get_asset_tools()
    orig = type(tools).create_asset

    def create_asset(self, name, pkg, cls, factory):
        o = orig(self, name, pkg, cls, factory)
        if isinstance(factory, BlendSpaceFactory1D):
            o._skeleton = factory.target_skeleton
        if isinstance(o, Blueprint):
            o._gen = _GenClass(o, factory.parent_class._t)
        return o

    type(tools).create_asset = create_asset

    for name, obj in list(locals().items()):
        if isinstance(obj, type) and not name.startswith("_") and name not in ("Object", "Name", "Vector"):
            setattr(u, name, obj)
    u.get_default_object = get_default_object
    u.new_object = lambda cls, outer=None, name="": cls()

    def add_mannequin(root="/Game/Characters/Mannequins"):
        """Register a few template Mannequin assets (one skeleton) like scripts/install_mannequin.py installs."""
        sk = register(Skeleton("SK_Mannequin", f"{root}/Meshes/SK_Mannequin.SK_Mannequin"))
        mesh = register(SkeletalMesh("SKM_Manny_Simple", f"{root}/Meshes/SKM_Manny_Simple.SKM_Manny_Simple"))
        mesh.skeleton = sk
        for rel, length in (
            ("Unarmed/MM_Idle", 2.0),
            ("Unarmed/Walk/MF_Unarmed_Walk_Fwd", 1.1),
            ("Unarmed/Jog/MF_Unarmed_Jog_Fwd", 0.8),
            ("Unarmed/Jump/MM_Jump", 0.9),
            ("Unarmed/Jump/MM_Fall_Loop", 1.0),
            ("Unarmed/Jump/MM_Land", 0.5),
            ("Unarmed/Attack/MM_Attack_01", 1.4),
        ):
            n = rel.rsplit("/", 1)[-1]
            a = register(AnimSequence(n, f"{root}/Anims/{rel}.{n}"))
            a._skeleton, a.length = sk, length
        return sk

    u.add_mannequin = add_mannequin
    state.setdefault("tags", set())
