// Copyright Epic Games, Inc. All Rights Reserved.

using UnrealBuildTool;

public class UnrealMCP : ModuleRules
{
	public UnrealMCP(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;
		// Each command file keeps small file-local helpers (Str, Norm, ReadVector, ...). Parallel branches kept adding
		// same-named ones that collide when unity builds concatenate files, so compile files separately.
		bUseUnity = false;
		
		PublicDefinitions.Add("UNREALMCP_EXPORTS=1");

		PublicIncludePaths.AddRange(
			new string[] {
				System.IO.Path.Combine(ModuleDirectory, "Public"),
				System.IO.Path.Combine(ModuleDirectory, "Public/Commands"),
				System.IO.Path.Combine(ModuleDirectory, "Public/Commands/BlueprintGraph"),
				System.IO.Path.Combine(ModuleDirectory, "Public/Commands/BlueprintGraph/Nodes")
			}
		);

		PrivateIncludePaths.AddRange(
			new string[] {
				System.IO.Path.Combine(ModuleDirectory, "Private"),
				System.IO.Path.Combine(ModuleDirectory, "Private/Commands"),
				System.IO.Path.Combine(ModuleDirectory, "Private/Commands/BlueprintGraph"),
				System.IO.Path.Combine(ModuleDirectory, "Private/Commands/BlueprintGraph/Nodes")
			}
		);
		
		PublicDependencyModuleNames.AddRange(
			new string[]
			{
				"Core",
				"CoreUObject",
				"Engine",
				"InputCore",
				"Networking",
				"Sockets",
				"HTTP",
				"Json",
				"JsonUtilities",
				"DeveloperSettings",
				"PhysicsCore",
				"UnrealEd",           // For Blueprint editing
				"BlueprintGraph",     // For K2Node classes (F15-F22)
				"KismetCompiler"      // For Blueprint compilation (F15-F22)
			}
		);
		
		PrivateDependencyModuleNames.AddRange(
			new string[]
			{
				"EditorScriptingUtilities",
				"EditorSubsystem",
				"Slate",
				"SlateCore",
				"Kismet",
				"Projects",
				"AssetRegistry",
				"RenderCore",          // FlushRenderingCommands for viewport_capture; frame timings
				"RHI",                 // frame_stats GPU time
				"EnhancedInput",       // pie_input action injection
				"PythonScriptPlugin",  // execute_python
				"UMG",                 // widget_apply
				"UMGEditor",           // UWidgetBlueprint
				"AIModule",            // bt_build
				"GameplayTasks",
				"Landscape",          // landscape_create / landscape_inspect / landscape_write
				"Foliage",            // LandscapeEdit.h includes InstancedFoliageActor.h
				"AnimGraph",           // animbp_apply: state machines, states, transitions, asset players
				"AnimGraphRuntime",
				"AnimationBlueprintLibrary", // anim_asset_apply: notifies
				"GameplayTags",        // gameplay_tags
				"LevelEditor",         // level_open: ULevelEditorSubsystem::NewLevel; pie_start mode=viewport
				"MaterialEditor",      // UMCPScriptLibrary: material outputs Python cannot reach (ClearCoat, PixelDepthOffset, ...)
				"MetasoundEngine",     // metasound_apply: UMetaSoundBuilderSubsystem
				"MetasoundFrontend",   // metasound_export / metasound_nodes: document + node registry
				"MetasoundGraphCore",
				"MetasoundEditor",     // BuildToAsset / FindOrBeginBuilding
				"AudioExtensions"
			}
		);
		
		if (Target.bBuildEditor == true)
		{
			PrivateDependencyModuleNames.AddRange(
				new string[]
				{
					"PropertyEditor",      // For property editing
					"ToolMenus",           // For editor UI
					"BlueprintEditorLibrary" // For Blueprint utilities
				}
			);
		}
		
		DynamicallyLoadedModuleNames.AddRange(
			new string[]
			{
				// ... add any modules that your module loads dynamically here ...
			}
		);
	}
} 