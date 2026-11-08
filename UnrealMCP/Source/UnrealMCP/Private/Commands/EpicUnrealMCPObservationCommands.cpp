#include "Commands/EpicUnrealMCPObservationCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"
#include "Commands/EpicUnrealMCPPythonCommands.h"
#include "Editor.h"
#include "EditorViewportClient.h"
#include "LevelEditorViewport.h"
#include "FileHelpers.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "ImageUtils.h"
#include "Misc/Base64.h"
#include "Misc/App.h"
#include "Misc/EngineVersion.h"
#include "Misc/Paths.h"
// FStringOutputDevice moved headers across engine versions.
#if __has_include("Misc/StringOutputDevice.h")
#include "Misc/StringOutputDevice.h"
#endif
#include "HAL/PlatformProperties.h"
#include "Misc/OutputDeviceRedirector.h"
#include "HAL/FileManager.h"
#include "Interfaces/IPluginManager.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "RenderingThread.h"
#include "PlayInEditorDataTypes.h"
#include "Algo/Reverse.h"
#include "Logging/LogVerbosity.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

// ---------------------------------------------------------------------------------------------
// Log capture
// ---------------------------------------------------------------------------------------------

FMCPLogCapture& FMCPLogCapture::Get()
{
	static FMCPLogCapture Instance;
	return Instance;
}

void FMCPLogCapture::Serialize(const TCHAR* V, ELogVerbosity::Type Verbosity, const FName& Category)
{
	FScopeLock Guard(&Lock);
	FEntry E{ NextSeq++, FDateTime::UtcNow(), Category, ELogVerbosity::Type(Verbosity & ELogVerbosity::VerbosityMask), FString(V) };
	if (Ring.Num() < Capacity)
	{
		Ring.Add(MoveTemp(E));
	}
	else
	{
		Ring[Head] = MoveTemp(E);
		Head = (Head + 1) % Capacity;
	}
}

uint64 FMCPLogCapture::LastSeq() const
{
	FScopeLock Guard(&Lock);
	return NextSeq - 1;
}

TArray<FMCPLogCapture::FEntry> FMCPLogCapture::Query(uint64 SinceSeq, int32 MaxLines, ELogVerbosity::Type MinVerbosity,
	const FString& Filter, const FString& CategoryFilter) const
{
	FScopeLock Guard(&Lock);
	TArray<FEntry> Out;
	const int32 N = Ring.Num();
	// Walk newest -> oldest, then reverse, so MaxLines keeps the most recent.
	for (int32 k = 0; k < N && Out.Num() < MaxLines; ++k)
	{
		const int32 Idx = (Head - 1 - k + N) % N;
		const FEntry& E = Ring[Idx];
		if (E.Seq <= SinceSeq)
		{
			break;
		}
		if (E.Verbosity > MinVerbosity) // lower value = more severe
		{
			continue;
		}
		if (!CategoryFilter.IsEmpty() && !E.Category.ToString().Equals(CategoryFilter, ESearchCase::IgnoreCase))
		{
			continue;
		}
		if (!Filter.IsEmpty() && !E.Message.Contains(Filter))
		{
			continue;
		}
		Out.Add(E);
	}
	Algo::Reverse(Out);
	return Out;
}

static ELogVerbosity::Type ParseVerbosity(const FString& S)
{
	if (S.Equals(TEXT("Error"), ESearchCase::IgnoreCase)) return ELogVerbosity::Error;
	if (S.Equals(TEXT("Warning"), ESearchCase::IgnoreCase)) return ELogVerbosity::Warning;
	if (S.Equals(TEXT("Display"), ESearchCase::IgnoreCase)) return ELogVerbosity::Display;
	if (S.Equals(TEXT("Verbose"), ESearchCase::IgnoreCase)) return ELogVerbosity::Verbose;
	return ELogVerbosity::Log;
}

static TArray<TSharedPtr<FJsonValue>> EntriesToJson(const TArray<FMCPLogCapture::FEntry>& Entries)
{
	TArray<TSharedPtr<FJsonValue>> Arr;
	for (const FMCPLogCapture::FEntry& E : Entries)
	{
		TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
		O->SetNumberField(TEXT("seq"), double(E.Seq));
		O->SetStringField(TEXT("time"), E.Time.ToIso8601());
		O->SetStringField(TEXT("category"), E.Category.ToString());
		O->SetStringField(TEXT("verbosity"), ToString(E.Verbosity));
		O->SetStringField(TEXT("message"), E.Message);
		Arr.Add(MakeShared<FJsonValueObject>(O));
	}
	return Arr;
}

static TSharedPtr<FJsonObject> HandleEditorLog(const TSharedPtr<FJsonObject>& Params)
{
	double Lines = 200, Since = 0;
	FString Filter, MinV = TEXT("Log"), Category;
	Params->TryGetNumberField(TEXT("lines"), Lines);
	Params->TryGetNumberField(TEXT("since_seq"), Since);
	Params->TryGetStringField(TEXT("filter"), Filter);
	Params->TryGetStringField(TEXT("min_verbosity"), MinV);
	Params->TryGetStringField(TEXT("category"), Category);

	FMCPLogCapture& Log = FMCPLogCapture::Get();
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetArrayField(TEXT("entries"), EntriesToJson(
		Log.Query(uint64(Since), FMath::Clamp(int32(Lines), 1, 5000), ParseVerbosity(MinV), Filter, Category)));
	Out->SetNumberField(TEXT("last_seq"), double(Log.LastSeq()));
	return Out;
}

// ---------------------------------------------------------------------------------------------
// Viewport capture
// ---------------------------------------------------------------------------------------------

static TSharedPtr<FJsonObject> HandleViewportCapture(const TSharedPtr<FJsonObject>& Params)
{
	double MaxWidth = 1280;
	Params->TryGetNumberField(TEXT("max_width"), MaxWidth);

	FViewport* Viewport = nullptr;
	if (GCurrentLevelEditingViewportClient && GCurrentLevelEditingViewportClient->Viewport)
	{
		Viewport = GCurrentLevelEditingViewportClient->Viewport;
	}
	else if (GEditor)
	{
		// Nobody has clicked into a viewport yet (fresh editor, or agents only): use the first
		// perspective level viewport, then any level viewport, then whatever is active.
		for (FLevelEditorViewportClient* Client : GEditor->GetLevelViewportClients())
		{
			if (Client && Client->Viewport && Client->IsPerspective())
			{
				Viewport = Client->Viewport;
				break;
			}
		}
		if (!Viewport)
		{
			for (FLevelEditorViewportClient* Client : GEditor->GetLevelViewportClients())
			{
				if (Client && Client->Viewport)
				{
					Viewport = Client->Viewport;
					break;
				}
			}
		}
		if (!Viewport)
		{
			Viewport = GEditor->GetActiveViewport();
		}
	}
	if (!Viewport)
	{
		return MCPError(TEXT("no active editor viewport (editor running headless with -nullrhi?)"));
	}

	// Render a fresh frame so the capture reflects changes made by the previous command.
	if (GCurrentLevelEditingViewportClient)
	{
		GCurrentLevelEditingViewportClient->Invalidate();
	}
	Viewport->Draw();
	FlushRenderingCommands();

	TArray<FColor> Pixels;
	const FIntPoint Size = Viewport->GetSizeXY();
	if (Size.X <= 0 || Size.Y <= 0 || !Viewport->ReadPixels(Pixels) || Pixels.Num() != Size.X * Size.Y)
	{
		return MCPError(TEXT("failed to read viewport pixels"));
	}
	for (FColor& C : Pixels)
	{
		C.A = 255;
	}

	int32 W = Size.X, H = Size.Y;
	if (MaxWidth > 16 && W > int32(MaxWidth))
	{
		const int32 NewW = int32(MaxWidth);
		const int32 NewH = FMath::Max(1, int32(int64(H) * NewW / W));
		TArray<FColor> Resized;
		FImageUtils::ImageResize(W, H, Pixels, NewW, NewH, Resized, false);
		Pixels = MoveTemp(Resized);
		W = NewW;
		H = NewH;
	}

	TArray64<uint8> Png;
	FImageUtils::PNGCompressImageArray(W, H, TArrayView64<const FColor>(Pixels.GetData(), Pixels.Num()), Png);
	if (Png.Num() == 0)
	{
		return MCPError(TEXT("PNG encoding failed"));
	}

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetNumberField(TEXT("width"), W);
	Out->SetNumberField(TEXT("height"), H);
	Out->SetStringField(TEXT("format"), TEXT("png"));
	Out->SetStringField(TEXT("data_base64"), FBase64::Encode(Png.GetData(), uint32(Png.Num())));
	return Out;
}

// ---------------------------------------------------------------------------------------------
// Project context
// ---------------------------------------------------------------------------------------------

static TSharedPtr<FJsonObject> HandleProjectContext(const TSharedPtr<FJsonObject>& Params)
{
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());
	Out->SetStringField(TEXT("project_name"), FApp::GetProjectName());
	Out->SetStringField(TEXT("project_dir"), FPaths::ConvertRelativePathToFull(FPaths::ProjectDir()));
	Out->SetStringField(TEXT("platform"), FPlatformProperties::IniPlatformName());
	Out->SetStringField(TEXT("plugin_version"), UNREALMCP_PLUGIN_VERSION);
	Out->SetBoolField(TEXT("python_available"), FEpicUnrealMCPPythonCommands::IsPythonAvailable());

	const FString Stub = FPaths::ConvertRelativePathToFull(FPaths::ProjectIntermediateDir() / TEXT("PythonStub/unreal.py"));
	Out->SetStringField(TEXT("python_stub_path"), Stub);
	Out->SetBoolField(TEXT("python_stub_exists"), IFileManager::Get().FileExists(*Stub));

	if (UWorld* World = FEpicUnrealMCPReflectionCommands::GetEditorWorld())
	{
		Out->SetStringField(TEXT("current_level"), World->GetOutermost()->GetName());
	}
	Out->SetBoolField(TEXT("pie_running"), GEditor && GEditor->PlayWorld != nullptr);

	TArray<TSharedPtr<FJsonValue>> Plugins;
	for (const TSharedRef<IPlugin>& Plugin : IPluginManager::Get().GetEnabledPlugins())
	{
		if (Plugin->GetLoadedFrom() == EPluginLoadedFrom::Project || Plugins.Num() < 400)
		{
			Plugins.Add(MakeShared<FJsonValueString>(Plugin->GetName()));
		}
	}
	Out->SetArrayField(TEXT("enabled_plugins"), Plugins);

	IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
	TArray<FAssetData> Maps;
	Registry.GetAssetsByClass(UWorld::StaticClass()->GetClassPathName(), Maps);
	TArray<TSharedPtr<FJsonValue>> MapNames;
	for (const FAssetData& Map : Maps)
	{
		const FString Pkg = Map.PackageName.ToString();
		if (Pkg.StartsWith(TEXT("/Game/")))
		{
			MapNames.Add(MakeShared<FJsonValueString>(Pkg));
		}
	}
	Out->SetArrayField(TEXT("maps"), MapNames);
	return Out;
}

// ---------------------------------------------------------------------------------------------
// Console + editor actions
// ---------------------------------------------------------------------------------------------

static TSharedPtr<FJsonObject> HandleConsoleCommand(const TSharedPtr<FJsonObject>& Params)
{
	FString Command;
	if (!Params->TryGetStringField(TEXT("command"), Command) || Command.IsEmpty())
	{
		return MCPError(TEXT("missing 'command'"));
	}
	if (!GEditor)
	{
		return MCPError(TEXT("no editor"));
	}
	const uint64 Before = FMCPLogCapture::Get().LastSeq();
	FStringOutputDevice Captured;
	UWorld* World = GEditor->PlayWorld ? GEditor->PlayWorld.Get() : FEpicUnrealMCPReflectionCommands::GetEditorWorld();
	const bool bHandled = GEditor->Exec(World, *Command, Captured);

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("handled"), bHandled);
	Out->SetStringField(TEXT("output"), Captured);
	// Many commands print to the log instead of the passed device; flush buffered lines first.
	GLog->Flush();
	Out->SetArrayField(TEXT("log"), EntriesToJson(FMCPLogCapture::Get().Query(Before, 200, ELogVerbosity::Log, FString(), FString())));
	return Out;
}

static TSharedPtr<FJsonObject> HandleEditorAction(const TSharedPtr<FJsonObject>& Params)
{
	if (!GEditor)
	{
		return MCPError(TEXT("no editor"));
	}
	FString Action;
	Params->TryGetStringField(TEXT("action"), Action);
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("action"), Action);

	if (Action == TEXT("undo") || Action == TEXT("redo"))
	{
		double Count = 1;
		Params->TryGetNumberField(TEXT("count"), Count);
		int32 Done = 0;
		for (int32 i = 0; i < FMath::Clamp(int32(Count), 1, 100); ++i)
		{
			const bool bOk = Action == TEXT("undo") ? GEditor->UndoTransaction() : GEditor->RedoTransaction();
			if (!bOk)
			{
				break;
			}
			++Done;
		}
		Out->SetNumberField(TEXT("steps"), Done);
		return Out;
	}
	if (Action == TEXT("save_all"))
	{
		// An untitled (/Temp/) map would pop a modal Save As dialog and stall the game thread.
		UWorld* World = FEpicUnrealMCPReflectionCommands::GetEditorWorld();
		const bool bUntitledMap = World && World->GetOutermost()->GetName().StartsWith(TEXT("/Temp/"));
		const bool bOk = FEditorFileUtils::SaveDirtyPackages(/*bPromptUserToSave*/ false, /*bSaveMapPackages*/ !bUntitledMap, /*bSaveContentPackages*/ true);
		Out->SetBoolField(TEXT("saved"), bOk);
		if (bUntitledMap)
		{
			Out->SetStringField(TEXT("warning"), TEXT("current map is untitled and was NOT saved; save it once via execute_python: unreal.EditorLoadingAndSavingUtils.save_map(world, '/Game/Maps/Name')"));
		}
		return Out;
	}
	if (Action == TEXT("open_asset"))
	{
		FString AssetPath;
		Params->TryGetStringField(TEXT("asset_path"), AssetPath);
		UObject* Asset = LoadObject<UObject>(nullptr, *AssetPath, nullptr, LOAD_NoWarn);
		if (!Asset)
		{
			return MCPError(FString::Printf(TEXT("asset '%s' not found"), *AssetPath));
		}
		GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()->OpenEditorForAsset(Asset);
		return Out;
	}
	if (Action == TEXT("load_level"))
	{
		FString LevelPath;
		Params->TryGetStringField(TEXT("level_path"), LevelPath);
		bool bDiscard = false;
		Params->TryGetBoolField(TEXT("discard_changes"), bDiscard);
		if (UWorld* Current = FEpicUnrealMCPReflectionCommands::GetEditorWorld())
		{
			UPackage* Pkg = Current->GetOutermost();
			if (Pkg->IsDirty())
			{
				if (!bDiscard)
				{
					return MCPError(TEXT("current level has unsaved changes; run save_all first or pass discard_changes=true (loading would otherwise open a modal save prompt)"));
				}
				Pkg->SetDirtyFlag(false);
			}
		}
		UWorld* Loaded = UEditorLoadingAndSavingUtils::LoadMap(LevelPath);
		if (!Loaded)
		{
			return MCPError(FString::Printf(TEXT("could not load level '%s'"), *LevelPath));
		}
		Out->SetStringField(TEXT("current_level"), Loaded->GetOutermost()->GetName());
		return Out;
	}
	if (Action == TEXT("select_actors"))
	{
		const TArray<TSharedPtr<FJsonValue>>* Names = nullptr;
		Params->TryGetArrayField(TEXT("actor_names"), Names);
		GEditor->SelectNone(false, true);
		TArray<TSharedPtr<FJsonValue>> Missing;
		int32 Selected = 0;
		if (Names)
		{
			for (const TSharedPtr<FJsonValue>& V : *Names)
			{
				if (AActor* Actor = FEpicUnrealMCPReflectionCommands::FindActor(V->AsString()))
				{
					GEditor->SelectActor(Actor, true, false);
					++Selected;
				}
				else
				{
					Missing.Add(V);
				}
			}
		}
		GEditor->NoteSelectionChange();
		Out->SetNumberField(TEXT("selected"), Selected);
		Out->SetArrayField(TEXT("missing"), Missing);
		return Out;
	}
	if (Action == TEXT("focus_actor"))
	{
		FString Name;
		Params->TryGetStringField(TEXT("actor_name"), Name);
		AActor* Actor = FEpicUnrealMCPReflectionCommands::FindActor(Name);
		if (!Actor)
		{
			return MCPError(FString::Printf(TEXT("actor '%s' not found"), *Name));
		}
		GEditor->MoveViewportCamerasToActor(*Actor, false);
		return Out;
	}
	if (Action == TEXT("pie_start"))
	{
		if (GEditor->PlayWorld)
		{
			return MCPError(TEXT("PIE already running"));
		}
		FRequestPlaySessionParams Session;
		GEditor->RequestPlaySession(Session);
		Out->SetStringField(TEXT("note"), TEXT("PIE starts on the next editor tick; poll project_context.pie_running"));
		return Out;
	}
	if (Action == TEXT("pie_stop"))
	{
		if (!GEditor->PlayWorld)
		{
			return MCPError(TEXT("PIE is not running"));
		}
		GEditor->RequestEndPlayMap();
		return Out;
	}
	return MCPError(FString::Printf(TEXT("unknown editor action '%s'"), *Action));
}

void FEpicUnrealMCPObservationCommands::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("editor_log"), &HandleEditorLog, false);
	Registry.Register(TEXT("viewport_capture"), &HandleViewportCapture, false);
	Registry.Register(TEXT("project_context"), &HandleProjectContext, false);
	Registry.Register(TEXT("console_command"), &HandleConsoleCommand, false);
	// Never transactional: undo/redo inside a transaction corrupts the undo buffer.
	Registry.Register(TEXT("editor_action"), &HandleEditorAction, false);
}
