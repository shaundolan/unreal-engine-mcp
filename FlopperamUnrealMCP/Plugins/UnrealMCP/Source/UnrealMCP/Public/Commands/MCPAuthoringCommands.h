#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"
#include "Engine/Blueprint.h"

/**
 * Declarative authoring: export an asset as a JSON spec, apply a batch of ops in one undo step.
 *   bp_export / bp_apply          Blueprint variables, components, functions, graph nodes, pins, links
 *   widget_export / widget_apply  UMG widget trees (logic goes through bp_apply on the same asset)
 *   bt_export / bt_build          Blackboard keys + Behavior Tree structure
 */
class UNREALMCP_API FMCPBlueprintAuthoring
{
public:
	static void Register(IMCPCommandRegistry& Registry);
};

class UNREALMCP_API FMCPWidgetAuthoring
{
public:
	static void Register(IMCPCommandRegistry& Registry);
};

class UNREALMCP_API FMCPBehaviorTreeAuthoring
{
public:
	static void Register(IMCPCommandRegistry& Registry);
};

/** Shared helpers for the authoring groups. */
namespace MCPAuthoring
{
	/** "/Game/Dir/Name" or "/Game/Dir/Name.Name" -> package path + asset name. */
	UNREALMCP_API bool SplitAssetPath(const FString& InPath, FString& OutPackage, FString& OutName, FString& OutError);
	/** Load an asset of class T by package or object path. */
	UNREALMCP_API UObject* LoadAssetOfClass(const FString& InPath, UClass* Class);
	/** New, registered, dirty asset object in its own package (fails if one exists). */
	UNREALMCP_API UObject* CreateAssetObject(const FString& InPath, UClass* Class, FString& OutError);
	UNREALMCP_API bool SaveAsset(UObject* Asset);
	/** JSON value -> Unreal text form ("true", "5", "1,2,3" for vectors, plain strings). */
	UNREALMCP_API FString JsonToDefaultString(const TSharedPtr<FJsonValue>& Value);
	UNREALMCP_API TSharedPtr<FJsonValue> StringArray(const TArray<FString>& Items);
	/** Compile and return {status, ok, errors[], warnings[]} from the compiler log. */
	UNREALMCP_API TSharedPtr<FJsonObject> CompileBlueprintWithResults(class UBlueprint* Blueprint);
	UNREALMCP_API FString BlueprintStatusName(enum EBlueprintStatus Status);
}
