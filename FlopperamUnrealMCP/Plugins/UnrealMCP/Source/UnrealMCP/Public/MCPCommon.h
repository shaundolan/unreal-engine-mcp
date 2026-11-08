#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

UNREALMCP_API DECLARE_LOG_CATEGORY_EXTERN(LogUnrealMCP, Log, All);

#define UNREALMCP_PLUGIN_VERSION TEXT("0.2.0")

/** A command handler: params in, result object out. Return MCPError(...) on failure. */
using FMCPCommandHandler = TFunction<TSharedPtr<FJsonObject>(const TSharedPtr<FJsonObject>&)>;

/** {"success": false, "error": Message}: the bridge turns this into status=error. */
inline TSharedPtr<FJsonObject> MCPError(const FString& Message)
{
	TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
	Obj->SetBoolField(TEXT("success"), false);
	Obj->SetStringField(TEXT("error"), Message);
	return Obj;
}

/** Registration surface handed to each command group. */
class IMCPCommandRegistry
{
public:
	virtual ~IMCPCommandRegistry() = default;
	/** bTransactional: wrap the call in an FScopedTransaction so editor undo reverts it. */
	virtual void Register(const FString& Name, FMCPCommandHandler Handler, bool bTransactional = false) = 0;
};
