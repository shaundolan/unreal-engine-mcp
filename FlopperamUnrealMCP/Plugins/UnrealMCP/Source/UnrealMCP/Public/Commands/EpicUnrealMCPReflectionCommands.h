#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"

class AActor;
class UWorld;

/** Generic UObject reflection: object_describe, property_get, property_set, function_call. */
class UNREALMCP_API FEpicUnrealMCPReflectionCommands
{
public:
	static void Register(IMCPCommandRegistry& Registry);

	/** Current editor world (not PIE). */
	static UWorld* GetEditorWorld();
	/** Actor in the editor world by label first, then by object name (case-insensitive). */
	static AActor* FindActor(const FString& LabelOrName);
	/** Object path ("/Game/X.X", "/Script/Engine.Default__Actor"), or an actor label/name. */
	static UObject* ResolveObject(const FString& PathOrLabel, FString& OutError);
	/** Class by short name ("StaticMeshActor") or path ("/Script/Engine.Character", "/Game/BP_X.BP_X_C"). */
	static UClass* ResolveClass(const FString& NameOrPath);
};
