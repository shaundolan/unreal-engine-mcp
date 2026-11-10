#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "ToolsetRegistry/Toolset.h"

/**
 * One UnrealMCP toolset (e.g. "UnrealMCP.Blueprint") registered with Epic's ToolsetRegistry.
 *
 * Tools are described by Resources/UnrealMCPToolsets.json, generated from the Python server's FastMCP tool
 * definitions (python -m unreal_mcp.native_toolsets). Every tool runs through UEpicUnrealMCPBridge, the same command
 * registry the UnrealMCP TCP server dispatches to, so the native MCP server and the UnrealMCP server share one
 * implementation:
 *   command        -> Bridge->ExecuteCommandJson(<command>, params)
 *   editor_script  -> the same editor_scripts/<name>.py text run_editor_script sends, through execute_python
 *   frame_stats    -> async: start recording, poll on the core ticker, resolve the future when done
 */
class FUnrealMCPToolset final : public UE::ToolsetRegistry::FToolset
{
public:
	enum class EKind : uint8
	{
		Command,
		EditorScript,
		FrameStats
	};

	struct FToolSpec
	{
		EKind Kind = EKind::Command;
		FString Target;          // command name (Command)
		FString ScriptBody;      // rendered editor script without its ARGS line (EditorScript)
		TMap<FString, FString> Rename;
		TMap<FString, TSharedPtr<FJsonValue>> Defaults;
		bool bOkFlag = false;
		double TimeoutS = 120.0;
	};

	/** Builds a toolset from one "toolsets" entry of the manifest; returns null (and logs) if it is malformed. */
	static TSharedPtr<FUnrealMCPToolset> FromManifest(const TSharedPtr<FJsonObject>& ToolsetJson,
		const FString& Version, const TSharedRef<const FString>& PythonWrapper);

	virtual FString GetToolsetName() const override { return Name; }
	virtual FString GetToolsetVersion() const override { return Version; }
	virtual FString GetToolsetDescription() const override { return Description; }

	int32 NumTools() const { return Tools.Num(); }

	/** Exposed for tests/diagnostics: the params a tool call is dispatched with (defaults, nulls dropped, renames). */
	TSharedRef<FJsonObject> PrepareParams(const FToolSpec& Spec, const TSharedPtr<FJsonObject>& Arguments) const;

protected:
	virtual TFuture<TValueOrError<FString, FString>> ExecuteToolInternal(const FString& ToolName, const FString& JsonInput) override;
	virtual FString GetJsonSchemaInternal() const override { return SchemaJson; }

private:
	TValueOrError<FString, FString> RunCommand(const FToolSpec& Spec, const TSharedRef<FJsonObject>& Params) const;
	TValueOrError<FString, FString> RunEditorScript(const FToolSpec& Spec, const TSharedRef<FJsonObject>& Params) const;
	TFuture<TValueOrError<FString, FString>> RunFrameStats(const FToolSpec& Spec, const TSharedRef<FJsonObject>& Params) const;

	FString Name;
	FString Version;
	FString Description;
	FString SchemaJson;
	TMap<FString, FToolSpec> Tools;
	TSharedPtr<const FString> PythonWrapper;
};
