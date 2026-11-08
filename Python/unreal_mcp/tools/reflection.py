"""Generic UObject reflection: describe, read, write and call anything by path.

Together with execute_python these reach every UPROPERTY and UFUNCTION in the engine,
including ones the Python API does not expose.
"""

from __future__ import annotations

from typing import Any

from . import call


def register(mcp, get_conn):
    @mcp.tool()
    def object_describe(path: str = "", class_name: str = "", include_values: bool = False,
                        include_functions: bool = True, filter: str = "") -> dict[str, Any]:
        """Describe a UObject or UClass: properties (name, type, flags) and functions (signatures).

        path: object path, e.g. "/Game/BP_Player.BP_Player_C", an actor label in the current
              level (e.g. "PointLight_1"), or "/Script/Engine.Default__Character".
        class_name: alternatively a class, e.g. "StaticMeshActor" or "/Script/Engine.Character".
        include_values: also return current property values (objects only).
        filter: case-insensitive substring to narrow property/function names.
        """
        return call(get_conn, "object_describe", {
            "path": path, "class": class_name, "include_values": include_values,
            "include_functions": include_functions, "filter": filter})

    @mcp.tool()
    def property_get(path: str, property: str) -> dict[str, Any]:
        """Read any property of a UObject or actor. Supports dotted struct/sub-object paths,
        e.g. property="RootComponent.RelativeLocation" or "LightComponent.Intensity"."""
        return call(get_conn, "property_get", {"path": path, "property": property})

    @mcp.tool()
    def property_set(path: str, property: str, value: Any) -> dict[str, Any]:
        """Write any property of a UObject or actor (undoable). Value is JSON matching the
        property type: numbers, strings, {"X":..,"Y":..,"Z":..} for vectors, object paths for
        object references, arrays for TArray. Returns old and new values."""
        return call(get_conn, "property_set", {"path": path, "property": property, "value": value})

    @mcp.tool()
    def function_call(path: str, function: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call any UFUNCTION on an object (BlueprintCallable, CallInEditor, or native).

        args maps parameter names to JSON values. Returns the return value and out-params.
        For static library functions use the class default object, e.g.
        path="/Script/Engine.Default__KismetSystemLibrary"."""
        return call(get_conn, "function_call", {"path": path, "function": function, "args": args or {}})
