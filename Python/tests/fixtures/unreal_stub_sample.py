# Excerpt in the format UE writes to Intermediate/PythonStub/unreal.py (abridged).
# This file is test data; it is parsed with ast, never imported.

def log(arg: object) -> None:
    r"""
    log(arg: Any) -> None -- log the given argument as information in the LogPython category
    """
    ...

class Object(_WrapperBase):
    r"""
    Object is the base class of all UE objects.

    **C++ Source:**

    - **Module**: CoreUObject
    - **File**: Object.h
    """
    def get_name(self) -> str:
        r"""
        x.get_name() -> str -- get the name of this instance
        """
        ...

class Actor(Object):
    r"""
    Actor is the base class for an Object that can be placed or spawned in a level.

    **C++ Source:**

    - **Module**: Engine
    - **File**: Actor.h

    **Editor Properties:** (see get_editor_property/set_editor_property)

    - ``auto_destroy_when_finished`` (bool):  [Read-Write] If true then destroy self when "finished"
    - ``tags`` (Array[Name]):  [Read-Write] Array of tags that can be used for grouping and categorizing.
    """
    @property
    def root_component(self) -> SceneComponent:
        r"""
        (SceneComponent):  [Read-Write] The component that defines the transform of this Actor.
        """
        ...
    @root_component.setter
    def root_component(self, value: SceneComponent) -> None:
        ...
    def get_actor_location(self) -> Vector:
        r"""
        x.get_actor_location() -> Vector
        Returns the location of the RootComponent of this Actor
        """
        ...

class EditorActorSubsystem(EditorSubsystem):
    r"""
    UEditorActorSubsystem
    Subsystem for exposing actor related utilities to scripts,

    **C++ Source:**

    - **Plugin**: EditorScriptingUtilities
    - **Module**: UnrealEd
    - **File**: EditorActorSubsystem.h
    """
    def spawn_actor_from_class(self, actor_class: type(Class), location: Vector, rotation: Rotator = [0.0, 0.0, 0.0], transient: bool = False) -> Actor:
        r"""
        x.spawn_actor_from_class(actor_class, location, rotation=[0.0, 0.0, 0.0], transient=False) -> Actor
        Create an actor and place it in the world editor. The Actor can be created from a Factory, Archetype, Blueprint, Class or an Asset.
        """
        ...
    def get_all_level_actors(self) -> Array[Actor]:
        r"""
        x.get_all_level_actors() -> Array[Actor]
        Find all loaded Actors in the world editor. Exclude actor that are pending kill, in PIE, PreviewEditor, ...
        """
        ...

class MaterialEditingLibrary(BlueprintFunctionLibrary):
    r"""
    Material Editing Library

    **C++ Source:**

    - **Module**: MaterialEditor
    - **File**: MaterialEditingLibrary.h
    """
    @classmethod
    def create_material_expression(cls, material: Material, expression_class: type(Class), node_pos_x: int = 0, node_pos_y: int = 0) -> MaterialExpression:
        r"""
        X.create_material_expression(material, expression_class, node_pos_x=0, node_pos_y=0) -> MaterialExpression
        Create a new material expression node within the supplied material
        """
        ...
