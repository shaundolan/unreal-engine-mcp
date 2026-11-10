// niagara_export / niagara_apply / niagara_particles: whole Niagara systems as JSON, edited by batched ops in one
// undo step (atomic, rolled back on any failure or compile error, dry run), and live particle counts.
//
// Editing goes through the same stack view models the Niagara editor UI uses (FNiagaraSystemViewModel ->
// UNiagaraStackViewModel -> script groups -> module items -> function inputs), so rapid-iteration parameters,
// static switches, dynamic inputs, data interfaces and inheritance behave exactly as when a human clicks.
// Module reordering relinks the stack graph's parameter-map chain directly (the engine's MoveModule isn't exported).
#include "Commands/MCPAuthoringCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Curves/RichCurve.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_Niagara.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "EditorAssetLibrary.h"
#include "EngineUtils.h"
#include "JsonObjectConverter.h"
#include "Materials/MaterialInterface.h"
#include "Engine/StaticMesh.h"
#include "NiagaraComponent.h"
#include "NiagaraDataInterface.h"
#include "NiagaraDataSetAccessor.h"
#include "NiagaraEmitter.h"
#include "NiagaraEmitterHandle.h"
#include "NiagaraEmitterInstance.h"
#include "NiagaraExternalSystemEditorUtilities.h"
#include "NiagaraGraph.h"
#include "NiagaraLightRendererProperties.h"
#include "NiagaraMeshRendererProperties.h"
#include "NiagaraNodeFunctionCall.h"
#include "NiagaraNodeOutput.h"
#include "NiagaraRendererProperties.h"
#include "NiagaraRibbonRendererProperties.h"
#include "NiagaraScript.h"
#include "NiagaraSimulationStageBase.h"
#include "NiagaraSpriteRendererProperties.h"
#include "NiagaraSystem.h"
#include "NiagaraSystemInstance.h"
#include "NiagaraSystemInstanceController.h"
#include "NiagaraTypes.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"
#include "UObject/UObjectIterator.h"
#include "ViewModels/NiagaraEmitterHandleViewModel.h"
#include "ViewModels/NiagaraEmitterViewModel.h"
#include "ViewModels/NiagaraSystemViewModel.h"
#include "ViewModels/Stack/NiagaraStackEntry.h"
#include "ViewModels/Stack/NiagaraStackFunctionInput.h"
#include "ViewModels/Stack/NiagaraStackGraphUtilities.h"
#include "ViewModels/Stack/NiagaraStackModuleItem.h"
#include "ViewModels/Stack/NiagaraStackRendererItem.h"
#include "ViewModels/Stack/NiagaraStackScriptItemGroup.h"
#include "ViewModels/Stack/NiagaraStackViewModel.h"

namespace MCPNiagaraPriv
{
using MCPAuthoring::StringArray;

FString Str(const TSharedPtr<FJsonObject>& O, const TCHAR* F)
{
	FString V;
	if (O.IsValid()) O->TryGetStringField(F, V);
	return V;
}

TSharedPtr<FJsonObject> Obj(const TSharedPtr<FJsonObject>& O, const TCHAR* F)
{
	const TSharedPtr<FJsonObject>* V = nullptr;
	return O.IsValid() && O->TryGetObjectField(F, V) ? *V : TSharedPtr<FJsonObject>();
}

/** Lower-case, alphanumerics only: "Spawn Rate" == "SpawnRate" == "spawn_rate". */
FString NiaNorm(const FString& In)
{
	FString Out;
	for (TCHAR C : In)
	{
		if (FChar::IsAlnum(C)) Out.AppendChar(FChar::ToLower(C));
	}
	return Out;
}

TSharedPtr<FJsonValue> Num(double V) { return MakeShared<FJsonValueNumber>(V); }

TSharedPtr<FJsonValue> NumArray(std::initializer_list<double> Values)
{
	TArray<TSharedPtr<FJsonValue>> A;
	for (double V : Values) A.Add(Num(FMath::RoundToDouble(V * 10000.0) / 10000.0));
	return MakeShared<FJsonValueArray>(A);
}

bool ReadFloats(const TSharedPtr<FJsonValue>& V, TArray<double>& Out)
{
	Out.Reset();
	if (!V.IsValid()) return false;
	if (V->Type == EJson::Number)
	{
		Out.Add(V->AsNumber());
		return true;
	}
	if (V->Type == EJson::Array)
	{
		for (const TSharedPtr<FJsonValue>& E : V->AsArray())
		{
			if (!E.IsValid() || E->Type != EJson::Number) return false;
			Out.Add(E->AsNumber());
		}
		return true;
	}
	if (V->Type == EJson::Object)
	{
		const TSharedPtr<FJsonObject> O = V->AsObject();
		static const TCHAR* Keys[][4] = {{TEXT("r"), TEXT("g"), TEXT("b"), TEXT("a")}, {TEXT("x"), TEXT("y"), TEXT("z"), TEXT("w")}};
		for (const auto& Set : Keys)
		{
			if (!O->HasField(Set[0])) continue;
			for (int32 I = 0; I < 4; ++I)
			{
				double D = 0;
				if (O->TryGetNumberField(Set[I], D)) Out.Add(D);
				else if (I == 3 && Set[0][0] == TEXT('r')) Out.Add(1.0);
			}
			return Out.Num() > 0;
		}
	}
	return false;
}

bool ReadBox(const TSharedPtr<FJsonObject>& O, FBox& Out, FString& Err)
{
	TArray<double> Mn, Mx;
	const TSharedPtr<FJsonValue> MinV = O.IsValid() ? O->TryGetField(TEXT("min")) : nullptr;
	const TSharedPtr<FJsonValue> MaxV = O.IsValid() ? O->TryGetField(TEXT("max")) : nullptr;
	if (!ReadFloats(MinV, Mn) || !ReadFloats(MaxV, Mx) || Mn.Num() != 3 || Mx.Num() != 3)
	{
		Err = TEXT("bounds need \"min\": [x,y,z] and \"max\": [x,y,z]");
		return false;
	}
	Out = FBox(FVector(Mn[0], Mn[1], Mn[2]), FVector(Mx[0], Mx[1], Mx[2]));
	return true;
}

TSharedPtr<FJsonObject> BoxJson(const FBox& B)
{
	TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
	O->SetField(TEXT("min"), NumArray({B.Min.X, B.Min.Y, B.Min.Z}));
	O->SetField(TEXT("max"), NumArray({B.Max.X, B.Max.Y, B.Max.Z}));
	return O;
}

template <typename T>
T* LoadAs(const FString& Path)
{
	if (Path.IsEmpty()) return nullptr;
	FString P = Path;
	if (!P.Contains(TEXT(".")))
	{
		P = P + TEXT(".") + FPackageName::GetLongPackageAssetName(P);
	}
	return Cast<T>(StaticLoadObject(T::StaticClass(), nullptr, *P, nullptr, LOAD_NoWarn | LOAD_Quiet));
}

// ---------------------------------------------------------------------------------------------
// Names: stages, types, value modes
// ---------------------------------------------------------------------------------------------

FString StageKey(ENiagaraScriptUsage Usage)
{
	switch (Usage)
	{
	case ENiagaraScriptUsage::SystemSpawnScript: return TEXT("system_spawn");
	case ENiagaraScriptUsage::SystemUpdateScript: return TEXT("system_update");
	case ENiagaraScriptUsage::EmitterSpawnScript: return TEXT("emitter_spawn");
	case ENiagaraScriptUsage::EmitterUpdateScript: return TEXT("emitter_update");
	case ENiagaraScriptUsage::ParticleSpawnScript:
	case ENiagaraScriptUsage::ParticleSpawnScriptInterpolated: return TEXT("particle_spawn");
	case ENiagaraScriptUsage::ParticleUpdateScript: return TEXT("particle_update");
	case ENiagaraScriptUsage::ParticleEventScript: return TEXT("event");
	case ENiagaraScriptUsage::ParticleSimulationStageScript: return TEXT("simulation_stage");
	default: return TEXT("other");
	}
}

bool IsSystemUsage(ENiagaraScriptUsage U)
{
	return U == ENiagaraScriptUsage::SystemSpawnScript || U == ENiagaraScriptUsage::SystemUpdateScript;
}

/** Value kind by script struct, so static-switch variants of a type match too. */
FString Kind(const FNiagaraTypeDefinition& T)
{
	if (T.IsEnum()) return TEXT("enum");
	const UScriptStruct* S = T.GetScriptStruct();
	if (!S) return FString();
	if (S == FNiagaraTypeDefinition::GetFloatStruct()) return TEXT("float");
	if (S == FNiagaraTypeDefinition::GetIntStruct()) return TEXT("int");
	if (S == FNiagaraTypeDefinition::GetBoolStruct()) return TEXT("bool");
	if (S == FNiagaraTypeDefinition::GetVec2Struct()) return TEXT("vec2");
	if (S == FNiagaraTypeDefinition::GetVec3Struct()) return TEXT("vec3");
	if (S == FNiagaraTypeDefinition::GetVec4Struct()) return TEXT("vec4");
	if (S == FNiagaraTypeDefinition::GetColorStruct()) return TEXT("color");
	if (S == FNiagaraTypeDefinition::GetPositionStruct()) return TEXT("position");
	if (S == FNiagaraTypeDefinition::GetQuatStruct()) return TEXT("quat");
	return FString();
}

/** User-defined enums (most Niagara module enums) have NewEnumeratorN names; their display names are what people see. */
FString EnumValueName(const UEnum* E, int64 Value)
{
	const FString Name = E->GetNameStringByValue(Value);
	return Name.StartsWith(TEXT("NewEnumerator")) ? E->GetDisplayNameTextByValue(Value).ToString() : Name;
}

FString TypeName(const FNiagaraTypeDefinition& T)
{
	const FString K = Kind(T);
	if (!K.IsEmpty() && K != TEXT("enum")) return K;
	if (T.IsEnum() && T.GetEnum()) return TEXT("enum:") + T.GetEnum()->GetName();
	if (T.IsDataInterface() && T.GetClass()) return TEXT("di:") + T.GetClass()->GetName();
	if (T.GetClass()) return TEXT("object:") + T.GetClass()->GetName();
	return T.GetName();
}

bool ParseTypeName(const FString& InName, FNiagaraTypeDefinition& Out)
{
	const FString N = NiaNorm(InName);
	if (N == TEXT("float")) Out = FNiagaraTypeDefinition::GetFloatDef();
	else if (N == TEXT("int") || N == TEXT("int32") || N == TEXT("integer")) Out = FNiagaraTypeDefinition::GetIntDef();
	else if (N == TEXT("bool") || N == TEXT("boolean")) Out = FNiagaraTypeDefinition::GetBoolDef();
	else if (N == TEXT("vec2") || N == TEXT("vector2") || N == TEXT("vector2d")) Out = FNiagaraTypeDefinition::GetVec2Def();
	else if (N == TEXT("vec3") || N == TEXT("vector") || N == TEXT("vector3")) Out = FNiagaraTypeDefinition::GetVec3Def();
	else if (N == TEXT("vec4") || N == TEXT("vector4")) Out = FNiagaraTypeDefinition::GetVec4Def();
	else if (N == TEXT("color") || N == TEXT("linearcolor")) Out = FNiagaraTypeDefinition::GetColorDef();
	else if (N == TEXT("position")) Out = FNiagaraTypeDefinition::GetPositionDef();
	else if (N == TEXT("quat") || N == TEXT("quaternion")) Out = FNiagaraTypeDefinition::GetQuatDef();
	else if (N == TEXT("material")) Out = FNiagaraTypeDefinition::GetUMaterialDef();
	else if (N == TEXT("staticmesh") || N == TEXT("mesh")) Out = FNiagaraTypeDefinition::GetUStaticMeshDef();
	else if (N == TEXT("texture")) Out = FNiagaraTypeDefinition::GetUTextureDef();
	else return false;
	return true;
}

FString ModeName(UNiagaraStackFunctionInput::EValueMode M)
{
	using E = UNiagaraStackFunctionInput::EValueMode;
	switch (M)
	{
	case E::Local: return TEXT("local");
	case E::Linked: return TEXT("linked");
	case E::Dynamic: return TEXT("dynamic");
	case E::Data: return TEXT("data");
	case E::ObjectAsset: return TEXT("object");
	case E::Expression: return TEXT("expression");
	case E::DefaultFunction: return TEXT("default_function");
	case E::InvalidOverride: return TEXT("invalid_override");
	case E::UnsupportedDefault: return TEXT("unsupported_default");
	default: return TEXT("none");
	}
}

// ---------------------------------------------------------------------------------------------
// Typed values <-> JSON
// ---------------------------------------------------------------------------------------------

TSharedPtr<FJsonValue> ValueToJson(const FNiagaraTypeDefinition& Type, const uint8* Mem)
{
	if (!Mem) return MakeShared<FJsonValueNull>();
	const FString K = Kind(Type);
	if (K == TEXT("float")) return Num(FMath::RoundToDouble(*(const float*)Mem * 10000.0) / 10000.0);
	if (K == TEXT("bool")) return MakeShared<FJsonValueBoolean>(((const FNiagaraBool*)Mem)->GetValue());
	if (K == TEXT("int") || K == TEXT("enum"))
	{
		const int32 I = *(const int32*)Mem;
		if (UEnum* E = Type.GetEnum())
		{
			return MakeShared<FJsonValueString>(EnumValueName(E, I));
		}
		return Num(I);
	}
	if (K == TEXT("vec2"))
	{
		const FVector2f& V = *(const FVector2f*)Mem;
		return NumArray({V.X, V.Y});
	}
	if (K == TEXT("vec3") || K == TEXT("position"))
	{
		const FVector3f& V = *(const FVector3f*)Mem;
		return NumArray({V.X, V.Y, V.Z});
	}
	if (K == TEXT("vec4") || K == TEXT("quat"))
	{
		const FVector4f& V = *(const FVector4f*)Mem;
		return NumArray({V.X, V.Y, V.Z, V.W});
	}
	if (K == TEXT("color"))
	{
		const FLinearColor& C = *(const FLinearColor*)Mem;
		return NumArray({C.R, C.G, C.B, C.A});
	}
	if (UScriptStruct* S = Type.GetScriptStruct())
	{
		TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
		if (FJsonObjectConverter::UStructToJsonObject(S, Mem, O.ToSharedRef(), 0, 0)) return MakeShared<FJsonValueObject>(O);
	}
	return MakeShared<FJsonValueNull>();
}

/** JSON -> bytes of Type's struct. Enums take a name (or display name) or an int. */
bool JsonToValue(const FNiagaraTypeDefinition& Type, const TSharedPtr<FJsonValue>& V, TArray<uint8>& Out, FString& Err)
{
	UScriptStruct* S = Type.IsEnum() ? FNiagaraTypeDefinition::GetIntStruct() : Type.GetScriptStruct();
	if (!S)
	{
		Err = FString::Printf(TEXT("type %s has no local value (use link, dynamic or object)"), *TypeName(Type));
		return false;
	}
	Out.SetNumZeroed(S->GetStructureSize());
	S->InitializeStruct(Out.GetData());
	uint8* Mem = Out.GetData();
	TArray<double> F;
	const FString TN = TypeName(Type);
	const FString K = Kind(Type);
	if (K == TEXT("bool"))
	{
		if (!V.IsValid() || (V->Type != EJson::Boolean && V->Type != EJson::Number))
		{
			Err = TEXT("expected true/false");
			return false;
		}
		((FNiagaraBool*)Mem)->SetValue(V->Type == EJson::Boolean ? V->AsBool() : V->AsNumber() != 0);
		return true;
	}
	if (Type.IsEnum())
	{
		UEnum* E = Type.GetEnum();
		int64 Value = INDEX_NONE;
		if (V.IsValid() && V->Type == EJson::Number) Value = (int64)V->AsNumber();
		else if (V.IsValid() && V->Type == EJson::String && E)
		{
			const FString Want = NiaNorm(V->AsString());
			for (int32 I = 0; I < E->NumEnums() - 1; ++I)
			{
				if (NiaNorm(E->GetNameStringByIndex(I)) == Want || NiaNorm(E->GetDisplayNameTextByIndex(I).ToString()) == Want)
				{
					Value = E->GetValueByIndex(I);
					break;
				}
			}
		}
		if (Value == INDEX_NONE)
		{
			TArray<FString> Names;
			if (E) for (int32 I = 0; I < E->NumEnums() - 1; ++I) Names.Add(EnumValueName(E, E->GetValueByIndex(I)));
			Err = FString::Printf(TEXT("enum %s has no value %s (one of: %s)"), E ? *E->GetName() : TEXT("?"),
				V.IsValid() && V->Type == EJson::String ? *V->AsString() : TEXT("?"), *FString::Join(Names, TEXT(", ")));
			return false;
		}
		*(int32*)Mem = (int32)Value;
		return true;
	}
	const bool bVec = K == TEXT("vec2") || K == TEXT("vec3") || K == TEXT("vec4") || K == TEXT("color") || K == TEXT("position") || K == TEXT("quat");
	if (K == TEXT("float") || K == TEXT("int") || bVec)
	{
		if (!ReadFloats(V, F))
		{
			Err = FString::Printf(TEXT("expected a number or a list of numbers for %s"), *TN);
			return false;
		}
		int32 Want = 1;
		if (K == TEXT("vec2")) Want = 2;
		else if (K == TEXT("vec3") || K == TEXT("position")) Want = 3;
		else if (bVec) Want = 4;
		if (F.Num() == 1 && Want > 1)
		{
			F.Init(F[0], Want); // a scalar fills every component (uniform scale)
			if (K == TEXT("color")) F[3] = 1.0;
		}
		if (K == TEXT("color") && F.Num() == 3) F.Add(1.0);
		if (F.Num() != Want)
		{
			Err = FString::Printf(TEXT("%s needs %d numbers, got %d"), *TN, Want, F.Num());
			return false;
		}
		if (K == TEXT("int")) *(int32*)Mem = FMath::RoundToInt(F[0]);
		else
		{
			float* P = (float*)Mem;
			for (int32 I = 0; I < Want; ++I) P[I] = (float)F[I];
		}
		return true;
	}
	if (V.IsValid() && V->Type == EJson::Object && FJsonObjectConverter::JsonObjectToUStruct(V->AsObject().ToSharedRef(), S, Mem))
	{
		return true;
	}
	Err = FString::Printf(TEXT("cannot convert value to %s"), *TN);
	return false;
}

// ---------------------------------------------------------------------------------------------
// Reflection helpers (renderers, emitter data, data interfaces)
// ---------------------------------------------------------------------------------------------

TSharedPtr<FJsonValue> PropToJson(FProperty* P, const void* ValuePtr)
{
	if (FObjectPropertyBase* OP = CastField<FObjectPropertyBase>(P))
	{
		UObject* O = OP->GetObjectPropertyValue(ValuePtr);
		return O ? TSharedPtr<FJsonValue>(MakeShared<FJsonValueString>(O->GetPathName())) : TSharedPtr<FJsonValue>(MakeShared<FJsonValueNull>());
	}
	TSharedPtr<FJsonValue> J = FJsonObjectConverter::UPropertyToJsonValue(P, ValuePtr, 0, CPF_Transient);
	if (J.IsValid()) return J;
	FString Text;
	P->ExportTextItem_Direct(Text, ValuePtr, nullptr, nullptr, PPF_None);
	return MakeShared<FJsonValueString>(Text);
}

/** Editable properties of Container whose value differs from Defaults (both of Struct). */
TSharedPtr<FJsonObject> ChangedProperties(const UStruct* Struct, const void* Container, const void* Defaults, const TSet<FName>& Skip)
{
	TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
	for (TFieldIterator<FProperty> It(Struct); It; ++It)
	{
		FProperty* P = *It;
		if (!P->HasAnyPropertyFlags(CPF_Edit) || P->HasAnyPropertyFlags(CPF_Transient | CPF_Deprecated) || Skip.Contains(P->GetFName()))
		{
			continue;
		}
		if (FStructProperty* SP = CastField<FStructProperty>(P))
		{
			if (SP->Struct && SP->Struct->GetName() == TEXT("NiagaraVariableAttributeBinding")) continue; // reported as bindings
		}
		const void* A = P->ContainerPtrToValuePtr<void>(Container);
		const void* B = Defaults ? P->ContainerPtrToValuePtr<void>(Defaults) : nullptr;
		if (B && P->Identical(A, B, PPF_None)) continue;
		O->SetField(P->GetName(), PropToJson(P, A));
	}
	return O;
}

FProperty* FindPropNormalized(const UStruct* Struct, const FString& Name)
{
	if (FProperty* P = Struct->FindPropertyByName(FName(*Name))) return P;
	const FString N = NiaNorm(Name);
	for (TFieldIterator<FProperty> It(Struct); It; ++It)
	{
		if (NiaNorm(It->GetName()) == N || NiaNorm(It->GetDisplayNameText().ToString()) == N) return *It;
		if (It->GetName().StartsWith(TEXT("b")) && NiaNorm(It->GetName().Mid(1)) == N) return *It;
	}
	return nullptr;
}

/** Curves of a curve data interface (UNiagaraDataInterfaceCurve and friends: FRichCurve properties). */
void CurveProps(UObject* DI, TArray<FStructProperty*>& Out)
{
	Out.Reset();
	if (!DI) return;
	for (TFieldIterator<FStructProperty> It(DI->GetClass()); It; ++It)
	{
		if (It->Struct == FRichCurve::StaticStruct()) Out.Add(*It);
	}
}

TSharedPtr<FJsonValue> CurveToJson(const FRichCurve& C)
{
	TArray<TSharedPtr<FJsonValue>> Keys;
	for (const FRichCurveKey& K : C.GetConstRefOfKeys())
	{
		Keys.Add(NumArray({K.Time, K.Value}));
	}
	return MakeShared<FJsonValueArray>(Keys);
}

/**
 * Set the keys of a curve DI. Spec: [[t, v...], ...] (one value per curve: float 1, vec2 2, vec3 3, color 4) or
 * {"keys": [...], "interp": "linear|cubic|constant"} or {"RedCurve": [[t, v], ...], ...}.
 */
bool ApplyCurve(UObject* DI, const TSharedPtr<FJsonValue>& Spec, FString& Err)
{
	TArray<FStructProperty*> Curves;
	CurveProps(DI, Curves);
	if (Curves.Num() == 0)
	{
		Err = FString::Printf(TEXT("%s has no curves"), DI ? *DI->GetClass()->GetName() : TEXT("data interface"));
		return false;
	}
	ERichCurveInterpMode Interp = RCIM_Linear;
	TArray<TSharedPtr<FJsonValue>> Keys;
	TSharedPtr<FJsonObject> PerCurve;
	if (Spec->Type == EJson::Array) Keys = Spec->AsArray();
	else if (Spec->Type == EJson::Object)
	{
		const TSharedPtr<FJsonObject> O = Spec->AsObject();
		const TArray<TSharedPtr<FJsonValue>>* K = nullptr;
		if (O->TryGetArrayField(TEXT("keys"), K)) Keys = *K;
		else PerCurve = O;
		const FString I = Str(O, TEXT("interp")).ToLower();
		if (I == TEXT("cubic") || I == TEXT("auto")) Interp = RCIM_Cubic;
		else if (I == TEXT("constant") || I == TEXT("step")) Interp = RCIM_Constant;
	}
	DI->Modify();
	auto SetKeys = [&](FRichCurve& C, const TArray<TSharedPtr<FJsonValue>>& Ks, int32 Component) -> bool
	{
		C.Reset();
		for (const TSharedPtr<FJsonValue>& KV : Ks)
		{
			TArray<double> F;
			if (!ReadFloats(KV, F) || F.Num() < 2)
			{
				Err = TEXT("curve keys are [time, value, ...]");
				return false;
			}
			const double Val = F.IsValidIndex(1 + Component) ? F[1 + Component] : F.Last();
			const FKeyHandle H = C.AddKey((float)F[0], (float)Val);
			C.SetKeyInterpMode(H, Interp);
		}
		return true;
	};
	if (PerCurve.IsValid())
	{
		for (const auto& PairIt : PerCurve->Values)
		{
			const TPair<FString, TSharedPtr<FJsonValue>> Pair(FString(PairIt.Key), PairIt.Value);
			if (Pair.Key == TEXT("interp")) continue;
			FStructProperty* Found = nullptr;
			for (FStructProperty* P : Curves)
			{
				if (NiaNorm(P->GetName()) == NiaNorm(Pair.Key) || NiaNorm(P->GetName()) == NiaNorm(Pair.Key + TEXT("Curve"))) Found = P;
			}
			if (!Found || Pair.Value->Type != EJson::Array)
			{
				TArray<FString> Names;
				for (FStructProperty* P : Curves) Names.Add(P->GetName());
				Err = FString::Printf(TEXT("curve '%s' not on %s (%s)"), *Pair.Key, *DI->GetClass()->GetName(), *FString::Join(Names, TEXT(", ")));
				return false;
			}
			if (!SetKeys(*Found->ContainerPtrToValuePtr<FRichCurve>(DI), Pair.Value->AsArray(), 0)) return false;
		}
	}
	else
	{
		for (int32 I = 0; I < Curves.Num(); ++I)
		{
			if (!SetKeys(*Curves[I]->ContainerPtrToValuePtr<FRichCurve>(DI), Keys, I)) return false;
		}
	}
	DI->PostEditChange(); // curve DIs rebuild their LUT here
	return true;
}

TSharedPtr<FJsonObject> DataInterfaceJson(UObject* DI)
{
	TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
	if (!DI) return O;
	O->SetStringField(TEXT("class"), DI->GetClass()->GetName());
	TArray<FStructProperty*> Curves;
	CurveProps(DI, Curves);
	if (Curves.Num())
	{
		TSharedPtr<FJsonObject> C = MakeShared<FJsonObject>();
		for (FStructProperty* P : Curves) C->SetField(P->GetName(), CurveToJson(*P->ContainerPtrToValuePtr<FRichCurve>(DI)));
		O->SetObjectField(TEXT("curves"), C);
	}
	TSet<FName> Skip;
	for (FStructProperty* P : Curves) Skip.Add(P->GetFName());
	TSharedPtr<FJsonObject> Props = ChangedProperties(DI->GetClass(), DI, DI->GetClass()->GetDefaultObject(), Skip);
	if (Props->Values.Num()) O->SetObjectField(TEXT("properties"), Props);
	return O;
}

// ---------------------------------------------------------------------------------------------
// Context: one system view model per call
// ---------------------------------------------------------------------------------------------

struct FCtx
{
	UNiagaraSystem* System = nullptr;
	TSharedPtr<FNiagaraSystemViewModel> VM;

	explicit FCtx(UNiagaraSystem* InSystem) : System(InSystem) { Reset(); }
	~FCtx() { Release(); }

	void Reset()
	{
		Release();
		VM = MakeShared<FNiagaraSystemViewModel>();
		FNiagaraSystemViewModelOptions Options;
		Options.bCanModifyEmittersFromTimeline = false;
		Options.bCompileForEdit = false;
		Options.bCanSimulate = false;
		Options.bCanAutoCompile = false;
		Options.bIsForDataProcessingOnly = true;
		Options.MessageLogGuid = System->GetAssetGuid();
		VM->Initialize(*System, Options);
	}

	void Release()
	{
		VM.Reset();
	}

	TSharedPtr<FNiagaraEmitterHandleViewModel> EmitterVM(const FString& Name, FString& Err)
	{
		TArray<FString> Names;
		for (const TSharedRef<FNiagaraEmitterHandleViewModel>& H : VM->GetEmitterHandleViewModels())
		{
			if (H->GetName().ToString().Equals(Name, ESearchCase::IgnoreCase)) return H;
			Names.Add(H->GetName().ToString());
		}
		for (const TSharedRef<FNiagaraEmitterHandleViewModel>& H : VM->GetEmitterHandleViewModels())
		{
			if (NiaNorm(H->GetName().ToString()) == NiaNorm(Name)) return H;
		}
		Err = FString::Printf(TEXT("emitter '%s' not found (emitters: %s)"), *Name, Names.Num() ? *FString::Join(Names, TEXT(", ")) : TEXT("none"));
		return nullptr;
	}

	FNiagaraEmitterHandle* Handle(const FString& Name)
	{
		for (FNiagaraEmitterHandle& H : System->GetEmitterHandles())
		{
			if (H.GetName().ToString().Equals(Name, ESearchCase::IgnoreCase) || NiaNorm(H.GetName().ToString()) == NiaNorm(Name)) return &H;
		}
		return nullptr;
	}
};

struct FStageRef
{
	UNiagaraStackScriptItemGroup* Group = nullptr;
	ENiagaraScriptUsage Usage = ENiagaraScriptUsage::Module;
	FGuid UsageId;
	FString Key;   // system_spawn, particle_update, event:0, simulation_stage:Name
	TSharedPtr<FJsonObject> Info;
};

void CollectStackEntries(UNiagaraStackEntry* E, TArray<UNiagaraStackScriptItemGroup*>& Groups, TArray<UNiagaraStackRendererItem*>& Renderers, int32 Depth)
{
	if (!E || Depth > 4) return;
	TArray<UNiagaraStackEntry*> Children;
	E->GetUnfilteredChildren(Children);
	for (UNiagaraStackEntry* C : Children)
	{
		if (UNiagaraStackScriptItemGroup* G = Cast<UNiagaraStackScriptItemGroup>(C))
		{
			Groups.Add(G);
		}
		else if (UNiagaraStackRendererItem* R = Cast<UNiagaraStackRendererItem>(C))
		{
			Renderers.Add(R);
		}
		else if (!Cast<UNiagaraStackModuleItem>(C) && !Cast<UNiagaraStackFunctionInput>(C))
		{
			CollectStackEntries(C, Groups, Renderers, Depth + 1);
		}
	}
}

UNiagaraStackEntry* RootFor(FCtx& Ctx, const TSharedPtr<FNiagaraEmitterHandleViewModel>& EVM)
{
	UNiagaraStackViewModel* SVM = EVM.IsValid() ? EVM->GetEmitterStackViewModel() : Ctx.VM->GetSystemStackViewModel();
	return SVM ? SVM->GetRootEntry() : nullptr;
}

/** Stages of the system (EVM null) or of one emitter, in stack order. */
TArray<FStageRef> Stages(FCtx& Ctx, const TSharedPtr<FNiagaraEmitterHandleViewModel>& EVM, TArray<UNiagaraStackRendererItem*>* OutRenderers = nullptr)
{
	TArray<FStageRef> Out;
	UNiagaraStackEntry* Root = RootFor(Ctx, EVM);
	if (!Root) return Out;
	Root->RefreshChildren();
	TArray<UNiagaraStackScriptItemGroup*> Groups;
	TArray<UNiagaraStackRendererItem*> Renderers;
	CollectStackEntries(Root, Groups, Renderers, 0);
	if (OutRenderers) *OutRenderers = Renderers;
	FVersionedNiagaraEmitterData* Data = EVM.IsValid() && EVM->GetEmitterHandle() ? EVM->GetEmitterHandle()->GetEmitterData() : nullptr;
	TSet<FString> Seen;
	int32 EventIndex = 0;
	for (UNiagaraStackScriptItemGroup* G : Groups)
	{
		UNiagaraNodeOutput* OutNode = G->GetScriptOutputNode();
		if (!OutNode) continue;
		FStageRef S;
		S.Group = G;
		S.Usage = OutNode->GetUsage();
		S.UsageId = OutNode->GetUsageId();
		if (EVM.IsValid() == IsSystemUsage(S.Usage)) continue; // the emitter stack shows only emitter stages
		S.Key = StageKey(S.Usage);
		S.Info = MakeShared<FJsonObject>();
		if (S.Usage == ENiagaraScriptUsage::ParticleEventScript && Data)
		{
			for (const FNiagaraEventScriptProperties& EP : Data->GetEventHandlers())
			{
				if (EP.Script && EP.Script->GetUsageId() == S.UsageId)
				{
					S.Info->SetStringField(TEXT("event_name"), EP.SourceEventName.ToString());
					FString Source;
					for (const FNiagaraEmitterHandle& H : Ctx.System->GetEmitterHandles())
					{
						if (H.GetId() == EP.SourceEmitterID) Source = H.GetName().ToString();
					}
					S.Info->SetStringField(TEXT("source_emitter"), Source);
					const UEnum* ModeEnum = StaticEnum<EScriptExecutionMode>();
					S.Info->SetStringField(TEXT("execution_mode"), ModeEnum ? ModeEnum->GetNameStringByValue((int64)EP.ExecutionMode) : TEXT(""));
					S.Info->SetNumberField(TEXT("spawn_number"), EP.SpawnNumber);
					S.Info->SetNumberField(TEXT("max_events_per_frame"), EP.MaxEventsPerFrame);
					S.Info->SetBoolField(TEXT("random_spawn_number"), EP.bRandomSpawnNumber);
					S.Info->SetNumberField(TEXT("min_spawn_number"), EP.MinSpawnNumber);
				}
			}
			S.Key = FString::Printf(TEXT("event:%d"), EventIndex++);
		}
		else if (S.Usage == ENiagaraScriptUsage::ParticleSimulationStageScript && Data)
		{
			FString Name = S.UsageId.ToString();
			for (UNiagaraSimulationStageBase* SS : Data->GetSimulationStages())
			{
				if (SS && SS->Script && SS->Script->GetUsageId() == S.UsageId)
				{
					Name = SS->SimulationStageName.ToString();
					S.Info->SetStringField(TEXT("class"), SS->GetClass()->GetName());
					S.Info->SetBoolField(TEXT("enabled"), SS->bEnabled);
					TSharedPtr<FJsonObject> Props = ChangedProperties(SS->GetClass(), SS, SS->GetClass()->GetDefaultObject(), {});
					if (Props->Values.Num()) S.Info->SetObjectField(TEXT("properties"), Props);
				}
			}
			S.Key = TEXT("simulation_stage:") + Name;
		}
		const FString Dedup = S.Key + S.UsageId.ToString();
		if (Seen.Contains(Dedup)) continue;
		Seen.Add(Dedup);
		Out.Add(S);
	}
	return Out;
}

bool StageMatches(const FStageRef& S, const FString& Want)
{
	const FString W = Want.ToLower().Replace(TEXT(" "), TEXT("_"));
	if (S.Key.Equals(W, ESearchCase::IgnoreCase)) return true;
	const UEnum* UsageEnum = StaticEnum<ENiagaraScriptUsage>();
	if (UsageEnum && NiaNorm(UsageEnum->GetNameStringByValue((int64)S.Usage)) == NiaNorm(Want)) return true;
	if (S.Usage == ENiagaraScriptUsage::ParticleEventScript && S.Info.IsValid())
	{
		// "event:DeathEvent" or "event:<source emitter>"
		if (W.StartsWith(TEXT("event:")))
		{
			const FString Tail = NiaNorm(Want.Mid(6));
			return NiaNorm(Str(S.Info, TEXT("event_name"))) == Tail || NiaNorm(Str(S.Info, TEXT("source_emitter"))) == Tail;
		}
	}
	if (S.Key.StartsWith(TEXT("simulation_stage:")) && (W.StartsWith(TEXT("sim:")) || W.StartsWith(TEXT("simulation_stage:"))))
	{
		return NiaNorm(S.Key.Mid(17)) == NiaNorm(Want.Mid(Want.Find(TEXT(":")) + 1));
	}
	return false;
}

TArray<UNiagaraStackModuleItem*> Modules(const FStageRef& S)
{
	TArray<UNiagaraStackModuleItem*> Out;
	if (!S.Group) return Out;
	TArray<UNiagaraStackEntry*> Children;
	S.Group->GetUnfilteredChildren(Children);
	for (UNiagaraStackEntry* C : Children)
	{
		if (UNiagaraStackModuleItem* M = Cast<UNiagaraStackModuleItem>(C)) Out.Add(M);
	}
	Out.Sort([](const UNiagaraStackModuleItem& A, const UNiagaraStackModuleItem& B) { return A.GetModuleIndex() < B.GetModuleIndex(); });
	return Out;
}

FString ModuleName(UNiagaraStackModuleItem* M)
{
	return M ? M->GetModuleNode().GetFunctionName() : FString();
}

/** Inputs directly under E (through categories and static-switch children), not into dynamic-input chains. */
void CollectInputs(UNiagaraStackEntry* E, TArray<UNiagaraStackFunctionInput*>& Out, int32 Depth = 0)
{
	if (!E || Depth > 8) return;
	TArray<UNiagaraStackEntry*> Children;
	E->GetUnfilteredChildren(Children);
	for (UNiagaraStackEntry* C : Children)
	{
		if (UNiagaraStackFunctionInput* I = Cast<UNiagaraStackFunctionInput>(C))
		{
			Out.Add(I);
			if (I->GetValueMode() != UNiagaraStackFunctionInput::EValueMode::Dynamic) CollectInputs(I, Out, Depth + 1);
		}
		else if (!Cast<UNiagaraStackModuleItem>(C))
		{
			CollectInputs(C, Out, Depth + 1);
		}
	}
}

FString InputName(UNiagaraStackFunctionInput* I)
{
	return I->GetInputParameterHandle().GetName().ToString();
}

bool InputVisible(UNiagaraStackFunctionInput* I)
{
	return !I->GetIsHidden() && (!I->GetHasVisibleCondition() || I->GetVisibleConditionEnabled());
}

UNiagaraStackFunctionInput* FindInput(UNiagaraStackEntry* Parent, const FString& Name, TArray<FString>* OutNames = nullptr)
{
	TArray<UNiagaraStackFunctionInput*> Inputs;
	CollectInputs(Parent, Inputs);
	const FString N = NiaNorm(Name);
	UNiagaraStackFunctionInput* Hidden = nullptr;
	for (int32 Pass = 0; Pass < 2; ++Pass)
	{
		for (UNiagaraStackFunctionInput* I : Inputs)
		{
			const bool bMatch = Pass == 0 ? InputName(I).Equals(Name, ESearchCase::IgnoreCase)
				: (NiaNorm(InputName(I)) == N || NiaNorm(I->GetDisplayName().ToString()) == N);
			if (!bMatch) continue;
			if (InputVisible(I)) return I;
			if (!Hidden) Hidden = I;
		}
	}
	if (Hidden) return Hidden;
	if (OutNames)
	{
		for (UNiagaraStackFunctionInput* I : Inputs)
		{
			if (InputVisible(I)) OutNames->AddUnique(InputName(I));
		}
	}
	return nullptr;
}

TSharedPtr<FJsonObject> ExportInput(UNiagaraStackFunctionInput* I, bool bIncludeHidden, bool bOnlyModified, int32 Depth);

TArray<TSharedPtr<FJsonValue>> ExportInputs(UNiagaraStackEntry* Parent, bool bIncludeHidden, bool bOnlyModified, int32 Depth)
{
	TArray<TSharedPtr<FJsonValue>> Out;
	TArray<UNiagaraStackFunctionInput*> Inputs;
	CollectInputs(Parent, Inputs);
	TSet<FString> Seen;
	for (UNiagaraStackFunctionInput* I : Inputs)
	{
		const bool bVisible = InputVisible(I);
		if (!bVisible && !bIncludeHidden) continue;
		if (bOnlyModified && !I->CanReset() && I->GetValueMode() != UNiagaraStackFunctionInput::EValueMode::Dynamic) continue;
		if (Seen.Contains(InputName(I))) continue;
		Seen.Add(InputName(I));
		if (TSharedPtr<FJsonObject> J = ExportInput(I, bIncludeHidden, bOnlyModified, Depth)) Out.Add(MakeShared<FJsonValueObject>(J));
	}
	return Out;
}

TSharedPtr<FJsonObject> ExportInput(UNiagaraStackFunctionInput* I, bool bIncludeHidden, bool bOnlyModified, int32 Depth)
{
	using E = UNiagaraStackFunctionInput::EValueMode;
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("name"), InputName(I));
	const FString Display = I->GetDisplayName().ToString();
	if (NiaNorm(Display) != NiaNorm(InputName(I))) J->SetStringField(TEXT("display"), Display);
	const FNiagaraTypeDefinition& Type = I->GetInputType();
	J->SetStringField(TEXT("type"), TypeName(Type));
	if (UEnum* En = Type.GetEnum())
	{
		TArray<FString> Options;
		for (int32 Idx = 0; Idx < En->NumEnums() - 1; ++Idx)
		{
			if (!En->HasMetaData(TEXT("Hidden"), Idx)) Options.Add(EnumValueName(En, En->GetValueByIndex(Idx)));
		}
		J->SetField(TEXT("options"), StringArray(Options));
	}
	const E Mode = I->GetValueMode();
	J->SetStringField(TEXT("mode"), ModeName(Mode));
	if (I->IsStaticParameter()) J->SetBoolField(TEXT("static_switch"), true);
	if (!InputVisible(I)) J->SetBoolField(TEXT("hidden"), true);
	if (I->GetHasEditCondition() && !I->GetEditConditionEnabled()) J->SetBoolField(TEXT("edit_condition_off"), true);
	J->SetBoolField(TEXT("modified"), I->CanReset());
	switch (Mode)
	{
	case E::Local:
	{
		TSharedPtr<const FStructOnScope> L = I->GetLocalValueStruct();
		if (L.IsValid() && L->IsValid()) J->SetField(TEXT("value"), ValueToJson(Type, L->GetStructMemory()));
		break;
	}
	case E::Linked:
		J->SetStringField(TEXT("link"), I->GetLinkedParameterValue().GetName().ToString());
		break;
	case E::Dynamic:
	{
		TSharedPtr<FJsonObject> D = MakeShared<FJsonObject>();
		if (UNiagaraNodeFunctionCall* N = I->GetDynamicInputNode())
		{
			D->SetStringField(TEXT("script"), N->FunctionScript ? N->FunctionScript->GetPathName() : FString());
			D->SetStringField(TEXT("name"), N->GetFunctionName());
		}
		if (Depth < 6) D->SetArrayField(TEXT("inputs"), ExportInputs(I, bIncludeHidden, bOnlyModified, Depth + 1));
		J->SetObjectField(TEXT("dynamic"), D);
		break;
	}
	case E::Data:
		J->SetObjectField(TEXT("data"), DataInterfaceJson(I->GetDataValueObject()));
		break;
	case E::ObjectAsset:
	{
		UObject* O = I->GetObjectAssetValue();
		J->SetField(TEXT("object"), O ? TSharedPtr<FJsonValue>(MakeShared<FJsonValueString>(O->GetPathName())) : TSharedPtr<FJsonValue>(MakeShared<FJsonValueNull>()));
		break;
	}
	case E::Expression:
		J->SetStringField(TEXT("expression"), I->GetCustomExpressionText().ToString());
		break;
	case E::DefaultFunction:
		if (UNiagaraNodeFunctionCall* N = I->GetDefaultFunctionNode())
		{
			J->SetStringField(TEXT("default_function"), N->FunctionScript ? N->FunctionScript->GetPathName() : N->GetFunctionName());
		}
		break;
	default:
		break;
	}
	return J;
}

FString RendererKind(const UNiagaraRendererProperties* R)
{
	if (Cast<UNiagaraSpriteRendererProperties>(R)) return TEXT("sprite");
	if (Cast<UNiagaraMeshRendererProperties>(R)) return TEXT("mesh");
	if (Cast<UNiagaraRibbonRendererProperties>(R)) return TEXT("ribbon");
	if (Cast<UNiagaraLightRendererProperties>(R)) return TEXT("light");
	FString N = R ? R->GetClass()->GetName() : FString();
	N.RemoveFromStart(TEXT("Niagara"));
	N.RemoveFromEnd(TEXT("RendererProperties"));
	return N.ToLower();
}

TSharedPtr<FJsonObject> ExportRenderer(UNiagaraRendererProperties* R, int32 Index)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetNumberField(TEXT("index"), Index);
	J->SetStringField(TEXT("type"), RendererKind(R));
	J->SetStringField(TEXT("class"), R->GetClass()->GetName());
	J->SetBoolField(TEXT("enabled"), R->GetIsEnabled());
	TArray<UMaterialInterface*> Materials;
	R->GetUsedMaterials(nullptr, Materials);
	TArray<FString> MatPaths;
	for (UMaterialInterface* M : Materials) MatPaths.Add(M ? M->GetPathName() : TEXT("None"));
	J->SetField(TEXT("materials"), StringArray(MatPaths));
	if (UNiagaraMeshRendererProperties* MR = Cast<UNiagaraMeshRendererProperties>(R))
	{
		TArray<FString> Meshes;
		for (const FNiagaraMeshRendererMeshProperties& MP : MR->Meshes) Meshes.Add(MP.Mesh ? MP.Mesh->GetPathName() : TEXT("None"));
		J->SetField(TEXT("meshes"), StringArray(Meshes));
	}
	TSharedPtr<FJsonObject> Bindings = MakeShared<FJsonObject>();
	for (TFieldIterator<FStructProperty> It(R->GetClass()); It; ++It)
	{
		if (It->Struct && It->Struct->GetName() == TEXT("NiagaraVariableAttributeBinding"))
		{
			const FNiagaraVariableAttributeBinding* B = It->ContainerPtrToValuePtr<FNiagaraVariableAttributeBinding>(R);
			Bindings->SetStringField(It->GetName(), B->GetParamMapBindableVariable().GetName().ToString());
		}
	}
	J->SetObjectField(TEXT("bindings"), Bindings);
	TSet<FName> Skip = {FName(TEXT("Meshes")), FName(TEXT("Platforms"))};
	TSharedPtr<FJsonObject> Props = ChangedProperties(R->GetClass(), R, R->GetClass()->GetDefaultObject(), Skip);
	J->SetObjectField(TEXT("properties"), Props);
	return J;
}

UScriptStruct* EmitterDataStruct()
{
	static UScriptStruct* S = FindObject<UScriptStruct>(nullptr, TEXT("/Script/Niagara.VersionedNiagaraEmitterData"));
	return S;
}

TSharedPtr<FJsonObject> ExportEmitter(FCtx& Ctx, const TSharedRef<FNiagaraEmitterHandleViewModel>& EVM, int32 Index, bool bIncludeHidden, bool bOnlyModified)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	FNiagaraEmitterHandle* H = EVM->GetEmitterHandle();
	J->SetStringField(TEXT("name"), EVM->GetName().ToString());
	J->SetNumberField(TEXT("index"), Index);
	J->SetBoolField(TEXT("enabled"), H && H->GetIsEnabled());
	FVersionedNiagaraEmitterData* D = H ? H->GetEmitterData() : nullptr;
	if (D)
	{
		J->SetStringField(TEXT("sim_target"), D->SimTarget == ENiagaraSimTarget::GPUComputeSim ? TEXT("gpu") : TEXT("cpu"));
		J->SetBoolField(TEXT("local_space"), D->bLocalSpace);
		J->SetBoolField(TEXT("determinism"), D->bDeterminism);
		J->SetNumberField(TEXT("random_seed"), D->RandomSeed);
		const UEnum* BoundsEnum = StaticEnum<ENiagaraEmitterCalculateBoundMode>();
		TSharedPtr<FJsonObject> Bounds = MakeShared<FJsonObject>();
		Bounds->SetStringField(TEXT("mode"), BoundsEnum ? BoundsEnum->GetNameStringByValue((int64)D->CalculateBoundsMode).ToLower() : TEXT(""));
		if (D->CalculateBoundsMode == ENiagaraEmitterCalculateBoundMode::Fixed)
		{
			Bounds->SetObjectField(TEXT("fixed"), BoxJson(D->FixedBounds));
		}
		J->SetObjectField(TEXT("bounds"), Bounds);
		const FVersionedNiagaraEmitter Parent = D->GetParent();
		J->SetField(TEXT("parent"), Parent.Emitter ? TSharedPtr<FJsonValue>(MakeShared<FJsonValueString>(Parent.Emitter->GetPathName())) : TSharedPtr<FJsonValue>(MakeShared<FJsonValueNull>()));
		if (UScriptStruct* S = EmitterDataStruct())
		{
			TArray<uint8> Defaults;
			Defaults.SetNumZeroed(S->GetStructureSize());
			S->InitializeStruct(Defaults.GetData());
			TSet<FName> Skip = {FName(TEXT("SimTarget")), FName(TEXT("bLocalSpace")), FName(TEXT("bDeterminism")), FName(TEXT("RandomSeed")),
				FName(TEXT("CalculateBoundsMode")), FName(TEXT("FixedBounds")), FName(TEXT("Platforms")), FName(TEXT("ScalabilityOverrides")),
				FName(TEXT("EventHandlerScriptProps")), FName(TEXT("SimulationStages"))};
			J->SetObjectField(TEXT("properties"), ChangedProperties(S, D, Defaults.GetData(), Skip));
			S->DestroyStruct(Defaults.GetData());
		}
	}
	TArray<UNiagaraStackRendererItem*> RendererItems;
	TArray<TSharedPtr<FJsonValue>> StagesJ;
	TSharedPtr<FNiagaraEmitterHandleViewModel> EVMPtr = EVM;
	for (const FStageRef& S : Stages(Ctx, EVMPtr, &RendererItems))
	{
		TSharedPtr<FJsonObject> SJ = MakeShared<FJsonObject>();
		SJ->SetStringField(TEXT("stage"), S.Key);
		for (const auto& Pair : S.Info->Values) SJ->SetField(FString(Pair.Key), Pair.Value);
		TArray<TSharedPtr<FJsonValue>> ModsJ;
		for (UNiagaraStackModuleItem* M : Modules(S))
		{
			TSharedPtr<FJsonObject> MJ = MakeShared<FJsonObject>();
			MJ->SetStringField(TEXT("name"), ModuleName(M));
			MJ->SetNumberField(TEXT("index"), M->GetModuleIndex());
			UNiagaraScript* Script = M->GetModuleNode().FunctionScript;
			MJ->SetStringField(TEXT("script"), Script ? Script->GetPathName() : FString());
			if (!Script) MJ->SetBoolField(TEXT("set_parameters"), true);
			MJ->SetBoolField(TEXT("enabled"), M->GetIsEnabled());
			MJ->SetArrayField(TEXT("inputs"), ExportInputs(M, bIncludeHidden, bOnlyModified, 0));
			ModsJ.Add(MakeShared<FJsonValueObject>(MJ));
		}
		SJ->SetArrayField(TEXT("modules"), ModsJ);
		StagesJ.Add(MakeShared<FJsonValueObject>(SJ));
	}
	J->SetArrayField(TEXT("stages"), StagesJ);
	TArray<TSharedPtr<FJsonValue>> RJ;
	if (D)
	{
		const TArray<UNiagaraRendererProperties*>& Renderers = D->GetRenderers();
		for (int32 I = 0; I < Renderers.Num(); ++I)
		{
			if (Renderers[I]) RJ.Add(MakeShared<FJsonValueObject>(ExportRenderer(Renderers[I], I)));
		}
	}
	J->SetArrayField(TEXT("renderers"), RJ);
	return J;
}

TArray<TSharedPtr<FJsonValue>> ExportUserParameters(UNiagaraSystem* System)
{
	TArray<TSharedPtr<FJsonValue>> Out;
	FNiagaraUserRedirectionParameterStore& Store = System->GetExposedParameters();
	for (const FNiagaraVariableWithOffset& V : Store.ReadParameterVariables())
	{
		TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
		J->SetStringField(TEXT("name"), V.GetName().ToString());
		J->SetStringField(TEXT("type"), TypeName(V.GetType()));
		if (V.GetType().IsDataInterface())
		{
			J->SetObjectField(TEXT("data"), DataInterfaceJson(Store.GetDataInterface(V.Offset)));
		}
		else if (V.GetType().IsUObject())
		{
			UObject* O = Store.GetUObject(V.Offset);
			J->SetField(TEXT("value"), O ? TSharedPtr<FJsonValue>(MakeShared<FJsonValueString>(O->GetPathName())) : TSharedPtr<FJsonValue>(MakeShared<FJsonValueNull>()));
		}
		else
		{
			J->SetField(TEXT("value"), ValueToJson(V.GetType(), Store.GetParameterData(V.Offset, V.GetType())));
		}
		Out.Add(MakeShared<FJsonValueObject>(J));
	}
	return Out;
}

TSharedPtr<FJsonObject> ExportSystem(FCtx& Ctx, bool bIncludeHidden, bool bOnlyModified, const TArray<FString>& OnlyEmitters)
{
	UNiagaraSystem* System = Ctx.System;
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("system"), System->GetPathName());
	J->SetStringField(TEXT("name"), System->GetName());
	J->SetNumberField(TEXT("warmup_time"), System->GetWarmupTime());
	J->SetBoolField(TEXT("fixed_bounds_enabled"), System->bFixedBounds != 0);
	if (System->bFixedBounds) J->SetObjectField(TEXT("fixed_bounds"), BoxJson(System->GetFixedBounds()));
	J->SetArrayField(TEXT("user_parameters"), ExportUserParameters(System));
	TArray<TSharedPtr<FJsonValue>> SysStages;
	for (const FStageRef& S : Stages(Ctx, nullptr))
	{
		TSharedPtr<FJsonObject> SJ = MakeShared<FJsonObject>();
		SJ->SetStringField(TEXT("stage"), S.Key);
		TArray<TSharedPtr<FJsonValue>> ModsJ;
		for (UNiagaraStackModuleItem* M : Modules(S))
		{
			TSharedPtr<FJsonObject> MJ = MakeShared<FJsonObject>();
			MJ->SetStringField(TEXT("name"), ModuleName(M));
			MJ->SetNumberField(TEXT("index"), M->GetModuleIndex());
			UNiagaraScript* Script = M->GetModuleNode().FunctionScript;
			MJ->SetStringField(TEXT("script"), Script ? Script->GetPathName() : FString());
			MJ->SetBoolField(TEXT("enabled"), M->GetIsEnabled());
			MJ->SetArrayField(TEXT("inputs"), ExportInputs(M, bIncludeHidden, bOnlyModified, 0));
			ModsJ.Add(MakeShared<FJsonValueObject>(MJ));
		}
		SJ->SetArrayField(TEXT("modules"), ModsJ);
		SysStages.Add(MakeShared<FJsonValueObject>(SJ));
	}
	J->SetArrayField(TEXT("system_stages"), SysStages);
	TArray<TSharedPtr<FJsonValue>> Emitters;
	int32 Index = 0;
	for (const TSharedRef<FNiagaraEmitterHandleViewModel>& EVM : Ctx.VM->GetEmitterHandleViewModels())
	{
		const int32 This = Index++;
		if (OnlyEmitters.Num() && !OnlyEmitters.ContainsByPredicate([&](const FString& N) { return NiaNorm(N) == NiaNorm(EVM->GetName().ToString()); }))
		{
			continue;
		}
		Emitters.Add(MakeShared<FJsonValueObject>(ExportEmitter(Ctx, EVM, This, bIncludeHidden, bOnlyModified)));
	}
	J->SetArrayField(TEXT("emitters"), Emitters);
	return J;
}

// ---------------------------------------------------------------------------------------------
// Compile
// ---------------------------------------------------------------------------------------------

FString CompileStatusName(ENiagaraExt_ScriptCompileStatus S)
{
	const UEnum* E = StaticEnum<ENiagaraExt_ScriptCompileStatus>();
	return E ? E->GetNameStringByValue((int64)S) : TEXT("Unknown");
}

TSharedPtr<FJsonObject> CompileSystem(UNiagaraSystem* System, bool bWaitGpu, bool bIssues)
{
	System->RequestCompile(false);
	System->WaitForCompilationComplete(bWaitGpu, false);
	FNiagaraExternalEditContext EC(System);
	FNiagaraExt_SystemCompileState State;
	UNiagaraExternalEditUtilities::GetSystemCompileState(System, State, EC);
	TArray<TSharedPtr<FJsonValue>> Errors, Warnings, Scripts;
	for (const FNiagaraExt_ScriptCompileInfo& S : State.Scripts)
	{
		TSharedPtr<FJsonObject> SJ = MakeShared<FJsonObject>();
		SJ->SetStringField(TEXT("emitter"), S.EmitterName.IsNone() ? FString() : S.EmitterName.ToString());
		SJ->SetStringField(TEXT("script"), S.ScriptName.ToString());
		SJ->SetStringField(TEXT("status"), CompileStatusName(S.LastCompileStatus));
		Scripts.Add(MakeShared<FJsonValueObject>(SJ));
		for (const FNiagaraExt_CompileEvent& Ev : S.CompileEvents)
		{
			if (Ev.Severity != ENiagaraExt_CompileEventSeverity::Error && Ev.Severity != ENiagaraExt_CompileEventSeverity::Warning) continue;
			TSharedPtr<FJsonObject> EJ = MakeShared<FJsonObject>();
			EJ->SetStringField(TEXT("emitter"), S.EmitterName.IsNone() ? FString() : S.EmitterName.ToString());
			EJ->SetStringField(TEXT("script"), S.ScriptName.ToString());
			EJ->SetStringField(TEXT("message"), Ev.Message);
			if (!Ev.ShortDescription.IsEmpty()) EJ->SetStringField(TEXT("short"), Ev.ShortDescription);
			if (Ev.NodeGuid.IsValid()) EJ->SetStringField(TEXT("node"), Ev.NodeGuid.ToString());
			if (Ev.bFromScriptDependency) EJ->SetBoolField(TEXT("from_dependency"), true);
			(Ev.Severity == ENiagaraExt_CompileEventSeverity::Error ? Errors : Warnings).Add(MakeShared<FJsonValueObject>(EJ));
		}
		if (S.LastCompileStatus == ENiagaraExt_ScriptCompileStatus::Error && S.CompileEvents.Num() == 0 && !S.ErrorSummary.IsEmpty())
		{
			TSharedPtr<FJsonObject> EJ = MakeShared<FJsonObject>();
			EJ->SetStringField(TEXT("emitter"), S.EmitterName.ToString());
			EJ->SetStringField(TEXT("script"), S.ScriptName.ToString());
			EJ->SetStringField(TEXT("message"), S.ErrorSummary);
			Errors.Add(MakeShared<FJsonValueObject>(EJ));
		}
	}
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	const bool bOk = !State.bHasErrors && Errors.Num() == 0;
	Out->SetBoolField(TEXT("ok"), bOk);
	Out->SetStringField(TEXT("status"), bOk ? (Warnings.Num() ? TEXT("ok_with_warnings") : TEXT("ok")) : TEXT("error"));
	Out->SetStringField(TEXT("aggregate"), CompileStatusName(State.AggregateStatus));
	Out->SetArrayField(TEXT("errors"), Errors);
	Out->SetArrayField(TEXT("warnings"), Warnings);
	Out->SetArrayField(TEXT("scripts"), Scripts);
	for (const FText& T : EC.Errors) Errors.Add(MakeShared<FJsonValueString>(T.ToString()));
	if (bIssues)
	{
		FNiagaraExt_StackIssues Issues;
		UNiagaraExternalEditUtilities::GetStackIssues(System, Issues, EC);
		TArray<TSharedPtr<FJsonValue>> IssuesJ;
		for (const FNiagaraExt_StackIssue& I : Issues.Issues)
		{
			if (I.bIsDismissed || (I.Severity != ENiagaraExt_StackIssueSeverity::Error && I.Severity != ENiagaraExt_StackIssueSeverity::Warning)) continue;
			TSharedPtr<FJsonObject> IJ = MakeShared<FJsonObject>();
			IJ->SetStringField(TEXT("severity"), I.Severity == ENiagaraExt_StackIssueSeverity::Error ? TEXT("error") : TEXT("warning"));
			IJ->SetStringField(TEXT("where"), I.StackDisplayPath);
			IJ->SetStringField(TEXT("message"), I.ShortDescription + (I.LongDescription.IsEmpty() ? FString() : TEXT(": ") + I.LongDescription));
			TArray<FString> Fixes;
			for (const FNiagaraExt_StackIssueFix& F : I.Fixes) Fixes.Add(F.Description);
			if (Fixes.Num()) IJ->SetField(TEXT("fixes"), StringArray(Fixes));
			IssuesJ.Add(MakeShared<FJsonValueObject>(IJ));
		}
		Out->SetArrayField(TEXT("issues"), IssuesJ);
	}
	return Out;
}

// ---------------------------------------------------------------------------------------------
// Asset lookup with friendly names
// ---------------------------------------------------------------------------------------------

/** A Niagara script (module or dynamic input) by path, or by asset name ("Collision", "UniformRangedFloat"). */
UNiagaraScript* FindScript(const FString& NameOrPath, ENiagaraScriptUsage Usage, FString& Err)
{
	if (NameOrPath.StartsWith(TEXT("/")))
	{
		if (UNiagaraScript* S = LoadAs<UNiagaraScript>(NameOrPath)) return S;
		Err = FString::Printf(TEXT("Niagara script '%s' not found"), *NameOrPath);
		return nullptr;
	}
	IAssetRegistry& AR = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();
	TArray<FAssetData> Assets;
	AR.GetAssetsByClass(UNiagaraScript::StaticClass()->GetClassPathName(), Assets);
	const FString Want = NiaNorm(NameOrPath);
	TArray<FAssetData> Hits;
	for (const FAssetData& A : Assets)
	{
		if (NiaNorm(A.AssetName.ToString()) != Want) continue;
		Hits.Add(A);
	}
	// Prefer /Niagara content, then newest version folders (V3 > V2 > none), then the requested usage.
	Hits.Sort([](const FAssetData& A, const FAssetData& B)
	{
		auto Score = [](const FAssetData& D)
		{
			const FString P = D.PackageName.ToString();
			int32 S = P.StartsWith(TEXT("/Niagara/")) ? 100 : 0;
			if (P.Contains(TEXT("/V3/"))) S += 3;
			else if (P.Contains(TEXT("/V2/"))) S += 2;
			if (P.Contains(TEXT("Deprecated")) || P.Contains(TEXT("CascadeConversion"))) S -= 50;
			return S;
		};
		return Score(A) > Score(B);
	});
	for (const FAssetData& A : Hits)
	{
		if (UNiagaraScript* S = Cast<UNiagaraScript>(A.GetAsset()))
		{
			if (Usage == ENiagaraScriptUsage::Module || S->GetUsage() == Usage) return S;
		}
	}
	for (const FAssetData& A : Hits)
	{
		if (UNiagaraScript* S = Cast<UNiagaraScript>(A.GetAsset())) return S;
	}
	Err = FString::Printf(TEXT("no Niagara script named '%s' (give a path like /Niagara/Modules/Update/Color/Color)"), *NameOrPath);
	return nullptr;
}

UNiagaraEmitter* FindEmitterAsset(const FString& NameOrPath, FString& Err)
{
	if (NameOrPath.StartsWith(TEXT("/")))
	{
		if (UNiagaraEmitter* E = LoadAs<UNiagaraEmitter>(NameOrPath)) return E;
		Err = FString::Printf(TEXT("Niagara emitter '%s' not found"), *NameOrPath);
		return nullptr;
	}
	const FString Template = FString::Printf(TEXT("/Niagara/DefaultAssets/Templates/Emitters/%s"), *NameOrPath);
	if (UNiagaraEmitter* E = LoadAs<UNiagaraEmitter>(Template)) return E;
	IAssetRegistry& AR = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();
	TArray<FAssetData> Assets;
	AR.GetAssetsByClass(UNiagaraEmitter::StaticClass()->GetClassPathName(), Assets);
	for (const FAssetData& A : Assets)
	{
		if (NiaNorm(A.AssetName.ToString()) == NiaNorm(NameOrPath)) return Cast<UNiagaraEmitter>(A.GetAsset());
	}
	Err = FString::Printf(TEXT("no emitter or emitter template named '%s' (templates: Fountain, OmnidirectionalBurst, DirectionalBurst, "
		"SimpleSpriteBurst, ConfettiBurst, UpwardMeshBurst, LocationBasedRibbon, StaticBeam, DynamicBeam, HangingParticulates, "
		"BlowingParticles, SingleLoopingParticle, RecycleParticlesInView, Minimal)"), *NameOrPath);
	return nullptr;
}

UClass* RendererClass(const FString& Type)
{
	const FString T = NiaNorm(Type);
	if (T == TEXT("sprite")) return UNiagaraSpriteRendererProperties::StaticClass();
	if (T == TEXT("mesh")) return UNiagaraMeshRendererProperties::StaticClass();
	if (T == TEXT("ribbon")) return UNiagaraRibbonRendererProperties::StaticClass();
	if (T == TEXT("light")) return UNiagaraLightRendererProperties::StaticClass();
	UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(Type);
	if (!C) C = FEpicUnrealMCPReflectionCommands::ResolveClass(FString::Printf(TEXT("Niagara%sRendererProperties"), *Type));
	return C && C->IsChildOf(UNiagaraRendererProperties::StaticClass()) ? C : nullptr;
}

// ---------------------------------------------------------------------------------------------
// Apply: setting module inputs
// ---------------------------------------------------------------------------------------------

struct FModuleRef
{
	TSharedPtr<FNiagaraEmitterHandleViewModel> EVM; // null = system stages
	FString Stage;
	FString Module;    // name, or "" with Index
	int32 Index = INDEX_NONE;
};

bool ParseModuleRef(FCtx& Ctx, const TSharedPtr<FJsonObject>& Op, FModuleRef& Out, FString& Err, bool bNeedModule = true)
{
	const FString Emitter = Str(Op, TEXT("emitter"));
	if (!Emitter.IsEmpty())
	{
		Out.EVM = Ctx.EmitterVM(Emitter, Err);
		if (!Out.EVM.IsValid()) return false;
	}
	Out.Stage = Str(Op, TEXT("stage"));
	if (Out.Stage.IsEmpty())
	{
		Err = TEXT("missing \"stage\" (system_spawn, system_update, emitter_spawn, emitter_update, particle_spawn, particle_update, event:<n>)");
		return false;
	}
	const TSharedPtr<FJsonValue> M = Op->TryGetField(TEXT("module"));
	if (M.IsValid() && M->Type == EJson::Number) Out.Index = (int32)M->AsNumber();
	else if (M.IsValid() && M->Type == EJson::String) Out.Module = M->AsString();
	double Idx = 0;
	if (Op->TryGetNumberField(TEXT("module_index"), Idx)) Out.Index = (int32)Idx;
	if (bNeedModule && Out.Module.IsEmpty() && Out.Index == INDEX_NONE)
	{
		Err = TEXT("missing \"module\" (its name from niagara_export, or an index)");
		return false;
	}
	return true;
}

bool ResolveStage(FCtx& Ctx, const FModuleRef& Ref, FStageRef& Out, FString& Err)
{
	const TArray<FStageRef> All = Stages(Ctx, Ref.EVM);
	for (const FStageRef& S : All)
	{
		if (StageMatches(S, Ref.Stage))
		{
			Out = S;
			return true;
		}
	}
	TArray<FString> Keys;
	for (const FStageRef& S : All) Keys.Add(S.Key);
	Err = FString::Printf(TEXT("stage '%s' not found on %s (stages: %s)%s"), *Ref.Stage,
		Ref.EVM.IsValid() ? *FString::Printf(TEXT("emitter '%s'"), *Ref.EVM->GetName().ToString()) : TEXT("the system"),
		*FString::Join(Keys, TEXT(", ")),
		!Ref.EVM.IsValid() && !Ref.Stage.StartsWith(TEXT("system")) ? TEXT(" - pass \"emitter\" for emitter and particle stages") : TEXT(""));
	return false;
}

UNiagaraStackModuleItem* ResolveModule(FCtx& Ctx, const FModuleRef& Ref, FString& Err, FStageRef* OutStage = nullptr)
{
	FStageRef S;
	if (!ResolveStage(Ctx, Ref, S, Err)) return nullptr;
	if (OutStage) *OutStage = S;
	TArray<UNiagaraStackModuleItem*> Mods = Modules(S);
	if (Ref.Module.IsEmpty())
	{
		for (UNiagaraStackModuleItem* M : Mods)
		{
			if (M->GetModuleIndex() == Ref.Index) return M;
		}
		Err = FString::Printf(TEXT("stage %s has no module at index %d (%d modules)"), *S.Key, Ref.Index, Mods.Num());
		return nullptr;
	}
	TArray<FString> Names;
	for (int32 Pass = 0; Pass < 3; ++Pass)
	{
		for (UNiagaraStackModuleItem* M : Mods)
		{
			const FString N = ModuleName(M);
			UNiagaraScript* Script = M->GetModuleNode().FunctionScript;
			bool bHit = false;
			if (Pass == 0) bHit = N.Equals(Ref.Module, ESearchCase::IgnoreCase);
			else if (Pass == 1) bHit = NiaNorm(N) == NiaNorm(Ref.Module) || NiaNorm(M->GetDisplayName().ToString()) == NiaNorm(Ref.Module);
			else bHit = Script && (NiaNorm(Script->GetName()) == NiaNorm(Ref.Module) || Script->GetPathName().StartsWith(Ref.Module));
			if (bHit) return M;
			if (Pass == 0) Names.Add(N);
		}
	}
	Err = FString::Printf(TEXT("module '%s' not in %s (modules: %s)"), *Ref.Module, *S.Key, Names.Num() ? *FString::Join(Names, TEXT(", ")) : TEXT("none"));
	return nullptr;
}

/** Input at Path ("Velocity" or "Velocity.Minimum" for a dynamic input's child) under a fresh module lookup. */
UNiagaraStackFunctionInput* ResolveInputPath(FCtx& Ctx, const FModuleRef& Ref, const TArray<FString>& Path, FString& Err)
{
	UNiagaraStackModuleItem* M = ResolveModule(Ctx, Ref, Err);
	if (!M) return nullptr;
	M->RefreshChildren();
	UNiagaraStackEntry* Parent = M;
	UNiagaraStackFunctionInput* Found = nullptr;
	for (int32 I = 0; I < Path.Num(); ++I)
	{
		if (Found && Found->GetValueMode() != UNiagaraStackFunctionInput::EValueMode::Dynamic)
		{
			Err = FString::Printf(TEXT("input '%s' is not a dynamic input, so '%s' has no child '%s'"), *InputName(Found), *InputName(Found), *Path[I]);
			return nullptr;
		}
		TArray<FString> Names;
		Found = FindInput(Parent, Path[I], &Names);
		if (!Found)
		{
			Err = FString::Printf(TEXT("input '%s' not found on %s (inputs: %s)"), *Path[I],
				I == 0 ? *FString::Printf(TEXT("module %s"), *ModuleName(M)) : *FString::Printf(TEXT("dynamic input %s"), *Path[I - 1]),
				*FString::Join(Names, TEXT(", ")));
			return nullptr;
		}
		Parent = Found;
	}
	return Found;
}

UNiagaraScript* CurveScriptFor(const FNiagaraTypeDefinition& T)
{
	FString Name;
	const FString K = Kind(T);
	if (K == TEXT("float")) Name = TEXT("FloatFromCurve");
	else if (K == TEXT("color")) Name = TEXT("ColorFromCurve");
	else if (K == TEXT("vec3") || K == TEXT("position")) Name = TEXT("VectorFromCurve");
	else if (K == TEXT("vec2")) Name = TEXT("Vector2DFromCurve");
	else if (K == TEXT("vec4")) Name = TEXT("Vector4FromCurve");
	if (Name.IsEmpty()) return nullptr;
	return LoadAs<UNiagaraScript>(FString::Printf(TEXT("/Niagara/DynamicInputs/ValueFromCurve/%s"), *Name));
}

UNiagaraScript* RandomScriptFor(const FNiagaraTypeDefinition& T)
{
	FString Name;
	const FString K = Kind(T);
	if (K == TEXT("float")) Name = TEXT("UniformRangedFloat");
	else if (K == TEXT("int")) Name = TEXT("UniformRangedInt");
	else if (K == TEXT("color")) Name = TEXT("UniformRangedLinearColor");
	else if (K == TEXT("vec3") || K == TEXT("position")) Name = TEXT("UniformRangedVector");
	else if (K == TEXT("vec2")) Name = TEXT("UniformRangedVector2D");
	else if (K == TEXT("vec4")) Name = TEXT("UniformRangedVector4");
	if (Name.IsEmpty()) return nullptr;
	return LoadAs<UNiagaraScript>(FString::Printf(TEXT("/Niagara/DynamicInputs/UniformRange/%s"), *Name));
}

bool SetInputPath(FCtx& Ctx, const FModuleRef& Ref, TArray<FString> Path, const TSharedPtr<FJsonValue>& Spec, FString& Err, int32 Depth = 0);

bool SetLocal(UNiagaraStackFunctionInput* In, const TSharedPtr<FJsonValue>& V, FString& Err)
{
	const FNiagaraTypeDefinition& T = In->GetInputType();
	if (T.IsDataInterface())
	{
		Err = FString::Printf(TEXT("input '%s' is a data interface (%s): set \"curve\" or \"data\""), *InputName(In), *TypeName(T));
		return false;
	}
	if (T.IsUObject() && V.IsValid() && V->Type == EJson::String)
	{
		UObject* Asset = LoadAs<UObject>(V->AsString());
		if (!Asset)
		{
			Err = FString::Printf(TEXT("asset '%s' not found"), *V->AsString());
			return false;
		}
		In->SetObjectAssetValue(Asset);
		return true;
	}
	TArray<uint8> Bytes;
	if (!JsonToValue(T, V, Bytes, Err))
	{
		Err = FString::Printf(TEXT("input '%s' (%s): %s"), *InputName(In), *TypeName(T), *Err);
		return false;
	}
	UScriptStruct* S = T.IsEnum() ? FNiagaraTypeDefinition::GetIntStruct() : T.GetScriptStruct();
	TSharedRef<FStructOnScope> Scope = MakeShared<FStructOnScope>(S);
	FMemory::Memcpy(Scope->GetStructMemory(), Bytes.GetData(), S->GetStructureSize());
	In->SetLocalValue(Scope);
	return true;
}

/** The child of a just-set dynamic input whose name contains Hint, else the Nth child of the given type. */
FString PickChild(FCtx& Ctx, const FModuleRef& Ref, const TArray<FString>& Path, const TArray<FString>& Hints, const FNiagaraTypeDefinition* Type, int32 Nth, FString& Err)
{
	UNiagaraStackFunctionInput* Parent = ResolveInputPath(Ctx, Ref, Path, Err);
	if (!Parent) return FString();
	TArray<UNiagaraStackFunctionInput*> Children;
	CollectInputs(Parent, Children);
	for (const FString& H : Hints)
	{
		for (UNiagaraStackFunctionInput* C : Children)
		{
			if (InputVisible(C) && NiaNorm(InputName(C)).Contains(NiaNorm(H))) return InputName(C);
		}
	}
	int32 Seen = 0;
	for (UNiagaraStackFunctionInput* C : Children)
	{
		if (!InputVisible(C)) continue;
		if (Type && !(C->GetInputType() == *Type)) continue;
		if (Seen++ == Nth) return InputName(C);
	}
	TArray<FString> Names;
	for (UNiagaraStackFunctionInput* C : Children) Names.Add(InputName(C) + TEXT(":") + TypeName(C->GetInputType()));
	Err = FString::Printf(TEXT("dynamic input on '%s' has no suitable child (children: %s)"), *Path.Last(), *FString::Join(Names, TEXT(", ")));
	return FString();
}

/**
 * Spec forms (any input):
 *   5 | [1,0,0] | true | "EnumValue"                     local value
 *   {"value": ...}                                       local value
 *   {"link": "Particles.NormalizedAge"}                  linked parameter (User.X, Emitter.X, Particles.X, Engine.X ...)
 *   {"dynamic": "UniformRangedFloat" | path, "inputs": {"Minimum": 1, "Maximum": 2}}
 *   {"random": [min, max]}                               Uniform Ranged <type> dynamic input
 *   {"curve": [[t, v...], ...] | {"keys": [...], "interp": "cubic"}}  curve DI, or <Type>FromCurve over normalized age
 *   {"expression": "Particles.Age * 2.0"}                HLSL expression
 *   {"object": "/Game/M_X"}                              object asset (mesh, material, texture inputs)
 *   {"data": {"Prop": value}}                            data interface properties
 *   {"reset": true}                                      back to the module's default
 */
bool SetInputPath(FCtx& Ctx, const FModuleRef& Ref, TArray<FString> Path, const TSharedPtr<FJsonValue>& Spec, FString& Err, int32 Depth)
{
	if (Depth > 8)
	{
		Err = TEXT("input spec nested too deeply");
		return false;
	}
	UNiagaraStackFunctionInput* In = ResolveInputPath(Ctx, Ref, Path, Err);
	if (!In) return false;
	if (!Spec.IsValid())
	{
		Err = TEXT("missing value");
		return false;
	}
	if (In->GetIsHidden())
	{
		Err = FString::Printf(TEXT("input '%s' is hidden by a static switch on this module; set the switch first"), *InputName(In));
		return false;
	}
	if (In->GetHasEditCondition() && !In->GetEditConditionEnabled())
	{
		In->SetEditConditionEnabled(true); // a value for a toggled-off input means "turn it on"
		In = ResolveInputPath(Ctx, Ref, Path, Err);
		if (!In) return false;
	}
	const FNiagaraTypeDefinition Type = In->GetInputType();
	if (Spec->Type != EJson::Object || (Type.GetScriptStruct() && !Spec->AsObject()->HasField(TEXT("value")) &&
		!Spec->AsObject()->HasField(TEXT("link")) && !Spec->AsObject()->HasField(TEXT("dynamic")) && !Spec->AsObject()->HasField(TEXT("random")) &&
		!Spec->AsObject()->HasField(TEXT("curve")) && !Spec->AsObject()->HasField(TEXT("expression")) && !Spec->AsObject()->HasField(TEXT("object")) &&
		!Spec->AsObject()->HasField(TEXT("data")) && !Spec->AsObject()->HasField(TEXT("reset")) && !Type.IsDataInterface() &&
		(Spec->AsObject()->HasField(TEXT("r")) || Spec->AsObject()->HasField(TEXT("x")))))
	{
		return SetLocal(In, Spec, Err); // raw value, including {"r":..} colors
	}
	const TSharedPtr<FJsonObject> O = Spec->AsObject();
	bool bReset = false;
	if (O->TryGetBoolField(TEXT("reset"), bReset) && bReset)
	{
		if (In->CanReset()) In->Reset();
		return true;
	}
	if (O->HasField(TEXT("value"))) return SetLocal(In, O->TryGetField(TEXT("value")), Err);
	if (O->HasField(TEXT("link")))
	{
		FString Link = Str(O, TEXT("link"));
		if (!Link.Contains(TEXT("."))) Link = TEXT("User.") + Link;
		FNiagaraTypeDefinition LinkType = Type;
		const FString LT = Str(O, TEXT("type"));
		if (!LT.IsEmpty() && !ParseTypeName(LT, LinkType))
		{
			Err = FString::Printf(TEXT("unknown type '%s'"), *LT);
			return false;
		}
		In->SetLinkedParameterValue(FNiagaraVariableBase(LinkType, FName(*Link)));
		return true;
	}
	if (O->HasField(TEXT("expression")))
	{
		if (!In->SupportsCustomExpressions())
		{
			Err = FString::Printf(TEXT("input '%s' does not take HLSL expressions"), *InputName(In));
			return false;
		}
		In->SetCustomExpression(Str(O, TEXT("expression")));
		return true;
	}
	if (O->HasField(TEXT("object")))
	{
		UObject* Asset = LoadAs<UObject>(Str(O, TEXT("object")));
		if (!Asset)
		{
			Err = FString::Printf(TEXT("asset '%s' not found"), *Str(O, TEXT("object")));
			return false;
		}
		In->SetObjectAssetValue(Asset);
		return true;
	}
	if (O->HasField(TEXT("dynamic")))
	{
		FString ScriptName;
		TSharedPtr<FJsonObject> Inputs = Obj(O, TEXT("inputs"));
		const TSharedPtr<FJsonValue> D = O->TryGetField(TEXT("dynamic"));
		if (D->Type == EJson::String) ScriptName = D->AsString();
		else if (D->Type == EJson::Object)
		{
			ScriptName = Str(D->AsObject(), TEXT("script"));
			if (!Inputs.IsValid()) Inputs = Obj(D->AsObject(), TEXT("inputs"));
		}
		UNiagaraScript* Script = FindScript(ScriptName, ENiagaraScriptUsage::DynamicInput, Err);
		if (!Script) return false;
		In->SetDynamicInput(Script);
		if (Inputs.IsValid())
		{
			for (const auto& PairIt : Inputs->Values)
			{
				const TPair<FString, TSharedPtr<FJsonValue>> Pair(FString(PairIt.Key), PairIt.Value);
				TArray<FString> Child = Path;
				Child.Add(Pair.Key);
				if (!SetInputPath(Ctx, Ref, Child, Pair.Value, Err, Depth + 1)) return false;
			}
		}
		return true;
	}
	if (O->HasField(TEXT("random")))
	{
		const TArray<TSharedPtr<FJsonValue>>* MinMax = nullptr;
		if (!O->TryGetArrayField(TEXT("random"), MinMax) || MinMax->Num() != 2)
		{
			Err = TEXT("\"random\" is [minimum, maximum]");
			return false;
		}
		UNiagaraScript* Script = RandomScriptFor(Type);
		if (!Script)
		{
			Err = FString::Printf(TEXT("no uniform-ranged dynamic input for %s"), *TypeName(Type));
			return false;
		}
		In->SetDynamicInput(Script);
		for (int32 I = 0; I < 2; ++I)
		{
			const FString Child = PickChild(Ctx, Ref, Path, {I == 0 ? TEXT("Minimum") : TEXT("Maximum"), I == 0 ? TEXT("Min") : TEXT("Max")}, &Type, I, Err);
			if (Child.IsEmpty()) return false;
			TArray<FString> P = Path;
			P.Add(Child);
			if (!SetInputPath(Ctx, Ref, P, (*MinMax)[I], Err, Depth + 1)) return false;
		}
		return true;
	}
	if (O->HasField(TEXT("curve")))
	{
		const TSharedPtr<FJsonValue> CurveSpec = O->TryGetField(TEXT("curve"));
		if (Type.IsDataInterface())
		{
			if (In->GetValueMode() == UNiagaraStackFunctionInput::EValueMode::Data && In->GetDataValueObject())
			{
				return ApplyCurve(In->GetDataValueObject(), CurveSpec, Err);
			}
			bool bOk = false;
			In->SetDataInterfaceValueExternal(Type.GetClass(), [&](UNiagaraDataInterface* DI) { bOk = DI && ApplyCurve(DI, CurveSpec, Err); });
			if (!bOk && Err.IsEmpty()) Err = TEXT("could not create the curve data interface");
			return bOk;
		}
		UNiagaraScript* Script = CurveScriptFor(Type);
		if (!Script)
		{
			Err = FString::Printf(TEXT("no from-curve dynamic input for %s"), *TypeName(Type));
			return false;
		}
		In->SetDynamicInput(Script);
		UNiagaraStackFunctionInput* Parent = ResolveInputPath(Ctx, Ref, Path, Err);
		if (!Parent) return false;
		TArray<UNiagaraStackFunctionInput*> Children;
		CollectInputs(Parent, Children);
		for (UNiagaraStackFunctionInput* C : Children)
		{
			if (C->GetInputType().IsDataInterface())
			{
				TArray<FString> P = Path;
				P.Add(InputName(C));
				TSharedPtr<FJsonObject> Sub = MakeShared<FJsonObject>();
				Sub->SetField(TEXT("curve"), CurveSpec);
				return SetInputPath(Ctx, Ref, P, MakeShared<FJsonValueObject>(Sub), Err, Depth + 1);
			}
		}
		Err = TEXT("the from-curve dynamic input has no curve child");
		return false;
	}
	if (O->HasField(TEXT("data")))
	{
		if (!Type.IsDataInterface())
		{
			Err = FString::Printf(TEXT("input '%s' is not a data interface"), *InputName(In));
			return false;
		}
		const TSharedPtr<FJsonObject> Props = Obj(O, TEXT("data"));
		TArray<FString> Errors;
		auto Apply = [&](UNiagaraDataInterface* DI)
		{
			if (!DI) { Errors.Add(TEXT("could not create the data interface")); return; }
			FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(DI, Props, Errors);
		};
		if (In->GetValueMode() == UNiagaraStackFunctionInput::EValueMode::Data && In->GetDataValueObject()) Apply(In->GetDataValueObject());
		else In->SetDataInterfaceValueExternal(Type.GetClass(), Apply);
		if (Errors.Num())
		{
			Err = FString::Join(Errors, TEXT("; "));
			return false;
		}
		return true;
	}
	return SetLocal(In, Spec, Err);
}

bool SetInputs(FCtx& Ctx, const FModuleRef& Ref, const TSharedPtr<FJsonObject>& Inputs, FString& Err)
{
	if (!Inputs.IsValid()) return true;
	// Static switches first: they decide which other inputs exist.
	TArray<FString> Order, Later;
	for (const auto& PairIt : Inputs->Values)
	{
		const TPair<FString, TSharedPtr<FJsonValue>> Pair(FString(PairIt.Key), PairIt.Value);
		FString E2;
		TArray<FString> Path;
		Pair.Key.ParseIntoArray(Path, TEXT("/"));
		if (Path.Num() == 0) Path.Add(Pair.Key);
		UNiagaraStackFunctionInput* In = ResolveInputPath(Ctx, Ref, Path, E2);
		(In && In->IsStaticParameter() ? Order : Later).Add(Pair.Key);
	}
	Order.Append(Later);
	for (const FString& Key : Order)
	{
		TArray<FString> Path;
		Key.ParseIntoArray(Path, TEXT("/"));
		if (Path.Num() == 0) Path.Add(Key);
		if (!SetInputPath(Ctx, Ref, Path, Inputs->TryGetField(Key), Err))
		{
			return false;
		}
	}
	return true;
}

// ---------------------------------------------------------------------------------------------
// Apply: module reorder (relink the stack's parameter-map chain)
// ---------------------------------------------------------------------------------------------

bool IsMapPin(const UEdGraphPin* P)
{
	return P && P->PinType.PinCategory == UEdGraphSchema_Niagara::PinCategoryType &&
		P->PinType.PinSubCategoryObject == FNiagaraTypeDefinition::GetParameterMapStruct();
}

UEdGraphPin* MapPin(UEdGraphNode* N, EEdGraphPinDirection Dir)
{
	if (!N) return nullptr;
	for (UEdGraphPin* P : N->Pins)
	{
		if (P && P->Direction == Dir && IsMapPin(P) && !P->bOrphanedPin) return P;
	}
	return nullptr;
}

/** First node of a module's stack group: its input override node (ParameterMapSet) if it has one. */
UEdGraphNode* GroupStart(UEdGraphNode* Module)
{
	UEdGraphPin* In = MapPin(Module, EGPD_Input);
	if (In && In->LinkedTo.Num() == 1)
	{
		UEdGraphNode* Prev = In->LinkedTo[0]->GetOwningNode();
		if (Prev && Prev->GetClass()->GetName() == TEXT("NiagaraNodeParameterMapSet")) return Prev;
	}
	return Module;
}

bool MoveModule(UNiagaraStackModuleItem* M, int32 NewIndex, const TArray<UNiagaraStackModuleItem*>& Mods, UNiagaraNodeOutput* OutputNode, FString& Err)
{
	UNiagaraNodeFunctionCall& Node = M->GetModuleNode();
	if (!M->CanMoveAndDelete())
	{
		Err = FString::Printf(TEXT("module %s is inherited from a parent emitter and cannot be moved"), *Node.GetFunctionName());
		return false;
	}
	TArray<UNiagaraStackModuleItem*> Others = Mods;
	Others.Remove(M);
	NewIndex = FMath::Clamp(NewIndex, 0, Others.Num());
	UEdGraphNode* Start = GroupStart(&Node);
	UEdGraphPin* StartIn = MapPin(Start, EGPD_Input);
	UEdGraphPin* EndOut = MapPin(&Node, EGPD_Output);
	if (!StartIn || !EndOut || StartIn->LinkedTo.Num() != 1 || EndOut->LinkedTo.Num() != 1)
	{
		Err = TEXT("unexpected stack graph shape around the module (parameter map pins)");
		return false;
	}
	// Target: insert before the group of Others[NewIndex], or before the output node.
	UEdGraphNode* TargetStart = NewIndex < Others.Num() ? GroupStart(&Others[NewIndex]->GetModuleNode()) : OutputNode;
	UEdGraphPin* TargetIn = MapPin(TargetStart, EGPD_Input);
	if (!TargetIn || TargetIn->LinkedTo.Num() != 1)
	{
		Err = TEXT("unexpected stack graph shape at the target position");
		return false;
	}
	if (TargetIn == EndOut->LinkedTo[0])
	{
		return true; // already there
	}
	UEdGraph* Graph = Node.GetGraph();
	Graph->Modify();
	Start->Modify();
	Node.Modify();
	UEdGraphPin* PrevOut = StartIn->LinkedTo[0];
	UEdGraphPin* NextIn = EndOut->LinkedTo[0];
	PrevOut->GetOwningNode()->Modify();
	NextIn->GetOwningNode()->Modify();
	TargetStart->Modify();
	// Unhook the group and close the gap.
	StartIn->BreakAllPinLinks();
	EndOut->BreakAllPinLinks();
	PrevOut->MakeLinkTo(NextIn);
	// Splice it in before TargetStart.
	UEdGraphPin* TargetPrevOut = TargetIn->LinkedTo[0];
	TargetPrevOut->GetOwningNode()->Modify();
	TargetPrevOut->BreakLinkTo(TargetIn);
	TargetPrevOut->MakeLinkTo(StartIn);
	EndOut->MakeLinkTo(TargetIn);
	Graph->NotifyGraphChanged();
	return true;
}

// ---------------------------------------------------------------------------------------------
// Apply: ops
// ---------------------------------------------------------------------------------------------

struct FApplyState
{
	TArray<TSharedPtr<FJsonValue>> Results;
	bool bTouchedEmitters = false;
};

FVersionedNiagaraEmitterData* EmitterData(FCtx& Ctx, const FString& Name, FNiagaraEmitterHandle*& OutHandle, FString& Err)
{
	OutHandle = Ctx.Handle(Name);
	if (!OutHandle)
	{
		Ctx.EmitterVM(Name, Err); // builds the "emitters: ..." message
		return nullptr;
	}
	FVersionedNiagaraEmitterData* D = OutHandle->GetEmitterData();
	if (!D) Err = FString::Printf(TEXT("emitter '%s' has no data (stateless emitters are not supported)"), *Name);
	return D;
}

void EmitterPropertyChanged(FNiagaraEmitterHandle* H, const TCHAR* PropName)
{
	FVersionedNiagaraEmitter VE = H->GetInstance();
	if (!VE.Emitter) return;
	FProperty* P = EmitterDataStruct() ? EmitterDataStruct()->FindPropertyByName(PropName) : nullptr;
	FPropertyChangedEvent Ev(P, EPropertyChangeType::ValueSet);
	VE.Emitter->PostEditChangeVersionedProperty(Ev, VE.Version);
}

bool OpSetEmitter(FCtx& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	FNiagaraEmitterHandle* H = nullptr;
	FVersionedNiagaraEmitterData* D = EmitterData(Ctx, Str(Op, TEXT("emitter")), H, Err);
	if (!D) return false;
	FVersionedNiagaraEmitter VE = H->GetInstance();
	VE.Emitter->Modify();
	bool bB = false;
	FString S;
	if (Op->TryGetStringField(TEXT("sim_target"), S))
	{
		D->SimTarget = S.ToLower().StartsWith(TEXT("gpu")) ? ENiagaraSimTarget::GPUComputeSim : ENiagaraSimTarget::CPUSim;
		EmitterPropertyChanged(H, TEXT("SimTarget"));
	}
	if (Op->TryGetBoolField(TEXT("local_space"), bB))
	{
		D->bLocalSpace = bB;
		EmitterPropertyChanged(H, TEXT("bLocalSpace"));
	}
	if (Op->TryGetBoolField(TEXT("determinism"), bB))
	{
		D->bDeterminism = bB;
		EmitterPropertyChanged(H, TEXT("bDeterminism"));
	}
	double Seed = 0;
	if (Op->TryGetNumberField(TEXT("random_seed"), Seed))
	{
		D->RandomSeed = (int32)Seed;
		EmitterPropertyChanged(H, TEXT("RandomSeed"));
	}
	const TSharedPtr<FJsonObject> Bounds = Obj(Op, TEXT("bounds"));
	if (Bounds.IsValid())
	{
		const FString Mode = Str(Bounds, TEXT("mode")).ToLower();
		if (Mode == TEXT("dynamic"))
		{
			D->CalculateBoundsMode = ENiagaraEmitterCalculateBoundMode::Dynamic;
		}
		else
		{
			FBox Box;
			if (!ReadBox(Bounds, Box, Err)) return false;
			D->CalculateBoundsMode = ENiagaraEmitterCalculateBoundMode::Fixed;
			D->FixedBounds = Box;
			EmitterPropertyChanged(H, TEXT("FixedBounds"));
		}
		EmitterPropertyChanged(H, TEXT("CalculateBoundsMode"));
	}
	const TSharedPtr<FJsonObject> Props = Obj(Op, TEXT("properties"));
	if (Props.IsValid())
	{
		UScriptStruct* SS = EmitterDataStruct();
		for (const auto& PairIt : Props->Values)
		{
			const TPair<FString, TSharedPtr<FJsonValue>> Pair(FString(PairIt.Key), PairIt.Value);
			FProperty* P = SS ? FindPropNormalized(SS, Pair.Key) : nullptr;
			if (!P)
			{
				Err = FString::Printf(TEXT("emitter property '%s' not found (see niagara_export emitters[].properties for names)"), *Pair.Key);
				return false;
			}
			void* Ptr = P->ContainerPtrToValuePtr<void>(D);
			bool bOk = FJsonObjectConverter::JsonValueToUProperty(Pair.Value, P, Ptr, 0, 0);
			if (!bOk && Pair.Value->Type == EJson::String)
			{
				bOk = P->ImportText_Direct(*Pair.Value->AsString(), Ptr, nullptr, PPF_None) != nullptr;
			}
			if (!bOk)
			{
				Err = FString::Printf(TEXT("could not set emitter property '%s'"), *Pair.Key);
				return false;
			}
			EmitterPropertyChanged(H, *P->GetName());
		}
	}
	if (Op->TryGetBoolField(TEXT("enabled"), bB))
	{
		H->SetIsEnabled(bB, *Ctx.System, false);
	}
	FString NewName;
	if (Op->TryGetStringField(TEXT("rename"), NewName) && !NewName.IsEmpty())
	{
		H->SetName(FName(*NewName), *Ctx.System);
	}
	Ctx.VM->RefreshAll();
	return true;
}

bool OpAddEmitter(FCtx& Ctx, const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Res, FString& Err)
{
	FString From = Str(Op, TEXT("from"));
	if (From.IsEmpty()) From = Str(Op, TEXT("template"));
	if (From.IsEmpty()) From = TEXT("Minimal");
	UNiagaraEmitter* Source = FindEmitterAsset(From, Err);
	if (!Source) return false;
	const FString Name = Str(Op, TEXT("name"));
	if (!Name.IsEmpty() && Ctx.Handle(Name))
	{
		Err = FString::Printf(TEXT("the system already has an emitter named '%s'"), *Name);
		return false;
	}
	TSet<FGuid> Before;
	for (const FNiagaraEmitterHandle& H : Ctx.System->GetEmitterHandles()) Before.Add(H.GetId());
	{
		// Epic's external-edit API (what the NiagaraToolsets AI plugin calls): adds the copy, names it and
		// places its overview node. It builds its own view model, so ours is rebuilt afterwards.
		Ctx.Release();
		FNiagaraExternalEditContext EC(Ctx.System);
		FNiagaraExt_EmitterTopology Topology;
		UNiagaraExternalEditUtilities::AddEmitter(Source, Name.IsEmpty() ? Source->GetFName() : FName(*Name), Topology, EC);
		Ctx.Reset();
		if (EC.HasErrors())
		{
			Err = EC.Errors[0].ToString();
			return false;
		}
	}
	FNiagaraEmitterHandle* NewHandle = nullptr;
	for (FNiagaraEmitterHandle& H : Ctx.System->GetEmitterHandles())
	{
		if (!Before.Contains(H.GetId())) NewHandle = &H;
	}
	if (!NewHandle)
	{
		Err = FString::Printf(TEXT("adding emitter %s failed"), *Source->GetPathName());
		return false;
	}
	bool bInherit = false;
	Op->TryGetBoolField(TEXT("inherit"), bInherit);
	if (!bInherit)
	{
		if (FVersionedNiagaraEmitterData* D = NewHandle->GetEmitterData())
		{
			if (D->GetParent().Emitter)
			{
				NewHandle->GetInstance().Emitter->Modify();
				D->RemoveParent(); // a standalone copy: every module can be edited and deleted
			}
		}
	}
	const FString FinalName = NewHandle->GetName().ToString();
	// Optional position in the emitter list.
	double Index = -1;
	if (Op->TryGetNumberField(TEXT("index"), Index) && Index >= 0)
	{
		TArray<FNiagaraEmitterHandle>& Handles = Ctx.System->GetEmitterHandles();
		const int32 From2 = Handles.IndexOfByPredicate([&](const FNiagaraEmitterHandle& H) { return H.GetName().ToString() == FinalName; });
		const int32 To = FMath::Clamp((int32)Index, 0, Handles.Num() - 1);
		if (From2 != INDEX_NONE && From2 != To)
		{
			FNiagaraEmitterHandle Moved = Handles[From2];
			Handles.RemoveAt(From2);
			Handles.Insert(Moved, To);
		}
	}
	Ctx.VM->RefreshAll();
	Res->SetStringField(TEXT("emitter"), FinalName);
	Res->SetStringField(TEXT("from"), Source->GetPathName());
	return true;
}

bool OpMoveEmitter(FCtx& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString Name = Str(Op, TEXT("emitter"));
	TArray<FNiagaraEmitterHandle>& Handles = Ctx.System->GetEmitterHandles();
	const int32 From = Handles.IndexOfByPredicate([&](const FNiagaraEmitterHandle& H) { return NiaNorm(H.GetName().ToString()) == NiaNorm(Name); });
	if (From == INDEX_NONE)
	{
		Ctx.EmitterVM(Name, Err);
		return false;
	}
	double Index = 0;
	if (!Op->TryGetNumberField(TEXT("index"), Index))
	{
		Err = TEXT("move_emitter needs \"index\"");
		return false;
	}
	const int32 To = FMath::Clamp((int32)Index, 0, Handles.Num() - 1);
	if (To != From)
	{
		Ctx.System->Modify();
		FNiagaraEmitterHandle Moved = Handles[From];
		Handles.RemoveAt(From);
		Handles.Insert(Moved, To);
		Ctx.System->ComputeEmittersExecutionOrder();
		Ctx.Reset(); // handle view models index the old array
	}
	return true;
}

bool OpAddModule(FCtx& Ctx, const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Res, FString& Err)
{
	FModuleRef Ref;
	if (!ParseModuleRef(Ctx, Op, Ref, Err, false)) return false;
	FString ScriptName = Str(Op, TEXT("script"));
	if (ScriptName.IsEmpty() && Ref.Module.Len()) ScriptName = Ref.Module;
	UNiagaraScript* Script = FindScript(ScriptName, ENiagaraScriptUsage::Module, Err);
	if (!Script) return false;
	FStageRef S;
	if (!ResolveStage(Ctx, Ref, S, Err)) return false;
	UNiagaraNodeOutput* OutNode = S.Group->GetScriptOutputNode();
	double Index = -1;
	Op->TryGetNumberField(TEXT("index"), Index);
	TSet<FString> Before;
	for (UNiagaraStackModuleItem* M : Modules(S)) Before.Add(ModuleName(M));
	const FVersionedNiagaraScriptData* Latest = Script->GetLatestScriptData();
	UNiagaraNodeFunctionCall* Node = FNiagaraStackGraphUtilities::AddScriptModuleToStack(Script, *OutNode, Index >= 0 ? (int32)Index : INDEX_NONE,
		Str(Op, TEXT("name")), Latest ? Latest->Version.VersionGuid : FGuid());
	if (!Node)
	{
		Err = FString::Printf(TEXT("could not add %s to %s (wrong stage for this module? modules have a usage: particle spawn/update, emitter, system)"),
			*Script->GetPathName(), *S.Key);
		return false;
	}
	S.Group->RefreshChildren();
	const FString Added = Node->GetFunctionName();
	Res->SetStringField(TEXT("module"), Added);
	Res->SetStringField(TEXT("stage"), S.Key);
	FModuleRef NewRef = Ref;
	NewRef.Module = Added;
	NewRef.Index = INDEX_NONE;
	bool bEnabled = true;
	if (Op->TryGetBoolField(TEXT("enabled"), bEnabled) && !bEnabled)
	{
		if (UNiagaraStackModuleItem* M = ResolveModule(Ctx, NewRef, Err)) M->SetIsEnabled(false);
	}
	return SetInputs(Ctx, NewRef, Obj(Op, TEXT("inputs")), Err);
}

bool OpRenderer(FCtx& Ctx, const FString& Kind, const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Res, FString& Err)
{
	FNiagaraEmitterHandle* H = nullptr;
	FVersionedNiagaraEmitterData* D = EmitterData(Ctx, Str(Op, TEXT("emitter")), H, Err);
	if (!D) return false;
	FVersionedNiagaraEmitter VE = H->GetInstance();
	UNiagaraEmitter* Emitter = VE.Emitter;
	UNiagaraRendererProperties* R = nullptr;
	if (Kind == TEXT("add_renderer"))
	{
		UClass* C = RendererClass(Str(Op, TEXT("type")).IsEmpty() ? Str(Op, TEXT("class")) : Str(Op, TEXT("type")));
		if (!C)
		{
			Err = TEXT("add_renderer needs \"type\": sprite, mesh, ribbon, light (or a renderer class)");
			return false;
		}
		Emitter->Modify();
		R = NewObject<UNiagaraRendererProperties>(Emitter, C, NAME_None, RF_Transactional);
		Emitter->AddRenderer(R, VE.Version);
		double Index = -1;
		if (Op->TryGetNumberField(TEXT("index"), Index) && Index >= 0) Emitter->MoveRenderer(R, (int32)Index, VE.Version);
	}
	else
	{
		const TArray<UNiagaraRendererProperties*>& Rs = D->GetRenderers();
		const TSharedPtr<FJsonValue> Which = Op->TryGetField(TEXT("renderer"));
		if (Which.IsValid() && Which->Type == EJson::Number && Rs.IsValidIndex((int32)Which->AsNumber())) R = Rs[(int32)Which->AsNumber()];
		else if (Which.IsValid() && Which->Type == EJson::String)
		{
			for (UNiagaraRendererProperties* X : Rs)
			{
				if (X && (RendererKind(X) == Which->AsString().ToLower() || X->GetClass()->GetName() == Which->AsString())) { R = X; break; }
			}
		}
		else if (!Which.IsValid() && Rs.Num() == 1) R = Rs[0];
		if (!R)
		{
			TArray<FString> Names;
			for (int32 I = 0; I < Rs.Num(); ++I) Names.Add(FString::Printf(TEXT("%d:%s"), I, Rs[I] ? *RendererKind(Rs[I]) : TEXT("?")));
			Err = FString::Printf(TEXT("renderer not found; pass \"renderer\": index or type (renderers: %s)"), *FString::Join(Names, TEXT(", ")));
			return false;
		}
		if (Kind == TEXT("remove_renderer"))
		{
			Emitter->Modify();
			Emitter->RemoveRenderer(R, VE.Version);
			return true;
		}
	}
	R->Modify();
	bool bEnabled = true;
	if (Op->TryGetBoolField(TEXT("enabled"), bEnabled)) R->SetIsEnabled(bEnabled);
	FString Path;
	if (Op->TryGetStringField(TEXT("material"), Path))
	{
		UMaterialInterface* Mat = LoadAs<UMaterialInterface>(Path);
		if (!Mat)
		{
			Err = FString::Printf(TEXT("material '%s' not found"), *Path);
			return false;
		}
		if (UNiagaraMeshRendererProperties* MR = Cast<UNiagaraMeshRendererProperties>(R))
		{
			MR->bOverrideMaterials = true;
			MR->OverrideMaterials.SetNum(1);
			MR->OverrideMaterials[0].ExplicitMat = Mat;
		}
		else if (FObjectProperty* MP = CastField<FObjectProperty>(R->GetClass()->FindPropertyByName(TEXT("Material"))))
		{
			MP->SetObjectPropertyValue_InContainer(R, Mat);
		}
		else
		{
			Err = FString::Printf(TEXT("%s has no Material property"), *R->GetClass()->GetName());
			return false;
		}
	}
	if (Op->TryGetStringField(TEXT("mesh"), Path))
	{
		UNiagaraMeshRendererProperties* MR = Cast<UNiagaraMeshRendererProperties>(R);
		UStaticMesh* Mesh = LoadAs<UStaticMesh>(Path);
		if (!MR || !Mesh)
		{
			Err = !MR ? TEXT("\"mesh\" is for mesh renderers") : FString::Printf(TEXT("static mesh '%s' not found"), *Path);
			return false;
		}
		if (MR->Meshes.Num() == 0) MR->Meshes.AddDefaulted();
		MR->Meshes[0].Mesh = Mesh;
	}
	const TSharedPtr<FJsonObject> Bindings = Obj(Op, TEXT("bindings"));
	if (Bindings.IsValid())
	{
		for (const auto& PairIt : Bindings->Values)
		{
			const TPair<FString, TSharedPtr<FJsonValue>> Pair(FString(PairIt.Key), PairIt.Value);
			FStructProperty* BP = nullptr;
			for (TFieldIterator<FStructProperty> It(R->GetClass()); It; ++It)
			{
				if (It->Struct && It->Struct->GetName() == TEXT("NiagaraVariableAttributeBinding") &&
					(NiaNorm(It->GetName()) == NiaNorm(Pair.Key) || NiaNorm(It->GetName()) == NiaNorm(Pair.Key + TEXT("Binding"))))
				{
					BP = *It;
				}
			}
			if (!BP)
			{
				Err = FString::Printf(TEXT("%s has no binding '%s' (see niagara_export renderers[].bindings)"), *R->GetClass()->GetName(), *Pair.Key);
				return false;
			}
			FNiagaraVariableAttributeBinding* B = BP->ContainerPtrToValuePtr<FNiagaraVariableAttributeBinding>(R);
			B->SetValue(FName(*Pair.Value->AsString()), VE.ToBase(), ENiagaraRendererSourceDataMode::Particles);
		}
	}
	const TSharedPtr<FJsonObject> Props = Obj(Op, TEXT("properties"));
	if (Props.IsValid())
	{
		TArray<FString> Errors;
		FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(R, Props, Errors);
		if (Errors.Num())
		{
			Err = FString::Join(Errors, TEXT("; "));
			return false;
		}
	}
	R->PostEditChange();
	const int32 Index = D->GetRenderers().IndexOfByKey(R);
	Res->SetNumberField(TEXT("renderer"), Index);
	return true;
}

bool OpUserParameter(FCtx& Ctx, const FString& Kind, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	FString Name = Str(Op, TEXT("name"));
	if (Name.IsEmpty())
	{
		Err = TEXT("missing \"name\"");
		return false;
	}
	if (!Name.StartsWith(TEXT("User."))) Name = TEXT("User.") + Name;
	FNiagaraUserRedirectionParameterStore& Store = Ctx.System->GetExposedParameters();
	const FNiagaraVariableWithOffset* Existing = nullptr;
	for (const FNiagaraVariableWithOffset& V : Store.ReadParameterVariables())
	{
		if (V.GetName().ToString().Equals(Name, ESearchCase::IgnoreCase)) Existing = &V;
	}
	Ctx.System->Modify();
	if (Kind == TEXT("remove_user_parameter"))
	{
		if (!Existing)
		{
			Err = FString::Printf(TEXT("no user parameter %s"), *Name);
			return false;
		}
		Store.RemoveParameter(FNiagaraVariableBase(Existing->GetType(), Existing->GetName()));
		return true;
	}
	FNiagaraTypeDefinition Type;
	const FString TypeStr = Str(Op, TEXT("type"));
	if (Existing && TypeStr.IsEmpty()) Type = Existing->GetType();
	else if (!ParseTypeName(TypeStr, Type))
	{
		Err = FString::Printf(TEXT("user parameter %s needs \"type\": float, int, bool, vec2, vec3, vec4, color, position, quat, material, mesh, texture"), *Name);
		return false;
	}
	if (Existing && !(Existing->GetType() == Type))
	{
		Err = FString::Printf(TEXT("user parameter %s exists with type %s"), *Name, *TypeName(Existing->GetType()));
		return false;
	}
	FNiagaraVariable Var(Type, FName(*Name));
	const TSharedPtr<FJsonValue> V = Op->TryGetField(TEXT("value"));
	if (Type.IsUObject())
	{
		UObject* Asset = V.IsValid() && V->Type == EJson::String ? LoadAs<UObject>(V->AsString()) : nullptr;
		if (!Existing) Store.AddParameter(Var, true, true);
		if (Asset) Store.SetUObject(Asset, Var);
	}
	else
	{
		// Epic's external-edit API adds or updates the exposed parameter (and its script variable).
		TArray<uint8> Bytes;
		if (V.IsValid())
		{
			if (!JsonToValue(Type, V, Bytes, Err)) return false;
		}
		else if (Existing)
		{
			return true; // nothing to change
		}
		else
		{
			Bytes.SetNumZeroed(Type.GetSize());
		}
		FNiagaraExt_UserVariable UV;
		UV.Name = FName(*Name);
		UV.Type = Type;
		UV.DefaultValue.Set(Type, FNiagaraVariant(Bytes.GetData(), Bytes.Num()));
		UV.Description = FText::FromString(Str(Op, TEXT("description")));
		FNiagaraExternalEditContext EC;
		UNiagaraExternalEditUtilities::AddUserVariable(Ctx.System, UV, EC);
		if (EC.HasErrors())
		{
			Err = EC.Errors[0].ToString();
			return false;
		}
	}
	Store.OnParameterChange();
	return true;
}

bool OpAddEventHandler(FCtx& Ctx, const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Res, FString& Err)
{
	TSharedPtr<FNiagaraEmitterHandleViewModel> EVM = Ctx.EmitterVM(Str(Op, TEXT("emitter")), Err);
	if (!EVM.IsValid()) return false;
	const FString SourceName = Str(Op, TEXT("source_emitter"));
	FNiagaraEmitterHandle* Source = Ctx.Handle(SourceName);
	if (!Source)
	{
		Ctx.EmitterVM(SourceName, Err);
		Err = TEXT("source_emitter: ") + Err;
		return false;
	}
	FNiagaraEventScriptProperties Props;
	Props.SourceEmitterID = Source->GetId();
	const FString Event = Str(Op, TEXT("event"));
	Props.SourceEventName = FName(*(Event.IsEmpty() ? FString(TEXT("DeathEvent")) : Event));
	const FString Mode = NiaNorm(Str(Op, TEXT("mode")));
	Props.ExecutionMode = Mode == TEXT("everyparticle") ? EScriptExecutionMode::EveryParticle
		: Mode == TEXT("singleparticle") ? EScriptExecutionMode::SingleParticle : EScriptExecutionMode::SpawnedParticles;
	double N = 0;
	if (Op->TryGetNumberField(TEXT("spawn_number"), N)) Props.SpawnNumber = (uint32)N;
	if (Op->TryGetNumberField(TEXT("max_events_per_frame"), N)) Props.MaxEventsPerFrame = (uint32)N;
	if (Op->TryGetNumberField(TEXT("min_spawn_number"), N)) Props.MinSpawnNumber = (uint32)N;
	bool bB = false;
	if (Op->TryGetBoolField(TEXT("random_spawn_number"), bB)) Props.bRandomSpawnNumber = bB;
	FVersionedNiagaraEmitter VE = EVM->GetEmitterHandle()->GetInstance();
	VE.Emitter->Modify();
	FVersionedNiagaraEmitterData* D = EVM->GetEmitterHandle()->GetEmitterData();
	const int32 Before = D ? D->GetEventHandlers().Num() : 0;
	EVM->GetEmitterViewModel()->AddEventHandler(Props, true);
	Ctx.VM->RefreshAll();
	Res->SetStringField(TEXT("stage"), FString::Printf(TEXT("event:%d"), Before));
	return true;
}

/** One op. Res collects what the caller needs back (assigned names). */
bool RunOp(FCtx& Ctx, const FString& Kind, const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Res, FString& Err)
{
	if (Kind == TEXT("add_emitter")) return OpAddEmitter(Ctx, Op, Res, Err);
	if (Kind == TEXT("remove_emitter"))
	{
		FNiagaraEmitterHandle* H = Ctx.Handle(Str(Op, TEXT("emitter")));
		if (!H)
		{
			Ctx.EmitterVM(Str(Op, TEXT("emitter")), Err);
			return false;
		}
		Ctx.Release();
		FNiagaraExternalEditContext EC(Ctx.System);
		UNiagaraExternalEditUtilities::RemoveEmitter(FNiagaraExt_StackItemReference(Ctx.System, H->GetName()), EC);
		Ctx.Reset();
		if (EC.HasErrors()) Err = EC.Errors[0].ToString();
		return !EC.HasErrors();
	}
	if (Kind == TEXT("move_emitter")) return OpMoveEmitter(Ctx, Op, Err);
	if (Kind == TEXT("set_emitter")) return OpSetEmitter(Ctx, Op, Err);
	if (Kind == TEXT("add_event_handler")) return OpAddEventHandler(Ctx, Op, Res, Err);
	if (Kind == TEXT("add_module")) return OpAddModule(Ctx, Op, Res, Err);
	if (Kind == TEXT("remove_module") || Kind == TEXT("enable_module") || Kind == TEXT("move_module") || Kind == TEXT("set_inputs") || Kind == TEXT("set_input"))
	{
		FModuleRef Ref;
		if (!ParseModuleRef(Ctx, Op, Ref, Err)) return false;
		FStageRef S;
		UNiagaraStackModuleItem* M = ResolveModule(Ctx, Ref, Err, &S);
		if (!M) return false;
		if (Kind == TEXT("remove_module"))
		{
			if (!M->CanMoveAndDelete())
			{
				Err = FString::Printf(TEXT("module %s is inherited from a parent emitter; disable it instead (enable_module false)"), *ModuleName(M));
				return false;
			}
			M->Delete();
			S.Group->RefreshChildren();
			return true;
		}
		if (Kind == TEXT("enable_module"))
		{
			bool bEnabled = true;
			Op->TryGetBoolField(TEXT("enabled"), bEnabled);
			M->SetIsEnabled(bEnabled);
			return true;
		}
		if (Kind == TEXT("move_module"))
		{
			double Index = 0;
			if (!Op->TryGetNumberField(TEXT("index"), Index))
			{
				Err = TEXT("move_module needs \"index\"");
				return false;
			}
			const bool bOk = MoveModule(M, (int32)Index, Modules(S), S.Group->GetScriptOutputNode(), Err);
			S.Group->RefreshChildren();
			return bOk;
		}
		Ref.Module = ModuleName(M);
		Ref.Index = INDEX_NONE;
		if (Kind == TEXT("set_input"))
		{
			FString Input = Str(Op, TEXT("input"));
			TArray<FString> Path;
			Input.ParseIntoArray(Path, TEXT("/"));
			if (Path.Num() == 0)
			{
				Err = TEXT("set_input needs \"input\" (name, or Parent/Child for a dynamic input's input)");
				return false;
			}
			TSharedPtr<FJsonValue> Spec = Op->TryGetField(TEXT("value"));
			if (!Spec.IsValid())
			{
				// {"link": ...}, {"curve": ...}, {"random": ...} ... given at the op level
				TSharedPtr<FJsonObject> Wrapped = MakeShared<FJsonObject>();
				for (const TCHAR* K : {TEXT("link"), TEXT("dynamic"), TEXT("inputs"), TEXT("random"), TEXT("curve"), TEXT("expression"), TEXT("object"), TEXT("data"), TEXT("reset"), TEXT("type")})
				{
					if (Op->HasField(K)) Wrapped->SetField(K, Op->TryGetField(K));
				}
				Spec = MakeShared<FJsonValueObject>(Wrapped);
			}
			else if (Spec->Type != EJson::Object)
			{
				TSharedPtr<FJsonObject> Wrapped = MakeShared<FJsonObject>();
				Wrapped->SetField(TEXT("value"), Spec);
				Spec = MakeShared<FJsonValueObject>(Wrapped);
			}
			return SetInputPath(Ctx, Ref, Path, Spec, Err);
		}
		return SetInputs(Ctx, Ref, Obj(Op, TEXT("inputs")), Err);
	}
	if (Kind == TEXT("add_renderer") || Kind == TEXT("set_renderer") || Kind == TEXT("remove_renderer")) return OpRenderer(Ctx, Kind, Op, Res, Err);
	if (Kind == TEXT("add_user_parameter") || Kind == TEXT("set_user_parameter") || Kind == TEXT("remove_user_parameter")) return OpUserParameter(Ctx, Kind, Op, Err);
	if (Kind == TEXT("add_parameter"))
	{
		// Emitter.X / System.X / Particles.X written once by a Set Parameters module (default stage: emitter_spawn or system_spawn).
		FString Name = Str(Op, TEXT("name"));
		FString Ns = Str(Op, TEXT("namespace"));
		if (Ns.IsEmpty() && Name.Contains(TEXT("."))) Name.Split(TEXT("."), &Ns, &Name);
		if (Ns.IsEmpty()) Ns = Str(Op, TEXT("emitter")).IsEmpty() ? TEXT("System") : TEXT("Emitter");
		if (Ns.Equals(TEXT("User"), ESearchCase::IgnoreCase)) return OpUserParameter(Ctx, TEXT("add_user_parameter"), Op, Err);
		FNiagaraTypeDefinition Type;
		if (!ParseTypeName(Str(Op, TEXT("type")), Type))
		{
			Err = TEXT("add_parameter needs \"type\"");
			return false;
		}
		TSharedPtr<FJsonObject> Op2 = MakeShared<FJsonObject>(*Op);
		if (!Op2->HasField(TEXT("stage")))
		{
			Op2->SetStringField(TEXT("stage"), Ns.Equals(TEXT("System"), ESearchCase::IgnoreCase) ? TEXT("system_spawn")
				: Ns.Equals(TEXT("Particles"), ESearchCase::IgnoreCase) ? TEXT("particle_spawn") : TEXT("emitter_spawn"));
		}
		FModuleRef Ref;
		if (!ParseModuleRef(Ctx, Op2, Ref, Err, false)) return false;
		FStageRef S;
		if (!ResolveStage(Ctx, Ref, S, Err)) return false;
		const FString Full = (Ns.Equals(TEXT("Particles"), ESearchCase::IgnoreCase) ? TEXT("Particles") : Ns.Left(1).ToUpper() + Ns.Mid(1).ToLower()) + TEXT(".") + Name;
		TArray<FNiagaraVariable> Vars = {FNiagaraVariable(Type, FName(*Full))};
		FString Default;
		if (Op->HasField(TEXT("value"))) Default = MCPAuthoring::JsonToDefaultString(Op->TryGetField(TEXT("value")));
		UNiagaraNodeFunctionCall* Node = (UNiagaraNodeFunctionCall*)FNiagaraStackGraphUtilities::AddParameterModuleToStack(Vars, *S.Group->GetScriptOutputNode(), INDEX_NONE, {Default});
		if (!Node)
		{
			Err = FString::Printf(TEXT("could not add a Set Parameters module for %s"), *Full);
			return false;
		}
		S.Group->RefreshChildren();
		Res->SetStringField(TEXT("module"), Node->GetFunctionName());
		Res->SetStringField(TEXT("parameter"), Full);
		if (Op->HasField(TEXT("value")))
		{
			FModuleRef NewRef = Ref;
			NewRef.Module = Node->GetFunctionName();
			NewRef.Index = INDEX_NONE;
			TArray<FString> Path = {Full};
			FString E2;
			if (!SetInputPath(Ctx, NewRef, Path, Op->TryGetField(TEXT("value")), E2))
			{
				TArray<FString> Path2 = {Name};
				SetInputPath(Ctx, NewRef, Path2, Op->TryGetField(TEXT("value")), E2); // pin default already holds it otherwise
			}
		}
		return true;
	}
	if (Kind == TEXT("set_system") || Kind == TEXT("set_bounds"))
	{
		if (Kind == TEXT("set_bounds") && !Str(Op, TEXT("emitter")).IsEmpty())
		{
			TSharedPtr<FJsonObject> Op2 = MakeShared<FJsonObject>();
			Op2->SetStringField(TEXT("emitter"), Str(Op, TEXT("emitter")));
			TSharedPtr<FJsonObject> B = MakeShared<FJsonObject>(*Op);
			Op2->SetObjectField(TEXT("bounds"), B);
			return OpSetEmitter(Ctx, Op2, Err);
		}
		Ctx.System->Modify();
		TSharedPtr<FJsonObject> Bounds = Kind == TEXT("set_bounds") ? Op : Obj(Op, TEXT("fixed_bounds"));
		if (Bounds.IsValid())
		{
			if (Str(Bounds, TEXT("mode")).ToLower() == TEXT("dynamic"))
			{
				Ctx.System->bFixedBounds = false;
			}
			else
			{
				FBox Box;
				if (!ReadBox(Bounds, Box, Err)) return false;
				Ctx.System->bFixedBounds = true;
				Ctx.System->SetFixedBounds(Box);
			}
		}
		double Warmup = 0;
		if (Op->TryGetNumberField(TEXT("warmup_time"), Warmup)) Ctx.System->SetWarmupTime((float)Warmup);
		const TSharedPtr<FJsonObject> Props = Obj(Op, TEXT("properties"));
		if (Props.IsValid())
		{
			TArray<FString> Errors;
			FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(Ctx.System, Props, Errors);
			if (Errors.Num())
			{
				Err = FString::Join(Errors, TEXT("; "));
				return false;
			}
		}
		return true;
	}
	Err = FString::Printf(TEXT("unknown op '%s' (add_emitter, remove_emitter, move_emitter, set_emitter, add_event_handler, add_module, "
		"remove_module, enable_module, move_module, set_input, set_inputs, add_renderer, set_renderer, remove_renderer, add_user_parameter, "
		"set_user_parameter, remove_user_parameter, add_parameter, set_system, set_bounds)"), *Kind);
	return false;
}

// ---------------------------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------------------------

UNiagaraSystem* LoadSystem(const FString& Path)
{
	return LoadAs<UNiagaraSystem>(Path);
}

TSharedPtr<FJsonObject> HandleExport(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("system"));
	UNiagaraSystem* System = LoadSystem(Path);
	if (!System) return MCPError(FString::Printf(TEXT("Niagara system '%s' not found (search_assets class NiagaraSystem)"), *Path));
	bool bHidden = false, bOnlyModified = false, bCompile = false;
	Params->TryGetBoolField(TEXT("include_hidden"), bHidden);
	Params->TryGetBoolField(TEXT("only_modified"), bOnlyModified);
	Params->TryGetBoolField(TEXT("compile"), bCompile);
	TArray<FString> Only;
	const TArray<TSharedPtr<FJsonValue>>* E = nullptr;
	if (Params->TryGetArrayField(TEXT("emitters"), E))
	{
		for (const TSharedPtr<FJsonValue>& V : *E) Only.Add(V->AsString());
	}
	System->WaitForCompilationComplete(false, false);
	TSharedPtr<FJsonObject> Out;
	{
		FCtx Ctx(System);
		Out = ExportSystem(Ctx, bHidden, bOnlyModified, Only);
	}
	if (bCompile) Out->SetObjectField(TEXT("compile"), CompileSystem(System, false, true));
	return Out;
}

TSharedPtr<FJsonObject> HandleApply(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("system"));
	bool bCompile = true, bDryRun = false, bAtomic = true, bSave = true, bWaitGpu = false, bIssues = true;
	Params->TryGetBoolField(TEXT("compile"), bCompile);
	Params->TryGetBoolField(TEXT("dry_run"), bDryRun);
	Params->TryGetBoolField(TEXT("atomic"), bAtomic);
	Params->TryGetBoolField(TEXT("save"), bSave);
	Params->TryGetBoolField(TEXT("wait_gpu_shaders"), bWaitGpu);
	Params->TryGetBoolField(TEXT("issues"), bIssues);
	const FString CreateFrom = Str(Params, TEXT("create_from"));

	UNiagaraSystem* System = LoadSystem(Path);
	bool bCreated = false;
	if (!System)
	{
		if (CreateFrom.IsEmpty())
		{
			return MCPError(FString::Printf(TEXT("Niagara system '%s' not found (pass create_from: \"empty\", \"template:<Name>\" e.g. "
				"template:Fountain / template:SimpleExplosion, or an existing system to copy)"), *Path));
		}
		FString Pkg, Name, Err;
		if (!MCPAuthoring::SplitAssetPath(Path, Pkg, Name, Err)) return MCPError(Err);
		UNiagaraSystem* Template = nullptr;
		UNiagaraEmitter* SeedEmitter = nullptr;
		if (!CreateFrom.Equals(TEXT("empty"), ESearchCase::IgnoreCase))
		{
			FString T = CreateFrom;
			if (T.StartsWith(TEXT("template:"), ESearchCase::IgnoreCase))
			{
				T = T.Mid(9);
				if (!T.StartsWith(TEXT("/")))
				{
					Template = LoadSystem(FString::Printf(TEXT("/Niagara/DefaultAssets/Templates/Systems/%s"), *T));
					if (!Template) SeedEmitter = LoadAs<UNiagaraEmitter>(FString::Printf(TEXT("/Niagara/DefaultAssets/Templates/Emitters/%s"), *T));
				}
				else
				{
					Template = LoadSystem(T);
				}
			}
			else
			{
				Template = LoadSystem(T);
			}
			if (!Template && !SeedEmitter)
			{
				return MCPError(FString::Printf(TEXT("create_from '%s' not found (system templates: AttributeReaderTrails, DirectionalBurst, "
					"DirectionalBurstLightweight, FountainLightweight, MinimalLightweight, RadialBurst, SimpleExplosion; emitter templates "
					"such as Fountain also work as template:Fountain; or a system path)"), *CreateFrom));
			}
		}
		FNiagaraExternalEditContext EC;
		System = UNiagaraExternalEditUtilities::CreateNiagaraSystem(Name, FPackageName::GetLongPackagePath(Pkg), Template, EC);
		if (!System)
		{
			TArray<FString> Errs;
			for (const FText& T2 : EC.Errors) Errs.Add(T2.ToString());
			return MCPError(FString::Printf(TEXT("could not create %s: %s"), *Path, *FString::Join(Errs, TEXT("; "))));
		}
		bCreated = true;
		if (SeedEmitter)
		{
			// template:<EmitterTemplate> -> a system with that one emitter, named after the template.
			FCtx Seed(System);
			FString E2;
			TSharedPtr<FJsonObject> Op = MakeShared<FJsonObject>();
			Op->SetStringField(TEXT("from"), SeedEmitter->GetPathName());
			Op->SetStringField(TEXT("name"), SeedEmitter->GetName());
			TSharedPtr<FJsonObject> R = MakeShared<FJsonObject>();
			if (!OpAddEmitter(Seed, Op, R, E2)) return MCPError(E2);
		}
	}

	TArray<TSharedPtr<FJsonValue>> Failed, Results;
	int32 Applied = 0;
	const int32 QueueBefore = (GEditor && GEditor->Trans) ? GEditor->Trans->GetQueueLength() : 0;
	System->WaitForCompilationComplete(false, false);
	{
		FCtx Ctx(System);
		FScopedTransaction Transaction(FText::FromString(FString::Printf(TEXT("MCP: niagara_apply %s"), *System->GetName())));
		System->Modify();
		const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
		if (Params->TryGetArrayField(TEXT("operations"), Ops))
		{
			for (int32 I = 0; I < Ops->Num(); ++I)
			{
				const TSharedPtr<FJsonObject> Op = (*Ops)[I]->AsObject();
				FString Err, Kind;
				if (Op.IsValid()) Op->TryGetStringField(TEXT("op"), Kind);
				TSharedPtr<FJsonObject> Res = MakeShared<FJsonObject>();
				bool bOk = false;
				if (!Op.IsValid()) Err = TEXT("operation is not an object");
				else bOk = RunOp(Ctx, Kind, Op, Res, Err);
				Res->SetNumberField(TEXT("index"), I);
				Res->SetStringField(TEXT("op"), Kind);
				Res->SetBoolField(TEXT("ok"), bOk);
				if (bOk) ++Applied;
				else
				{
					Res->SetStringField(TEXT("error"), Err);
					Failed.Add(MakeShared<FJsonValueObject>(Res));
					if (bAtomic) break; // later ops usually depend on this one; nothing will be kept anyway
				}
				Results.Add(MakeShared<FJsonValueObject>(Res));
			}
		}
		Ctx.Release();
	}
	FPropertyChangedEvent Changed(nullptr, EPropertyChangeType::ValueSet);
	System->PostEditChangeProperty(Changed);

	TSharedPtr<FJsonObject> Compile = (bCompile && (Failed.Num() == 0 || !bAtomic)) ? CompileSystem(System, bWaitGpu, bIssues) : nullptr;
	const bool bCompileOk = !Compile.IsValid() || Compile->GetBoolField(TEXT("ok"));
	const bool bRollback = bDryRun || (bAtomic && (Failed.Num() > 0 || !bCompileOk));
	bool bRolledBack = false;
	if (bRollback && !bCreated && GEditor && GEditor->Trans && GEditor->Trans->GetQueueLength() > QueueBefore)
	{
		GEditor->UndoTransaction(false);
		System->RequestCompile(false);
		System->WaitForCompilationComplete(false, false);
		bRolledBack = true;
	}
	const FString FinalPath = System->GetPathName();
	if (bRollback && bCreated)
	{
		UEditorAssetLibrary::DeleteLoadedAsset(System);
		bRolledBack = true;
	}
	bool bSaved = false;
	if (!bRollback && bSave) bSaved = MCPAuthoring::SaveAsset(System);

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("ok"), Failed.Num() == 0 && bCompileOk);
	Out->SetStringField(TEXT("system"), FinalPath);
	Out->SetBoolField(TEXT("created"), bCreated && !bRolledBack);
	Out->SetNumberField(TEXT("applied"), Applied);
	Out->SetArrayField(TEXT("failed"), Failed);
	Out->SetArrayField(TEXT("results"), Results);
	if (Compile.IsValid()) Out->SetObjectField(TEXT("compile"), Compile);
	Out->SetBoolField(TEXT("dry_run"), bDryRun);
	Out->SetBoolField(TEXT("rolled_back"), bRolledBack);
	Out->SetBoolField(TEXT("saved"), bSaved);
	return Out;
}

/** Live particle counts of every Niagara component in the PIE (default when running) or editor world. */
TSharedPtr<FJsonObject> HandleParticles(const TSharedPtr<FJsonObject>& Params)
{
	FString Where = Str(Params, TEXT("world")).ToLower();
	UWorld* World = nullptr;
	if (Where.IsEmpty()) Where = GEditor && GEditor->PlayWorld ? TEXT("pie") : TEXT("editor");
	World = Where == TEXT("pie") ? (GEditor ? GEditor->PlayWorld.Get() : nullptr) : FEpicUnrealMCPReflectionCommands::GetEditorWorld();
	if (!World) return MCPError(Where == TEXT("pie") ? TEXT("PIE is not running") : TEXT("no editor world"));
	const FString Actor = Str(Params, TEXT("actor"));
	const FString SystemFilter = Str(Params, TEXT("system"));
	TArray<TSharedPtr<FJsonValue>> Comps;
	int64 Total = 0;
	for (TObjectIterator<UNiagaraComponent> It; It; ++It)
	{
		UNiagaraComponent* C = *It;
		if (!IsValid(C) || C->GetWorld() != World) continue;
		AActor* Owner = C->GetOwner();
		const FString Label = Owner ? Owner->GetActorLabel() : FString();
		if (!Actor.IsEmpty() && !Label.Equals(Actor, ESearchCase::IgnoreCase) && !(Owner && Owner->GetName().Equals(Actor, ESearchCase::IgnoreCase)))
		{
			continue;
		}
		UNiagaraSystem* Asset = C->GetAsset();
		if (!SystemFilter.IsEmpty() && (!Asset || !Asset->GetPathName().StartsWith(SystemFilter))) continue;
		TSharedPtr<FJsonObject> CJ = MakeShared<FJsonObject>();
		CJ->SetStringField(TEXT("actor"), Label);
		CJ->SetStringField(TEXT("component"), C->GetName());
		CJ->SetStringField(TEXT("system"), Asset ? Asset->GetPathName() : FString());
		CJ->SetBoolField(TEXT("active"), C->IsActive());
		const FVector Loc = C->GetComponentLocation();
		CJ->SetField(TEXT("location"), NumArray({Loc.X, Loc.Y, Loc.Z}));
		TArray<TSharedPtr<FJsonValue>> EmittersJ;
		int64 CompTotal = 0;
		FNiagaraSystemInstanceControllerPtr Controller = C->GetSystemInstanceController();
		FNiagaraSystemInstance* Inst = Controller.IsValid() ? Controller->GetSystemInstance_Unsafe() : nullptr;
		if (Inst)
		{
			const FTransform ToWorld = C->GetComponentTransform();
			for (const FNiagaraEmitterInstanceRef& E : Inst->GetEmitters())
			{
				TSharedPtr<FJsonObject> EJ = MakeShared<FJsonObject>();
				EJ->SetStringField(TEXT("name"), E->GetEmitterHandle().GetName().ToString());
				const int32 Count = E->GetNumParticles();
				EJ->SetNumberField(TEXT("particles"), Count);
				const UEnum* StateEnum = StaticEnum<ENiagaraExecutionState>();
				EJ->SetStringField(TEXT("state"), StateEnum ? StateEnum->GetNameStringByValue((int64)E->GetExecutionState()) : TEXT(""));
				EJ->SetStringField(TEXT("sim_target"), E->GetSimTarget() == ENiagaraSimTarget::GPUComputeSim ? TEXT("gpu") : TEXT("cpu"));
				CompTotal += Count;
				if (E->GetSimTarget() == ENiagaraSimTarget::CPUSim && Count > 0)
				{
					// World-space AABB of the live particles (CPU data only).
					const FNiagaraDataSet& DS = E->GetParticleData();
					if (DS.GetCurrentData())
					{
						FNiagaraDataSetReaderFloat<FNiagaraPosition> Reader = FNiagaraDataSetAccessor<FNiagaraPosition>::CreateReader(DS, FName(TEXT("Position")));
						if (Reader.IsValid())
						{
							FNiagaraPosition Mn, Mx;
							Reader.GetMinMax(Mn, Mx);
							FVector A(Mn), B(Mx);
							FVersionedNiagaraEmitterData* ED = E->GetEmitterHandle().GetEmitterData();
							if (ED && ED->bLocalSpace)
							{
								A = ToWorld.TransformPosition(A);
								B = ToWorld.TransformPosition(B);
							}
							EJ->SetObjectField(TEXT("positions"), BoxJson(FBox(A.ComponentMin(B), A.ComponentMax(B))));
						}
					}
				}
				EmittersJ.Add(MakeShared<FJsonValueObject>(EJ));
			}
		}
		CJ->SetNumberField(TEXT("particles"), CompTotal);
		CJ->SetArrayField(TEXT("emitters"), EmittersJ);
		Total += CompTotal;
		Comps.Add(MakeShared<FJsonValueObject>(CJ));
	}
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("world"), Where);
	Out->SetNumberField(TEXT("particles"), Total);
	Out->SetArrayField(TEXT("components"), Comps);
	return Out;
}
} // namespace MCPNiagaraPriv

void FMCPNiagaraAuthoring::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("niagara_export"), &MCPNiagaraPriv::HandleExport, false);
	// niagara_apply manages its own transaction (dry runs and failures are undone as one step).
	Registry.Register(TEXT("niagara_apply"), &MCPNiagaraPriv::HandleApply, false);
	Registry.Register(TEXT("niagara_particles"), &MCPNiagaraPriv::HandleParticles, false);
}
