#include "Commands/MCPScriptLibrary.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "MaterialEditingLibrary.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpression.h"
#include "Misc/PackageName.h"
#include "RenderUtils.h"
#include "SceneTypes.h"
#include "UObject/Package.h"
#include "UObject/UObjectHash.h"
#include "UObject/UObjectGlobals.h"

namespace
{
	FString NormName(const FString& S)
	{
		FString Out;
		for (TCHAR C : S)
		{
			if (FChar::IsAlnum(C))
			{
				Out.AppendChar(FChar::ToLower(C));
			}
		}
		return Out;
	}

	/** Every connectable output, canonical name (enum name without MP_) -> property, read from the engine's enum. */
	const TArray<TPair<FString, EMaterialProperty>>& AllOutputs()
	{
		static TArray<TPair<FString, EMaterialProperty>> Outputs;
		if (Outputs.Num() == 0)
		{
			const UEnum* Enum = StaticEnum<EMaterialProperty>();
			TSet<int64> Seen;
			for (int32 I = 0; Enum && I < Enum->NumEnums(); ++I)
			{
				const int64 Value = Enum->GetValueByIndex(I);
				FString Name = Enum->GetNameStringByIndex(I);
				if (Value < 0 || Value >= int64(MP_MAX) || Seen.Contains(Value) || Name.Contains(TEXT("DEPRECATED")))
				{
					continue; // MP_MAX, aliases (MP_LastCustomizedUVs), removed properties
				}
				const EMaterialProperty Prop = EMaterialProperty(Value);
				if (Prop == MP_DiffuseColor || Prop == MP_SpecularColor || Prop == MP_CustomOutput)
				{
					continue; // derived for Lightmass / not a material input
				}
				Seen.Add(Value);
				Name.RemoveFromStart(TEXT("MP_"));
				Outputs.Emplace(Name, Prop);
			}
		}
		return Outputs;
	}

	bool ResolveOutput(const FString& InName, EMaterialProperty& Out)
	{
		FString N = NormName(InName);
		if (N.StartsWith(TEXT("mp")) && InName.StartsWith(TEXT("MP_"), ESearchCase::IgnoreCase))
		{
			N.RightChopInline(2);
		}
		static const TMap<FString, FString> Aliases = {
			{ TEXT("clearcoat"), TEXT("customdata0") }, { TEXT("clearcoatroughness"), TEXT("customdata1") },
			{ TEXT("emissive"), TEXT("emissivecolor") }, { TEXT("ao"), TEXT("ambientocclusion") },
			{ TEXT("wpo"), TEXT("worldpositionoffset") }, { TEXT("subsurface"), TEXT("subsurfacecolor") },
			{ TEXT("pdo"), TEXT("pixeldepthoffset") }, { TEXT("substrate"), TEXT("frontmaterial") },
			{ TEXT("diffuse"), TEXT("basecolor") }, { TEXT("attributes"), TEXT("materialattributes") },
		};
		if (const FString* A = Aliases.Find(N))
		{
			N = *A;
		}
		for (const TPair<FString, EMaterialProperty>& O : AllOutputs())
		{
			const FString Key = NormName(O.Key);
			// "CustomizedUV3" and "CustomizedUVs3" both name MP_CustomizedUVs3
			if (Key == N || Key.Replace(TEXT("uvs"), TEXT("uv")) == N)
			{
				Out = O.Value;
				return true;
			}
		}
		return false;
	}

	FString OutputName(EMaterialProperty Prop)
	{
		for (const TPair<FString, EMaterialProperty>& O : AllOutputs())
		{
			if (O.Value == Prop) return O.Key;
		}
		return FString::Printf(TEXT("%d"), int32(Prop));
	}
}

FString UMCPScriptLibrary::ConnectMaterialOutput(UMaterialExpression* FromExpression, const FString& FromOutputName, const FString& Output)
{
	if (!FromExpression)
	{
		return TEXT("no expression");
	}
	UMaterial* Material = Cast<UMaterial>(FromExpression->GetOuter());
	if (!Material)
	{
		return TEXT("the expression is not in a Material (material functions have no material outputs; use a FunctionOutput)");
	}
	EMaterialProperty Prop = MP_MAX;
	if (!ResolveOutput(Output, Prop))
	{
		return FString::Printf(TEXT("unknown material output '%s'; outputs: %s (aliases: ClearCoat, ClearCoatRoughness, Emissive, AO, WPO, "
			"Subsurface, PDO, Substrate)"), *Output, *FString::Join(ListMaterialOutputs(), TEXT(", ")));
	}
	if (!Material->GetExpressionInputForProperty(Prop))
	{
		return FString::Printf(TEXT("material output %s is not available on %s"), *OutputName(Prop), *Material->GetName());
	}
	if (Prop >= MP_CustomizedUVs0 && Prop <= MP_CustomizedUVs7)
	{
		// The pin only exists (and is only used) for indices below NumCustomizedUVs.
		Material->NumCustomizedUVs = FMath::Max(Material->NumCustomizedUVs, int32(Prop - MP_CustomizedUVs0) + 1);
	}
	if (!UMaterialEditingLibrary::ConnectMaterialProperty(FromExpression, FromOutputName, Prop))
	{
		return FString::Printf(TEXT("could not connect %s output '%s' to %s (outputs: %s)"), *FromExpression->GetName(), *FromOutputName,
			*OutputName(Prop), *FString::Join(UMaterialEditingLibrary::GetMaterialExpressionOutputNames(FromExpression), TEXT(", ")));
	}
	return FString();
}

TArray<FString> UMCPScriptLibrary::ListMaterialOutputs()
{
	TArray<FString> Names;
	for (const TPair<FString, EMaterialProperty>& O : AllOutputs())
	{
		Names.Add(O.Key);
	}
	return Names;
}

TArray<FString> UMCPScriptLibrary::GetConnectedMaterialOutputs(UMaterial* Material)
{
	TArray<FString> Out;
	if (!Material)
	{
		return Out;
	}
	for (const TPair<FString, EMaterialProperty>& O : AllOutputs())
	{
		if (!Material->GetExpressionInputForProperty(O.Value))
		{
			continue;
		}
		if (UMaterialExpression* Node = UMaterialEditingLibrary::GetMaterialPropertyInputNode(Material, O.Value))
		{
			Out.Add(FString::Printf(TEXT("%s\t%s\t%s"), *O.Key, *Node->GetName(),
				*UMaterialEditingLibrary::GetMaterialPropertyInputNodeOutputName(Material, O.Value)));
		}
	}
	return Out;
}

bool UMCPScriptLibrary::IsSubstrateEnabled()
{
	return Substrate::IsSubstrateEnabled();
}

FString UMCPScriptLibrary::ReleaseStaleAssetPath(const FString& AssetPath)
{
	FString PackageName = AssetPath;
	int32 Dot = INDEX_NONE;
	if (PackageName.FindLastChar(TEXT('.'), Dot) && Dot > PackageName.Find(TEXT("/"), ESearchCase::CaseSensitive, ESearchDir::FromEnd))
	{
		PackageName.LeftInline(Dot);
	}
	if (!FPackageName::IsValidLongPackageName(PackageName))
	{
		return TEXT("absent");
	}
	// StaticFindObjectFast with no exclusion flags also finds garbage (force-deleted, not yet collected) objects: exactly
	// what the importer's StaticFindObject trips over.
	UPackage* Pkg = Cast<UPackage>(StaticFindObjectFast(UPackage::StaticClass(), nullptr, FName(*PackageName)));
	if (!Pkg)
	{
		return TEXT("absent");
	}
	TArray<FAssetData> Registered;
	IAssetRegistry::GetChecked().GetAssetsByPackageName(FName(*PackageName), Registered, /*bIncludeOnlyOnDiskAssets*/ false);

	TArray<UObject*> Objects;
#if __has_include("UObject/FindObjectFlags.h")
	GetObjectsWithPackage(Pkg, Objects, EGetObjectsFlags::None, RF_NoFlags, EInternalObjectFlags::None); // top-level only
#else
	GetObjectsWithPackage(Pkg, Objects, /*bIncludeNestedObjects*/ false, RF_NoFlags, EInternalObjectFlags::None);
#endif
	const bool bAnyGarbage = !IsValid(Pkg) || Objects.ContainsByPredicate([](UObject* O) { return O && !IsValid(O); });
	// Not registered, and not a file the registry simply has not scanned yet: a deleted asset's leftovers.
	const bool bPackageStale = !IsValid(Pkg)
		|| (Registered.Num() == 0 && (bAnyGarbage || !FPackageName::DoesPackageExist(PackageName)));
	TArray<UObject*> ToMove;
	for (UObject* Obj : Objects)
	{
		if (Obj && Obj != Pkg && (bPackageStale || !IsValid(Obj)))
		{
			ToMove.Add(Obj);
		}
	}
	if (ToMove.Num() == 0 && !bPackageStale)
	{
		return TEXT("live");
	}
	const ERenameFlags Flags = REN_DontCreateRedirectors | REN_NonTransactional | REN_DoNotDirty;
	for (UObject* Obj : ToMove)
	{
		Obj->ClearFlags(RF_Public | RF_Standalone);
		const FName NewName = MakeUniqueObjectName(GetTransientPackage(), Obj->GetClass(), FName(*(Obj->GetName() + TEXT("_MCPStale"))));
		Obj->Rename(*NewName.ToString(), GetTransientPackage(), Flags);
	}
	if (bPackageStale)
	{
		// The empty package itself would otherwise still be found by CreatePackage (with its stale flags and linker).
		Pkg->ClearFlags(RF_Standalone);
		const FName NewPkgName = MakeUniqueObjectName(nullptr, UPackage::StaticClass(),
			FName(*FString::Printf(TEXT("/Temp/MCPStale_%s"), *FPackageName::GetShortName(PackageName))));
		Pkg->Rename(*NewPkgName.ToString(), nullptr, Flags);
	}
	return FString::Printf(TEXT("released:%d"), ToMove.Num());
}
