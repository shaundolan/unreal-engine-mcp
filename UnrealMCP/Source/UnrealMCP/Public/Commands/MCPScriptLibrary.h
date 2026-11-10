#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MCPScriptLibrary.generated.h"

class UMaterial;
class UMaterialExpression;

/**
 * Helpers for the plugin's editor scripts, reachable from editor Python as unreal.MCPScriptLibrary. They cover what the
 * Python API cannot do on its own.
 */
UCLASS()
class UNREALMCP_API UMCPScriptLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Connect an expression output to a material output by name. Covers every output UE has, including the ones
	 * EMaterialProperty hides from Python: ClearCoat / ClearCoatRoughness (CustomData0 / CustomData1), PixelDepthOffset,
	 * ShadingModel, Displacement, SurfaceThickness, CustomizedUV0..7, plus FrontMaterial (Substrate).
	 * @return "" on success, else the error.
	 */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP")
	static FString ConnectMaterialOutput(UMaterialExpression* FromExpression, const FString& FromOutputName, const FString& Output);

	/** Canonical names of every material output (MaterialAttributes included) that ConnectMaterialOutput accepts. */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP")
	static TArray<FString> ListMaterialOutputs();

	/** "Output<TAB>expression object name<TAB>expression output name" for every connected material output. */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP")
	static TArray<FString> GetConnectedMaterialOutputs(UMaterial* Material);

	/** Whether r.Substrate is on (the material root then exposes FrontMaterial). */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP")
	static bool IsSubstrateEnabled();

	/**
	 * Free an asset path that a deleted asset still occupies in memory: after a force delete the object can survive
	 * (a pending async build, a render or undo reference), and StaticFindObject still finds it, so importing or creating
	 * an asset at the same path fails ("bReplaceExisting is false, we are not overriding ..."). Objects at the path that
	 * are garbage, or that are neither on disk nor in the asset registry, are renamed into the transient package; live
	 * assets are never touched.
	 * @return "absent" (nothing in memory), "live" (a real asset is there; untouched) or "released:N" (N objects moved).
	 */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP")
	static FString ReleaseStaleAssetPath(const FString& AssetPath);
};
