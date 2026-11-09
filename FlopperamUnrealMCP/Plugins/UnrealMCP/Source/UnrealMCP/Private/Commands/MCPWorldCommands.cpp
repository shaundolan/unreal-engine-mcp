// landscape_create / landscape_inspect / landscape_write.
// The Python API can place foliage, author PCG graphs and build splines, but it cannot create landscape
// components or read/write heights and weights; these three commands do only that. Everything procedural
// (noise, erosion, heightmap decoding, slope/height auto-paint) runs in the Python server.
#include "Commands/MCPWorldCommands.h"

#include "Editor.h"
#include "EngineUtils.h"
#include "Landscape.h"
#include "LandscapeComponent.h"
#include "LandscapeEdit.h"
#include "LandscapeEditLayer.h"
#include "LandscapeInfo.h"
#include "LandscapeLayerInfoObject.h"
#include "LandscapeProxy.h"
#include "LandscapeUtils.h"
#include "Materials/MaterialInterface.h"
#include "Misc/Base64.h"

namespace
{
	UWorld* EditorWorld()
	{
		return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
	}

	ALandscape* FindLandscape(const FString& Target, FString& OutError)
	{
		UWorld* World = EditorWorld();
		if (!World)
		{
			OutError = TEXT("no editor world");
			return nullptr;
		}
		TArray<ALandscape*> All;
		for (TActorIterator<ALandscape> It(World); It; ++It)
		{
			All.Add(*It);
		}
		if (Target.IsEmpty())
		{
			if (All.Num() == 1)
			{
				return All[0];
			}
			OutError = All.Num() == 0 ? TEXT("the level has no landscape")
				: FString::Printf(TEXT("the level has %d landscapes; pass target (label or name)"), All.Num());
			return nullptr;
		}
		for (ALandscape* L : All)
		{
			if (L->GetActorLabel().Equals(Target, ESearchCase::IgnoreCase) || L->GetName().Equals(Target, ESearchCase::IgnoreCase))
			{
				return L;
			}
		}
		OutError = FString::Printf(TEXT("no landscape labelled or named '%s'"), *Target);
		return nullptr;
	}

	bool ReadVector(const TSharedPtr<FJsonObject>& P, const TCHAR* Field, FVector& Out)
	{
		const TArray<TSharedPtr<FJsonValue>>* Arr;
		if (P->TryGetArrayField(Field, Arr) && Arr->Num() == 3)
		{
			Out = FVector((*Arr)[0]->AsNumber(), (*Arr)[1]->AsNumber(), (*Arr)[2]->AsNumber());
			return true;
		}
		return false;
	}

	TSharedPtr<FJsonValue> VectorJson(const FVector& V)
	{
		TArray<TSharedPtr<FJsonValue>> A;
		A.Add(MakeShared<FJsonValueNumber>(FMath::RoundToDouble(V.X * 1000.0) / 1000.0));
		A.Add(MakeShared<FJsonValueNumber>(FMath::RoundToDouble(V.Y * 1000.0) / 1000.0));
		A.Add(MakeShared<FJsonValueNumber>(FMath::RoundToDouble(V.Z * 1000.0) / 1000.0));
		return MakeShared<FJsonValueArray>(A);
	}

	TSharedPtr<FJsonValue> IntPairJson(int32 X, int32 Y)
	{
		TArray<TSharedPtr<FJsonValue>> A;
		A.Add(MakeShared<FJsonValueNumber>(X));
		A.Add(MakeShared<FJsonValueNumber>(Y));
		return MakeShared<FJsonValueArray>(A);
	}

	template <typename T>
	bool DecodeGrid(const FString& B64, int32 Expected, TArray<T>& Out, const TCHAR* What, FString& OutError)
	{
		TArray<uint8> Bytes;
		if (!FBase64::Decode(B64, Bytes))
		{
			OutError = FString::Printf(TEXT("%s: invalid base64"), What);
			return false;
		}
		if (Bytes.Num() != Expected * int32(sizeof(T)))
		{
			OutError = FString::Printf(TEXT("%s: expected %d values (%d bytes), got %d bytes"), What, Expected,
				Expected * int32(sizeof(T)), Bytes.Num());
			return false;
		}
		Out.SetNumUninitialized(Expected);
		FMemory::Memcpy(Out.GetData(), Bytes.GetData(), Bytes.Num());
		return true;
	}

	struct FExtent
	{
		int32 MinX = 0, MinY = 0, MaxX = -1, MaxY = -1;
		int32 Width() const { return MaxX - MinX + 1; }
		int32 Height() const { return MaxY - MinY + 1; }
		int32 Num() const { return Width() * Height(); }
	};

	bool GetExtent(ULandscapeInfo* Info, FExtent& E, FString& OutError)
	{
		if (!Info || !Info->GetLandscapeExtent(E.MinX, E.MinY, E.MaxX, E.MaxY) || E.Num() <= 0)
		{
			OutError = TEXT("landscape has no components");
			return false;
		}
		return true;
	}

	FGuid BaseEditLayerGuid(ALandscape* L)
	{
		const ULandscapeEditLayerBase* Layer = L->GetEditLayerConst(0);
		return Layer ? Layer->GetGuid() : FGuid();
	}

	/** Find or create the paint layer Name on L, with a layer info asset under Dir if it has none. */
	ULandscapeLayerInfoObject* EnsurePaintLayer(ALandscape* L, const FName& Name, const FString& Dir, TArray<FString>& Created)
	{
		ULandscapeInfo* Info = L->GetLandscapeInfo();
		ULandscapeLayerInfoObject* LayerInfo = Info ? Info->GetLayerInfoByName(Name) : nullptr;
		if (!LayerInfo)
		{
			const FString AssetName = Name.ToString() + TEXT("_LayerInfo");
			LayerInfo = LoadObject<ULandscapeLayerInfoObject>(nullptr, *(Dir / AssetName + TEXT(".") + AssetName), nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!LayerInfo)
			{
				LayerInfo = UE::Landscape::CreateTargetLayerInfo(Name, Dir, AssetName);
				if (LayerInfo)
				{
					Created.Add(LayerInfo->GetPathName());
				}
			}
		}
		if (!LayerInfo)
		{
			return nullptr;
		}
		if (L->HasTargetLayer(Name))
		{
			const FLandscapeTargetLayerSettings* Existing = L->GetTargetLayers().Find(Name);
			if (!Existing || Existing->LayerInfoObj != LayerInfo)
			{
				L->UpdateTargetLayer(Name, FLandscapeTargetLayerSettings(LayerInfo));
			}
		}
		else
		{
			L->AddTargetLayer(Name, FLandscapeTargetLayerSettings(LayerInfo));
		}
		if (Info)
		{
			Info->UpdateLayerInfoMap(L);
			const int32 Index = Info->GetLayerInfoIndex(Name);
			if (Index != INDEX_NONE)
			{
				Info->Layers[Index].LayerInfoObj = LayerInfo;
			}
		}
		return LayerInfo;
	}

	bool SetMaterial(ALandscape* L, const FString& Path, FString& OutError)
	{
		UMaterialInterface* M = LoadObject<UMaterialInterface>(nullptr, *Path);
		if (!M)
		{
			OutError = FString::Printf(TEXT("material '%s' not found"), *Path);
			return false;
		}
		L->Modify();
		L->LandscapeMaterial = M;
		FProperty* Prop = FindFProperty<FProperty>(ALandscapeProxy::StaticClass(), TEXT("LandscapeMaterial"));
		FPropertyChangedEvent Event(Prop);
		L->PostEditChangeProperty(Event);
		return true;
	}

	FString DefaultLayerDir(const TSharedPtr<FJsonObject>& P)
	{
		FString Dir;
		if (!P->TryGetStringField(TEXT("layer_info_dir"), Dir) || Dir.IsEmpty())
		{
			Dir = TEXT("/Game/Landscape/LayerInfos");
		}
		return Dir;
	}

	/** layers: ["Grass", ...] -> paint layers with layer infos. Appends errors. */
	void ApplyLayers(ALandscape* L, const TSharedPtr<FJsonObject>& P, TArray<FString>& Created, TArray<FString>& Errors)
	{
		const TArray<TSharedPtr<FJsonValue>>* Layers;
		if (!P->TryGetArrayField(TEXT("layers"), Layers))
		{
			return;
		}
		const FString Dir = DefaultLayerDir(P);
		for (const TSharedPtr<FJsonValue>& V : *Layers)
		{
			const FString Name = V->AsString();
			if (Name.IsEmpty() || !EnsurePaintLayer(L, FName(*Name), Dir, Created))
			{
				Errors.Add(FString::Printf(TEXT("could not create paint layer '%s'"), *Name));
			}
		}
	}

	bool WriteHeights(ALandscape* L, const FString& B64, FString& OutError)
	{
		ULandscapeInfo* Info = L->GetLandscapeInfo();
		FExtent E;
		TArray<uint16> Heights;
		if (!GetExtent(Info, E, OutError) || !DecodeGrid(B64, E.Num(), Heights, TEXT("heights"), OutError))
		{
			return false;
		}
		FScopedSetLandscapeEditingLayer Scope(L, BaseEditLayerGuid(L), [L] { L->RequestLayersContentUpdate(ELandscapeLayerUpdateMode::Update_Heightmap_All); });
		FHeightmapAccessor<false> Accessor(Info);
		Accessor.SetData(E.MinX, E.MinY, E.MaxX, E.MaxY, Heights.GetData());
		return true;
	}

	/** weights: {"Grass": b64, "Rock": b64}. Values are written as given (no re-normalisation): send weights that sum to 255. */
	bool WriteWeights(ALandscape* L, const TSharedPtr<FJsonObject>& Weights, const FString& Dir, TArray<FString>& Written,
		TArray<FString>& Created, FString& OutError)
	{
		ULandscapeInfo* Info = L->GetLandscapeInfo();
		FExtent E;
		if (!GetExtent(Info, E, OutError))
		{
			return false;
		}
		TArray<TPair<ULandscapeLayerInfoObject*, TArray<uint8>>> Pending;
		for (const auto& KV : Weights->Values)
		{
			const FString Key(*KV.Key);
			TArray<uint8> Data;
			if (!DecodeGrid(KV.Value->AsString(), E.Num(), Data, *FString::Printf(TEXT("weights[%s]"), *Key), OutError))
			{
				return false;
			}
			ULandscapeLayerInfoObject* LayerInfo = EnsurePaintLayer(L, FName(*Key), Dir, Created);
			if (!LayerInfo)
			{
				OutError = FString::Printf(TEXT("could not create paint layer '%s'"), *Key);
				return false;
			}
			Pending.Emplace(LayerInfo, MoveTemp(Data));
		}
		FScopedSetLandscapeEditingLayer Scope(L, BaseEditLayerGuid(L), [L] { L->RequestLayersContentUpdate(ELandscapeLayerUpdateMode::Update_Weightmap_All); });
		FLandscapeEditDataInterface Edit(Info);
		TSet<ULandscapeComponent*> Components;
		if (Edit.GetComponentsInRegion(E.MinX, E.MinY, E.MaxX, E.MaxY, &Components))
		{
			for (ULandscapeComponent* C : Components)
			{
				C->RequestWeightmapUpdate();
			}
		}
		for (TPair<ULandscapeLayerInfoObject*, TArray<uint8>>& Item : Pending)
		{
			Edit.SetAlphaData(Item.Key, E.MinX, E.MinY, E.MaxX, E.MaxY, Item.Value.GetData(), 0,
				ELandscapeLayerPaintingRestriction::None);
			Written.Add(Item.Key->GetLayerName().ToString());
		}
		Edit.Flush();
		return true;
	}

	TSharedPtr<FJsonValue> StringArray(const TArray<FString>& Items)
	{
		TArray<TSharedPtr<FJsonValue>> A;
		for (const FString& S : Items)
		{
			A.Add(MakeShared<FJsonValueString>(S));
		}
		return MakeShared<FJsonValueArray>(A);
	}

	TSharedPtr<FJsonObject> Describe(ALandscape* L, bool bHeights, bool bWeights)
	{
		TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
		ULandscapeInfo* Info = L->GetLandscapeInfo();
		Out->SetStringField(TEXT("label"), L->GetActorLabel());
		Out->SetStringField(TEXT("name"), L->GetName());
		Out->SetField(TEXT("location"), VectorJson(L->GetActorLocation()));
		Out->SetNumberField(TEXT("yaw"), L->GetActorRotation().Yaw);
		const FVector Scale = L->GetActorScale3D();
		Out->SetField(TEXT("scale"), VectorJson(Scale));
		Out->SetStringField(TEXT("material"), L->LandscapeMaterial ? L->LandscapeMaterial->GetPathName() : FString());
		FExtent E;
		FString Ignored;
		if (Info && GetExtent(Info, E, Ignored))
		{
			const int32 CompQuads = Info->ComponentSizeQuads;
			Out->SetField(TEXT("resolution"), IntPairJson(E.Width(), E.Height()));
			Out->SetField(TEXT("extent_min"), IntPairJson(E.MinX, E.MinY));
			Out->SetField(TEXT("component_count"), IntPairJson((E.Width() - 1) / FMath::Max(1, CompQuads), (E.Height() - 1) / FMath::Max(1, CompQuads)));
			Out->SetNumberField(TEXT("component_size_quads"), CompQuads);
			Out->SetNumberField(TEXT("section_size"), Info->SubsectionSizeQuads);
			Out->SetNumberField(TEXT("sections_per_component"), Info->ComponentNumSubsections);
			Out->SetField(TEXT("size_cm"), VectorJson(FVector((E.Width() - 1) * Scale.X, (E.Height() - 1) * Scale.Y, 0)));

			FLandscapeEditDataInterface Edit(Info, BaseEditLayerGuid(L));
			TArray<uint16> Heights;
			Heights.SetNumZeroed(E.Num());
			Edit.GetHeightDataFast(E.MinX, E.MinY, E.MaxX, E.MaxY, Heights.GetData(), 0);
			uint16 Lo = MAX_uint16, Hi = 0;
			double Sum = 0;
			for (uint16 H : Heights)
			{
				Lo = FMath::Min(Lo, H);
				Hi = FMath::Max(Hi, H);
				Sum += H;
			}
			const double ZPerUnit = Scale.Z / 128.0;
			const double Z0 = L->GetActorLocation().Z;
			TSharedPtr<FJsonObject> Stats = MakeShared<FJsonObject>();
			Stats->SetNumberField(TEXT("min"), Z0 + (double(Lo) - 32768.0) * ZPerUnit);
			Stats->SetNumberField(TEXT("max"), Z0 + (double(Hi) - 32768.0) * ZPerUnit);
			Stats->SetNumberField(TEXT("mean"), Z0 + (Sum / FMath::Max(1, Heights.Num()) - 32768.0) * ZPerUnit);
			Out->SetObjectField(TEXT("height_world_cm"), Stats);
			if (bHeights)
			{
				Out->SetStringField(TEXT("heights"), FBase64::Encode(reinterpret_cast<const uint8*>(Heights.GetData()), Heights.Num() * sizeof(uint16)));
			}

			TArray<TSharedPtr<FJsonValue>> Layers;
			TSharedPtr<FJsonObject> WeightData = MakeShared<FJsonObject>();
			for (const TPair<FName, FLandscapeTargetLayerSettings>& KV : L->GetTargetLayers())
			{
				TSharedPtr<FJsonObject> LJ = MakeShared<FJsonObject>();
				LJ->SetStringField(TEXT("name"), KV.Key.ToString());
				ULandscapeLayerInfoObject* LayerInfo = KV.Value.LayerInfoObj;
				LJ->SetStringField(TEXT("info"), LayerInfo ? LayerInfo->GetPathName() : FString());
				if (LayerInfo)
				{
					TArray<uint8> W;
					W.SetNumZeroed(E.Num());
					Edit.GetWeightDataFast(LayerInfo, E.MinX, E.MinY, E.MaxX, E.MaxY, W.GetData(), 0);
					double WSum = 0;
					int32 Dominant = 0;
					for (uint8 X : W)
					{
						WSum += X;
						Dominant += X >= 128 ? 1 : 0;
					}
					LJ->SetNumberField(TEXT("coverage"), WSum / (255.0 * FMath::Max(1, W.Num())));
					LJ->SetNumberField(TEXT("dominant_fraction"), double(Dominant) / FMath::Max(1, W.Num()));
					if (bWeights)
					{
						WeightData->SetStringField(KV.Key.ToString(), FBase64::Encode(W.GetData(), W.Num()));
					}
				}
				Layers.Add(MakeShared<FJsonValueObject>(LJ));
			}
			Out->SetArrayField(TEXT("layers"), Layers);
			if (bWeights)
			{
				Out->SetObjectField(TEXT("weights"), WeightData);
			}
		}
		FVector Origin, BoxExtent;
		L->GetActorBounds(false, Origin, BoxExtent);
		TSharedPtr<FJsonObject> Bounds = MakeShared<FJsonObject>();
		Bounds->SetField(TEXT("origin"), VectorJson(Origin));
		Bounds->SetField(TEXT("extent"), VectorJson(BoxExtent));
		Out->SetObjectField(TEXT("bounds"), Bounds);
		return Out;
	}

	TSharedPtr<FJsonObject> HandleCreate(const TSharedPtr<FJsonObject>& P)
	{
		UWorld* World = EditorWorld();
		if (!World)
		{
			return MCPError(TEXT("no editor world"));
		}
		int32 SectionSize = 63, Sections = 1;
		P->TryGetNumberField(TEXT("section_size"), SectionSize);
		P->TryGetNumberField(TEXT("sections_per_component"), Sections);
		static const int32 ValidSections[] = { 7, 15, 31, 63, 127, 255 };
		bool bValid = false;
		for (int32 S : ValidSections)
		{
			bValid |= S == SectionSize;
		}
		if (!bValid || (Sections != 1 && Sections != 2))
		{
			return MCPError(TEXT("section_size must be 7, 15, 31, 63, 127 or 255 and sections_per_component 1 or 2"));
		}
		int32 CountX = 8, CountY = 8;
		const TArray<TSharedPtr<FJsonValue>>* Count;
		if (P->TryGetArrayField(TEXT("component_count"), Count) && Count->Num() == 2)
		{
			CountX = int32((*Count)[0]->AsNumber());
			CountY = int32((*Count)[1]->AsNumber());
		}
		if (CountX < 1 || CountY < 1 || CountX > 32 || CountY > 32)
		{
			return MCPError(TEXT("component_count must be 1..32 per axis"));
		}
		const int32 QuadsPerComponent = SectionSize * Sections;
		const int32 SizeX = CountX * QuadsPerComponent + 1;
		const int32 SizeY = CountY * QuadsPerComponent + 1;

		TArray<uint16> Heights;
		FString HeightsB64, Error;
		if (P->TryGetStringField(TEXT("heights"), HeightsB64) && !HeightsB64.IsEmpty())
		{
			if (!DecodeGrid(HeightsB64, SizeX * SizeY, Heights, TEXT("heights"), Error))
			{
				return MCPError(Error);
			}
		}
		else
		{
			Heights.Init(32768, SizeX * SizeY);
		}

		FVector Center(0), Scale(100);
		ReadVector(P, TEXT("location"), Center);
		ReadVector(P, TEXT("scale"), Scale);
		double Yaw = 0;
		P->TryGetNumberField(TEXT("yaw"), Yaw);
		const FRotator Rotation(0, Yaw, 0);
		// location is the landscape's centre (like the New Landscape tool); the actor origin is its corner.
		const FVector Offset = FTransform(Rotation, FVector::ZeroVector, Scale).TransformVector(FVector(-(SizeX - 1) / 2.0, -(SizeY - 1) / 2.0, 0.0));

		FString MaterialPath;
		UMaterialInterface* Material = nullptr;
		if (P->TryGetStringField(TEXT("material"), MaterialPath) && !MaterialPath.IsEmpty())
		{
			Material = LoadObject<UMaterialInterface>(nullptr, *MaterialPath);
			if (!Material)
			{
				return MCPError(FString::Printf(TEXT("material '%s' not found"), *MaterialPath));
			}
		}

		FActorSpawnParameters Params;
		Params.ObjectFlags |= RF_Transactional;
		ALandscape* L = World->SpawnActor<ALandscape>(Center + Offset, Rotation, Params);
		if (!L)
		{
			return MCPError(TEXT("could not spawn a Landscape actor"));
		}
		L->LandscapeMaterial = Material;
		L->SetActorRelativeScale3D(Scale);
		L->StaticLightingLOD = FMath::DivideAndRoundUp(FMath::CeilLogTwo((SizeX * SizeY) / (2048 * 2048) + 1), (uint32)2);

		TMap<FGuid, TArray<uint16>> HeightPerLayer;
		HeightPerLayer.Add(FGuid(), MoveTemp(Heights));
		TMap<FGuid, TArray<FLandscapeImportLayerInfo>> MaterialLayers;
		MaterialLayers.Add(FGuid(), TArray<FLandscapeImportLayerInfo>());
		L->Import(FGuid::NewGuid(), 0, 0, SizeX - 1, SizeY - 1, Sections, SectionSize, HeightPerLayer, nullptr, MaterialLayers,
			ELandscapeImportAlphamapType::Additive, TArrayView<const FLandscapeLayer>());

		FString Label;
		if (P->TryGetStringField(TEXT("label"), Label) && !Label.IsEmpty())
		{
			L->SetActorLabel(Label);
		}
		ULandscapeInfo* Info = L->GetLandscapeInfo();
		if (Info)
		{
			Info->UpdateLayerInfoMap(L);
		}
		TArray<FString> Created, Errors;
		ApplyLayers(L, P, Created, Errors);

		const TSharedPtr<FJsonObject>* Weights;
		if (P->TryGetObjectField(TEXT("weights"), Weights) && (*Weights)->Values.Num() > 0)
		{
			TArray<FString> Written;
			if (!WriteWeights(L, *Weights, DefaultLayerDir(P), Written, Created, Error))
			{
				Errors.Add(Error);
			}
		}

		TSharedPtr<FJsonObject> Out = Describe(L, false, false);
		Out->SetField(TEXT("created_layer_infos"), StringArray(Created));
		Out->SetField(TEXT("errors"), StringArray(Errors));
		return Out;
	}

	TSharedPtr<FJsonObject> HandleInspect(const TSharedPtr<FJsonObject>& P)
	{
		FString Target, Error;
		P->TryGetStringField(TEXT("target"), Target);
		ALandscape* L = FindLandscape(Target, Error);
		if (!L)
		{
			return MCPError(Error);
		}
		bool bHeights = false, bWeights = false;
		P->TryGetBoolField(TEXT("include_heights"), bHeights);
		P->TryGetBoolField(TEXT("include_weights"), bWeights);
		return Describe(L, bHeights, bWeights);
	}

	TSharedPtr<FJsonObject> HandleWrite(const TSharedPtr<FJsonObject>& P)
	{
		FString Target, Error;
		P->TryGetStringField(TEXT("target"), Target);
		ALandscape* L = FindLandscape(Target, Error);
		if (!L)
		{
			return MCPError(Error);
		}
		L->Modify();
		TArray<FString> Done, Created, Errors;
		FString MaterialPath;
		if (P->TryGetStringField(TEXT("material"), MaterialPath) && !MaterialPath.IsEmpty())
		{
			if (!SetMaterial(L, MaterialPath, Error))
			{
				return MCPError(Error);
			}
			Done.Add(TEXT("material"));
		}
		ApplyLayers(L, P, Created, Errors);
		FString HeightsB64;
		if (P->TryGetStringField(TEXT("heights"), HeightsB64) && !HeightsB64.IsEmpty())
		{
			if (!WriteHeights(L, HeightsB64, Error))
			{
				return MCPError(Error);
			}
			Done.Add(TEXT("heights"));
		}
		const TSharedPtr<FJsonObject>* Weights;
		if (P->TryGetObjectField(TEXT("weights"), Weights) && (*Weights)->Values.Num() > 0)
		{
			TArray<FString> Written;
			if (!WriteWeights(L, *Weights, DefaultLayerDir(P), Written, Created, Error))
			{
				return MCPError(Error);
			}
			for (const FString& W : Written)
			{
				Done.Add(TEXT("weights:") + W);
			}
		}
		TSharedPtr<FJsonObject> Out = Describe(L, false, false);
		Out->SetField(TEXT("written"), StringArray(Done));
		Out->SetField(TEXT("created_layer_infos"), StringArray(Created));
		Out->SetField(TEXT("errors"), StringArray(Errors));
		return Out;
	}
}

void FMCPWorldCommands::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("landscape_create"), &HandleCreate, true);
	Registry.Register(TEXT("landscape_inspect"), &HandleInspect, false);
	Registry.Register(TEXT("landscape_write"), &HandleWrite, true);
}
