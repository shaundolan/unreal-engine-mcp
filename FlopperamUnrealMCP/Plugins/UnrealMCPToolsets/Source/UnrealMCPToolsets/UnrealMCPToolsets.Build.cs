using UnrealBuildTool;

public class UnrealMCPToolsets : ModuleRules
{
	public UnrealMCPToolsets(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PrivateDependencyModuleNames.AddRange(
			new string[]
			{
				"Core",
				"CoreUObject",
				"Engine",
				"Json",
				"Projects",          // IPluginManager: locate Resources/UnrealMCPToolsets.json
				"UnrealEd",          // GEditor
				"EditorSubsystem",
				"UnrealMCP",         // UEpicUnrealMCPBridge: the shared command registry
				"ToolsetRegistry"    // Epic's FToolset / UToolsetRegistrySubsystem (UE 5.8, experimental)
			}
		);
	}
}
