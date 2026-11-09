// Non-interactive saving, safe world switching and compile-queue status.
#include "Commands/MCPLevelCommands.h"

#include "AssetCompilingManager.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Level.h"
#include "Engine/LevelStreaming.h"
#include "Engine/World.h"
#include "FileHelpers.h"
#include "HAL/FileManager.h"
#include "IPythonScriptPlugin.h"
#include "LevelEditorSubsystem.h"
#include "Misc/PackageName.h"
#include "PackageTools.h"
#include "ShaderCompiler.h"
#include "UObject/GarbageCollection.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

namespace
{
TSharedPtr<FJsonValue> Str(const FString& S) { return MakeShared<FJsonValueString>(S); }

TSharedPtr<FJsonValue> Entry(const FString& Package, const TCHAR* Field, const FString& Text)
{
	TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
	O->SetStringField(TEXT("package"), Package);
	O->SetStringField(Field, Text);
	return MakeShared<FJsonValueObject>(O);
}

FString PackageFilename(const UPackage* Pkg)
{
	FString Filename;
	const FString& Ext = Pkg->ContainsMap() ? FPackageName::GetMapPackageExtension() : FPackageName::GetAssetPackageExtension();
	FPackageName::TryConvertLongPackageNameToFilename(Pkg->GetName(), Filename, Ext);
	return Filename;
}

bool NeverWritten(const UPackage* Pkg)
{
	return Pkg->HasAnyPackageFlags(PKG_NewlyCreated) || !FPackageName::DoesPackageExist(Pkg->GetName());
}

/** The current world's persistent level, loaded sub-levels and their external (OFPA) packages that are dirty or
 *  were never written. A NewLevel map is saved once by NewLevel and then not dirty; if that save failed it is never
 *  picked up by SaveDirtyPackages, so it is collected here explicitly. */
void GatherWorldPackages(UWorld* World, TArray<UPackage*>& Out)
{
	if (!World)
	{
		return;
	}
	for (ULevel* Level : World->GetLevels())
	{
		if (!Level)
		{
			continue;
		}
		UPackage* LevelPkg = Level->GetPackage();
		if (LevelPkg && (LevelPkg->IsDirty() || NeverWritten(LevelPkg)))
		{
			Out.AddUnique(LevelPkg);
		}
		for (UPackage* Ext : Level->GetLoadedExternalObjectPackages())
		{
			if (Ext && (Ext->IsDirty() || Ext->HasAnyPackageFlags(PKG_NewlyCreated)))
			{
				Out.AddUnique(Ext);
			}
		}
	}
}

/** Drops Python wrappers of editor objects (script globals, reference cycles) so GC can free the old world. */
void CollectPythonGarbage()
{
	IPythonScriptPlugin* Python = IPythonScriptPlugin::Get();
	if (Python && Python->IsPythonAvailable())
	{
		Python->ExecPythonCommand(TEXT("import gc as _mcp_gc; _mcp_gc.collect(); del _mcp_gc"));
	}
}

FString NormalizePackageName(FString Path)
{
	Path.TrimStartAndEndInline();
	int32 Dot = INDEX_NONE;
	if (Path.FindLastChar(TEXT('.'), Dot) && Dot > Path.Find(TEXT("/"), ESearchCase::CaseSensitive, ESearchDir::FromEnd))
	{
		Path.LeftInline(Dot); // "/Game/Maps/Arena.Arena" -> "/Game/Maps/Arena"
	}
	return Path;
}
}

TSharedPtr<FJsonObject> FMCPLevelCommands::SaveAllNonInteractive(bool bSaveMaps, bool bSaveContent)
{
	TArray<UPackage*> Candidates;
	if (bSaveMaps)
	{
		TArray<UPackage*> Worlds;
		FEditorFileUtils::GetDirtyWorldPackages(Worlds);
		for (UPackage* P : Worlds) Candidates.AddUnique(P);
		GatherWorldPackages(GEditor ? GEditor->GetEditorWorldContext().World() : nullptr, Candidates);
	}
	if (bSaveContent)
	{
		TArray<UPackage*> Content;
		FEditorFileUtils::GetDirtyContentPackages(Content);
		for (UPackage* P : Content) Candidates.AddUnique(P);
	}

	TArray<UPackage*> ToSave;
	TArray<TSharedPtr<FJsonValue>> Saved, Failed, Skipped;
	for (UPackage* Pkg : Candidates)
	{
		if (!Pkg || Pkg == GetTransientPackage() || Pkg->HasAnyPackageFlags(PKG_PlayInEditor | PKG_CompiledIn))
		{
			continue;
		}
		const FString Name = Pkg->GetName();
		if (FPackageName::IsTempPackage(Name))
		{
			Skipped.Add(Entry(Name, TEXT("reason"), TEXT("untitled map (/Temp): open it with level_open(path, create=true) or save it once with "
				"unreal.EditorLoadingAndSavingUtils.save_map(world, '/Game/Maps/Name')")));
			continue;
		}
		if (!FPackageName::IsValidLongPackageName(Name, /*bIncludeReadOnlyRoots*/ false))
		{
			Skipped.Add(Entry(Name, TEXT("reason"), TEXT("read-only root (engine or script package)")));
			continue;
		}
		ToSave.Add(Pkg);
	}

	if (ToSave.Num() > 0)
	{
		// FEditorFileUtils::SaveDirtyPackages(bPromptUserToSave=false) still opens modal dialogs: revision-control
		// checkout ("Unable to Check Out From Revision Control!"), "make writable" for read-only files and the
		// failed-save summary. In an offscreen editor nobody can answer them and the game thread blocks forever.
		// With GIsRunningUnattendedScript every prompt is skipped; SavePackages also saves maps that are not dirty.
		TGuardValue<bool> Unattended(GIsRunningUnattendedScript, true);
		UEditorLoadingAndSavingUtils::SavePackages(ToSave, /*bOnlyDirty*/ false);
	}

	for (UPackage* Pkg : ToSave)
	{
		const FString Name = Pkg->GetName();
		if (!Pkg->IsDirty() && FPackageName::DoesPackageExist(Name))
		{
			Saved.Add(Str(Name));
			continue;
		}
		const FString File = PackageFilename(Pkg);
		FString Why = TEXT("save failed (see editor_log)");
		if (!File.IsEmpty() && IFileManager::Get().FileExists(*File) && IFileManager::Get().IsReadOnly(*File))
		{
			Why = FString::Printf(TEXT("file is read-only: %s"), *FPaths::ConvertRelativePathToFull(File));
		}
		Failed.Add(Entry(Name, TEXT("error"), Why));
	}

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetArrayField(TEXT("saved_packages"), Saved);
	Out->SetArrayField(TEXT("failed"), Failed);
	Out->SetArrayField(TEXT("skipped"), Skipped);
	Out->SetBoolField(TEXT("all_saved"), Failed.Num() == 0);
	return Out;
}

/**
 * params: path ("/Game/Maps/Arena"), create (bool: make it with NewLevel and save it if it does not exist),
 *         save_current (bool: save dirty packages first), discard_changes (bool)
 * result: current_level, created, already_open, unloaded_stale_copy, saved_current {..}
 */
TSharedPtr<FJsonObject> FMCPLevelCommands::OpenLevel(const TSharedPtr<FJsonObject>& Params)
{
	if (!GEditor)
	{
		return MCPError(TEXT("no editor"));
	}
	if (GEditor->PlayWorld)
	{
		return MCPError(TEXT("PIE is running; stop it first (editor_action pie_stop)"));
	}
	FString Path;
	Params->TryGetStringField(TEXT("path"), Path);
	const FString PackageName = NormalizePackageName(Path);
	FText Reason;
	if (!FPackageName::IsValidLongPackageName(PackageName, false, &Reason))
	{
		return MCPError(FString::Printf(TEXT("'%s' is not a map path like /Game/Maps/Arena: %s"), *Path, *Reason.ToString()));
	}
	bool bCreate = false, bSaveCurrent = false, bDiscard = false;
	Params->TryGetBoolField(TEXT("create"), bCreate);
	Params->TryGetBoolField(TEXT("save_current"), bSaveCurrent);
	Params->TryGetBoolField(TEXT("discard_changes"), bDiscard);

	TGuardValue<bool> Unattended(GIsRunningUnattendedScript, true); // no "save changes?" prompts on the way
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("created"), false);
	Out->SetBoolField(TEXT("unloaded_stale_copy"), false);

	UWorld* Current = GEditor->GetEditorWorldContext().World();
	if (Current && Current->GetOutermost()->GetName() == PackageName)
	{
		Out->SetStringField(TEXT("current_level"), PackageName);
		Out->SetBoolField(TEXT("already_open"), true);
		return Out;
	}
	if (Current)
	{
		TArray<UPackage*> Dirty;
		GatherWorldPackages(Current, Dirty);
		Dirty.RemoveAll([](UPackage* P) { return !P->IsDirty(); });
		if (bSaveCurrent)
		{
			TSharedPtr<FJsonObject> SaveResult = FMCPLevelCommands::SaveAllNonInteractive(true, true);
			Out->SetObjectField(TEXT("saved_current"), SaveResult);
			if (!SaveResult->GetBoolField(TEXT("all_saved")))
			{
				return MCPError(TEXT("could not save the current packages; not switching (see save_all for details)"));
			}
		}
		else if (Dirty.Num() > 0)
		{
			if (!bDiscard)
			{
				return MCPError(FString::Printf(TEXT("the current level %s has unsaved changes; pass save_current=true or discard_changes=true"),
					*Current->GetOutermost()->GetName()));
			}
			for (UPackage* P : Dirty) P->SetDirtyFlag(false);
			Current->GetOutermost()->SetDirtyFlag(false);
		}
	}

	// Python wrappers of the old world's actors keep it alive: LoadMap then fatals with "World Memory Leaks".
	CollectPythonGarbage();
	CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS);

	// A UWorld of the target that is already in memory but is not the editor world (a duplicate_asset copy, an asset
	// loaded by load_asset, a map left over from NewLevel, or a sub-level streamed in by the current map) makes MAP
	// LOAD fatal with "World Memory Leaks". Unload it.
	if (UPackage* Existing = FindPackage(nullptr, *PackageName))
	{
		if (UWorld* Stale = UWorld::FindWorldInPackage(Existing); Stale && Stale != GEditor->GetEditorWorldContext().World())
		{
			if (Existing->IsDirty() && !bDiscard)
			{
				return MCPError(FString::Printf(TEXT("%s is in memory with unsaved changes (not as the editor world); save it with "
					"asset_manage save or pass discard_changes=true to drop them"), *PackageName));
			}
			// The current world streams the target in as a sub-level (a title / wrapper map): its streaming level
			// keeps the package loaded, so it cannot be unloaded while that world is open. Leave the current world
			// first (a blank untitled map, nothing is saved or modified), which unloads its streaming levels.
			UWorld* EditorWorld = GEditor->GetEditorWorldContext().World();
			bool bStreamedByCurrent = false;
			if (EditorWorld)
			{
				for (ULevelStreaming* Streaming : EditorWorld->GetStreamingLevels())
				{
					if (Streaming && Streaming->GetWorldAssetPackageFName() == FName(*PackageName))
					{
						bStreamedByCurrent = true;
						break;
					}
				}
				bStreamedByCurrent |= Stale->PersistentLevel && Stale->PersistentLevel->OwningWorld == EditorWorld;
			}
			FText UnloadError;
			auto TryUnload = [&]()
			{
				if (UPackage* Pkg = FindPackage(nullptr, *PackageName))
				{
					if (UWorld::FindWorldInPackage(Pkg))
					{
						UPackageTools::UnloadPackages({ Pkg }, UnloadError, /*bUnloadDirtyPackages*/ true);
					}
				}
				CollectPythonGarbage();
				CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS);
				UPackage* Still = FindPackage(nullptr, *PackageName);
				return !(Still && UWorld::FindWorldInPackage(Still));
			};
			bool bUnloaded = false;
			if (!bStreamedByCurrent)
			{
				bUnloaded = TryUnload();
			}
			if (!bUnloaded)
			{
				// Streamed in by the current map, or held by something the current world owns: switch away and retry.
				const FString Leaving = EditorWorld ? EditorWorld->GetOutermost()->GetName() : FString();
				if (!UEditorLoadingAndSavingUtils::NewBlankMap(/*bSaveExistingMap*/ false))
				{
					return MCPError(FString::Printf(TEXT("refusing to load %s: its world is loaded as a sub-level of %s and switching to a "
						"blank map to unload it failed"), *PackageName, *Leaving));
				}
				Out->SetStringField(TEXT("left_world"), Leaving);
				Out->SetBoolField(TEXT("unloaded_streaming_levels"), bStreamedByCurrent);
				bUnloaded = TryUnload();
			}
			if (!bUnloaded)
			{
				return MCPError(FString::Printf(TEXT("refusing to load %s: a standalone copy of its world is still in memory and could not be "
					"unloaded%s. Loading it would crash the editor with 'World Memory Leaks'. Drop Python references to it "
					"and retry, or restart the editor."), *PackageName,
					UnloadError.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(" (%s)"), *UnloadError.ToString())));
			}
			Out->SetBoolField(TEXT("unloaded_stale_copy"), true);
		}
	}

	if (!FPackageName::DoesPackageExist(PackageName))
	{
		if (!bCreate)
		{
			return MCPError(FString::Printf(TEXT("map %s does not exist; pass create=true to make it"), *PackageName));
		}
		ULevelEditorSubsystem* LES = GEditor->GetEditorSubsystem<ULevelEditorSubsystem>();
		if (!LES || !LES->NewLevel(PackageName))
		{
			return MCPError(FString::Printf(TEXT("NewLevel(%s) failed (see editor_log)"), *PackageName));
		}
		Out->SetBoolField(TEXT("created"), true);
		// NewLevel saves the map once; a failed save leaves it unwritten and invisible to SaveDirtyPackages.
		TSharedPtr<FJsonObject> SaveResult = FMCPLevelCommands::SaveAllNonInteractive(true, false);
		Out->SetObjectField(TEXT("save"), SaveResult);
		if (!FPackageName::DoesPackageExist(PackageName))
		{
			return MCPError(FString::Printf(TEXT("created %s but could not write it to disk (see editor_log)"), *PackageName));
		}
	}
	else
	{
		if (!UEditorLoadingAndSavingUtils::LoadMap(PackageName))
		{
			return MCPError(FString::Printf(TEXT("could not load %s"), *PackageName));
		}
	}
	UWorld* Now = GEditor->GetEditorWorldContext().World();
	Out->SetStringField(TEXT("current_level"), Now ? Now->GetOutermost()->GetName() : FString());
	return Out;
}

static TSharedPtr<FJsonObject> HandleShaderStatus(const TSharedPtr<FJsonObject>&)
{
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	const int32 Shaders = GShaderCompilingManager ? GShaderCompilingManager->GetNumRemainingJobs() : 0;
	const int32 Pending = GShaderCompilingManager ? GShaderCompilingManager->GetNumPendingJobs() : 0;
	const bool bCompiling = GShaderCompilingManager && GShaderCompilingManager->IsCompiling();
	const int32 Assets = FAssetCompilingManager::Get().GetNumRemainingAssets();
	Out->SetNumberField(TEXT("shader_jobs"), Shaders);
	Out->SetNumberField(TEXT("shader_jobs_pending"), Pending);
	Out->SetBoolField(TEXT("shaders_compiling"), bCompiling);
	Out->SetNumberField(TEXT("asset_builds"), Assets);
	Out->SetBoolField(TEXT("idle"), !bCompiling && Shaders == 0 && Assets == 0);
	return Out;
}

void FMCPLevelCommands::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("level_open"), &FMCPLevelCommands::OpenLevel, false);
	Registry.Register(TEXT("shader_status"), &HandleShaderStatus, false);
}
