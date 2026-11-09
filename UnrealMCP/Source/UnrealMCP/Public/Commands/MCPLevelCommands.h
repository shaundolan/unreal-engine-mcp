#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"

/**
 * Saving, world switching and compile-queue status, written for an editor with nobody at the keyboard.
 *   level_open     switch the editor world safely: drop Python references, collect garbage, unload a stale in-memory
 *                  copy of the target (which would otherwise fatal the editor with "World Memory Leaks"), then load
 *                  the map, or create it with NewLevel and save it.
 *   shader_status  shader jobs and asset builds (textures, meshes) still queued, for wait_for_shaders.
 */
class UNREALMCP_API FMCPLevelCommands
{
public:
	static void Register(IMCPCommandRegistry& Registry);

	/**
	 * Saves dirty map and/or content packages with no dialogs at all, plus the current world (and its sub-levels and
	 * external packages) when it was never written to disk: a map made with NewLevel is not dirty, so
	 * SaveDirtyPackages skips it. Result fields: saved_packages [package], failed [{package, error}],
	 * skipped [{package, reason}] (untitled /Temp maps, read-only roots).
	 */
	static TSharedPtr<FJsonObject> SaveAllNonInteractive(bool bSaveMaps, bool bSaveContent);

	/** level_open (also behind editor_action load_level). params: path, create, save_current, discard_changes. */
	static TSharedPtr<FJsonObject> OpenLevel(const TSharedPtr<FJsonObject>& Params);
};
