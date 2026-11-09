#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"
#include "Engine/Blueprint.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"

/**
 * Declarative authoring: export an asset as a JSON spec, apply a batch of ops in one undo step.
 *   bp_export / bp_apply          Blueprint variables, components, functions, graph nodes, pins, links
 *   widget_export / widget_apply  UMG widget trees (logic goes through bp_apply on the same asset)
 *   bt_export / bt_build          Blackboard keys + Behavior Tree structure
 *   animbp_export / animbp_apply  Animation Blueprints: state machines, states, transitions + bp_apply ops
 *   anim_asset_apply              Montages (segments, slots, sections) and named notifies
 *   gameplay_tags                 Gameplay tag table (DefaultGameplayTags.ini)
 *   niagara_export / niagara_apply  Niagara systems: emitters, stage module stacks, inputs, renderers, user params
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

class UNREALMCP_API FMCPAnimAuthoring
{
public:
	static void Register(IMCPCommandRegistry& Registry);
};

class UNREALMCP_API FMCPNiagaraAuthoring
{
public:
	static void Register(IMCPCommandRegistry& Registry);
};

class UNREALMCP_API FMCPGameplayTagCommands
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

	/** State shared by the ops of one apply call. Aliases are the "id"s given to nodes in this call. */
	struct FGraphApplyContext
	{
		UBlueprint* BP = nullptr;
		TMap<FString, UEdGraphNode*> Aliases;
		TMap<UEdGraph*, FIntPoint> Layout; // X = nodes placed so far, Y = first row
		TArray<TSharedPtr<FJsonObject>> DeferredDefaults;
		bool bStructural = false;
	};

	/**
	 * Extra op handler for a domain built on bp_apply (animbp_apply). Return true when it handled the op
	 * (and set bOk / Err); false hands the op to the standard bp_apply ops.
	 */
	using FOpExtension = TFunction<bool(FGraphApplyContext& Ctx, const FString& Op, const TSharedPtr<FJsonObject>& Spec, bool& bOk, FString& Err)>;

	/**
	 * bp_apply semantics for an already loaded/created Blueprint: run "operations" in one transaction, compile,
	 * roll back on failure (atomic) or dry_run, apply set_defaults, save. bCreated: delete the asset on rollback.
	 * Setup runs inside the transaction before the ops (e.g. to register aliases for existing nodes).
	 */
	UNREALMCP_API TSharedPtr<FJsonObject> ApplyBlueprintOps(UBlueprint* BP, bool bCreated, const TSharedPtr<FJsonObject>& Params,
		const FOpExtension& Extension = nullptr, TFunction<void(FGraphApplyContext&)> Setup = nullptr);
	/** Graph by name (EventGraph, a function, AnimGraph, any sub-graph) or by an alias / node id whose node owns a sub-graph. */
	UNREALMCP_API UEdGraph* ResolveGraph(FGraphApplyContext& Ctx, const FString& Name);
	/** Node by alias from this call or by node id (GUID, or a unique prefix of 8+ chars). */
	UNREALMCP_API UEdGraphNode* ResolveNodeRef(FGraphApplyContext& Ctx, const FString& Ref);
	/** Create a node from an add_node spec (kind: event, call_function, get_var, ...); placed, pins not yet set. */
	UNREALMCP_API UEdGraphNode* CreateGraphNode(FGraphApplyContext& Ctx, UEdGraph* Graph, const TSharedPtr<FJsonObject>& Spec, FString& Err);
	UNREALMCP_API void PlaceGraphNode(FGraphApplyContext& Ctx, UEdGraph* Graph, UEdGraphNode* Node, const TSharedPtr<FJsonObject>& Spec);
	/** Spec "pins": {"Name": value} -> input pin defaults (by-ref pins get a wired literal). */
	UNREALMCP_API bool ApplyPinDefaults(UEdGraphNode* Node, const TSharedPtr<FJsonObject>& Spec, FString& Err);
	/** Pin of a node by name, label or alias (exec/then/return/target); Dir EGPD_MAX = either. */
	UNREALMCP_API UEdGraphPin* FindNodePin(UEdGraphNode* Node, const FString& Name, EEdGraphPinDirection Dir);
	/** Link two pins with their graph's schema; Err explains a refusal and lists the pins. */
	UNREALMCP_API bool LinkPins(UEdGraphNode* From, const FString& FromPin, UEdGraphNode* To, const FString& ToPin, FString& Err);
	UNREALMCP_API FString GraphNodeId(const UEdGraphNode* Node);
	UNREALMCP_API TSharedPtr<FJsonObject> ExportGraphNode(UEdGraphNode* Node, bool bPins);
	/** bp_export's spec (variables, components, graphs); OnlyGraphs empty = all top-level graphs. */
	UNREALMCP_API TSharedPtr<FJsonObject> ExportBlueprintSpec(UBlueprint* Blueprint, const TArray<FString>& OnlyGraphs, bool bPins);
}
