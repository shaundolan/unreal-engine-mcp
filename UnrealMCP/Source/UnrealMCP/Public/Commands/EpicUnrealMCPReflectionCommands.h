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

	/**
	 * Set a (possibly nested) property from JSON with full editor semantics: converts into a scratch
	 * value first, then Modify / PreEditChange / copy / PostEditChange. Path like "RootComponent.RelativeLocation"
	 * or "CharacterMovement.MaxWalkSpeed". Optional old/new values for reporting.
	 */
	static bool SetPropertyByPath(UObject* Root, const FString& PropertyPath, const TSharedPtr<FJsonValue>& Value,
		FString& OutError, TSharedPtr<FJsonValue>* OutOld = nullptr, TSharedPtr<FJsonValue>* OutNew = nullptr);
	/** Every key of Props via SetPropertyByPath; returns the number set, appends "key: error" to OutErrors. */
	static int32 SetPropertiesByPath(UObject* Root, const TSharedPtr<FJsonObject>& Props, TArray<FString>& OutErrors);
	/** Read a (possibly nested) property as JSON; nullptr + OutError when the path does not resolve. */
	static TSharedPtr<FJsonValue> GetPropertyByPath(UObject* Root, const FString& PropertyPath, FString& OutError);
};
