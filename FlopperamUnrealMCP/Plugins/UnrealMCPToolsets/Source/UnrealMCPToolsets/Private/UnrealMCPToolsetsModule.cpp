#include "CoreMinimal.h"
#include "Editor.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "ToolsetRegistry/ToolsetRegistry.h"
#include "ToolsetRegistry/ToolsetRegistrySubsystem.h"
#include "UnrealMCPToolset.h"

DEFINE_LOG_CATEGORY_STATIC(LogUnrealMCPToolsetsModule, Log, All);

/**
 * Registers the toolsets in Resources/UnrealMCPToolsets.json with Epic's ToolsetRegistry once the editor is up.
 * Epic's ModelContextProtocolEditor module listens to the registry and (re)publishes every toolset on its MCP
 * server: as list_toolsets / describe_toolset / call_tool (tool search, the default) or as native tools named
 * "UnrealMCP.<Toolset>.<tool>" (bEnableToolSearch=False).
 */
class FUnrealMCPToolsetsModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		if (IsRunningCommandlet())
		{
			return;
		}
		if (GEditor)
		{
			RegisterToolsets();
		}
		else
		{
			PostEngineInitHandle = FCoreDelegates::GetOnPostEngineInit().AddRaw(this, &FUnrealMCPToolsetsModule::RegisterToolsets);
		}
	}

	virtual void ShutdownModule() override
	{
		if (PostEngineInitHandle.IsValid())
		{
			FCoreDelegates::GetOnPostEngineInit().Remove(PostEngineInitHandle);
			PostEngineInitHandle.Reset();
		}
		if (UToolsetRegistrySubsystem* Subsystem = GEditor ? GEditor->GetEditorSubsystem<UToolsetRegistrySubsystem>() : nullptr)
		{
			for (const TSharedPtr<FUnrealMCPToolset>& Toolset : Registered)
			{
				Subsystem->ToolsetRegistry.UnregisterToolset(Toolset);
			}
		}
		Registered.Reset();
	}

private:
	void RegisterToolsets()
	{
		UToolsetRegistrySubsystem* Subsystem = GEditor ? GEditor->GetEditorSubsystem<UToolsetRegistrySubsystem>() : nullptr;
		if (!Subsystem)
		{
			UE_LOG(LogUnrealMCPToolsetsModule, Warning, TEXT("UnrealMCPToolsets: ToolsetRegistry subsystem not available; nothing registered"));
			return;
		}
		const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UnrealMCPToolsets"));
		const FString Path = Plugin.IsValid() ? FPaths::Combine(Plugin->GetBaseDir(), TEXT("Resources"), TEXT("UnrealMCPToolsets.json")) : FString();
		FString Text;
		if (Path.IsEmpty() || !FFileHelper::LoadFileToString(Text, *Path))
		{
			UE_LOG(LogUnrealMCPToolsetsModule, Error, TEXT("UnrealMCPToolsets: cannot read manifest '%s'"), *Path);
			return;
		}
		TSharedPtr<FJsonObject> Manifest;
		TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Manifest) || !Manifest.IsValid())
		{
			UE_LOG(LogUnrealMCPToolsetsModule, Error, TEXT("UnrealMCPToolsets: manifest '%s' is not valid JSON"), *Path);
			return;
		}
		FString Version = TEXT("0");
		Manifest->TryGetStringField(TEXT("version"), Version);
		const TSharedRef<const FString> Wrapper = MakeShared<const FString>(Manifest->GetStringField(TEXT("python_wrapper")));

		int32 ToolCount = 0;
		const TArray<TSharedPtr<FJsonValue>>* Toolsets = nullptr;
		Manifest->TryGetArrayField(TEXT("toolsets"), Toolsets);
		for (const TSharedPtr<FJsonValue>& Value : Toolsets ? *Toolsets : TArray<TSharedPtr<FJsonValue>>())
		{
			TSharedPtr<FUnrealMCPToolset> Toolset = FUnrealMCPToolset::FromManifest(Value.IsValid() ? Value->AsObject() : nullptr, Version, Wrapper);
			if (Toolset.IsValid() && Subsystem->ToolsetRegistry.RegisterToolset(Toolset))
			{
				ToolCount += Toolset->NumTools();
				Registered.Add(Toolset);
			}
		}
		UE_LOG(LogUnrealMCPToolsetsModule, Display, TEXT("UnrealMCPToolsets: registered %d toolsets (%d tools) with the ToolsetRegistry"),
			Registered.Num(), ToolCount);
	}

	FDelegateHandle PostEngineInitHandle;
	TArray<TSharedPtr<FUnrealMCPToolset>> Registered;
};

IMPLEMENT_MODULE(FUnrealMCPToolsetsModule, UnrealMCPToolsets)
