// data_types / data_types_export: user-defined structs and enums, curve assets and curve tables.
// DataTables (rows from JSON / CSV) and DataAssets are written by the Python side (editor_scripts/data_table.py,
// data_asset.py), which the data_types MCP tool routes to; data_types_export reads all of them.
#include "Commands/MCPAuthoringCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Curves/CurveFloat.h"
#include "Curves/CurveLinearColor.h"
#include "Curves/CurveVector.h"
#include "EdGraphSchema_K2.h"
#include "EditorAssetLibrary.h"
#include "Engine/CurveTable.h"
#include "Engine/DataAsset.h"
#include "Engine/DataTable.h"
#include "Engine/UserDefinedEnum.h"
#include "StructUtils/UserDefinedStruct.h"
#include "JsonObjectConverter.h"
#include "Kismet2/EnumEditorUtils.h"
#include "Kismet2/StructureEditorUtils.h"
#include "UObject/Package.h"
#include "UserDefinedStructure/UserDefinedStructEditorData.h"

using namespace MCPAuthoring;

namespace MCPDataTypePriv // named: unity builds share one TU with other authoring files
{
FString Str(const TSharedPtr<FJsonObject>& O, const TCHAR* F)
{
	FString V;
	O->TryGetStringField(F, V);
	return V;
}

bool Bool(const TSharedPtr<FJsonObject>& O, const TCHAR* F, bool bDefault)
{
	bool B = bDefault;
	O->TryGetBoolField(F, B);
	return B;
}

/** Struct member default from JSON: vectors / rotators / colours as Unreal text "(X=..,Y=..,Z=..)". */
FString MemberDefault(const FEdGraphPinType& T, const TSharedPtr<FJsonValue>& V)
{
	if (!V.IsValid() || V->Type == EJson::Null) return FString();
	if (V->Type == EJson::Array && T.PinCategory == UEdGraphSchema_K2::PC_Struct)
	{
		const TArray<TSharedPtr<FJsonValue>>& A = V->AsArray();
		const UObject* S = T.PinSubCategoryObject.Get();
		auto N = [&](int32 I) { return A.IsValidIndex(I) ? FString::SanitizeFloat(A[I]->AsNumber()) : FString(TEXT("0")); };
		if (S == TBaseStructure<FVector>::Get()) return FString::Printf(TEXT("(X=%s,Y=%s,Z=%s)"), *N(0), *N(1), *N(2));
		if (S == TBaseStructure<FVector2D>::Get()) return FString::Printf(TEXT("(X=%s,Y=%s)"), *N(0), *N(1));
		if (S == TBaseStructure<FRotator>::Get()) return FString::Printf(TEXT("(Pitch=%s,Yaw=%s,Roll=%s)"), *N(0), *N(1), *N(2));
		if (S == TBaseStructure<FLinearColor>::Get())
		{
			return FString::Printf(TEXT("(R=%s,G=%s,B=%s,A=%s)"), *N(0), *N(1), *N(2), A.Num() > 3 ? *N(3) : TEXT("1.0"));
		}
	}
	if (T.PinCategory == UEdGraphSchema_K2::PC_Byte && V->Type == EJson::String)
	{
		if (const UEnum* E = Cast<UEnum>(T.PinSubCategoryObject.Get())) return EnumEntryName(E, V->AsString());
	}
	return JsonToDefaultString(V);
}

FStructVariableDescription* FindMember(UUserDefinedStruct* S, const FString& Name)
{
	for (FStructVariableDescription& D : FStructureEditorUtils::GetVarDesc(S))
	{
		if (D.FriendlyName.Equals(Name, ESearchCase::IgnoreCase) || D.VarName.ToString().Equals(Name, ESearchCase::IgnoreCase)) return &D;
	}
	return nullptr;
}

UObject* CreateOrLoad(const FString& Path, UClass* Class, bool& bCreated, FString& Err)
{
	bCreated = false;
	if (UObject* Existing = LoadAssetOfClass(Path, Class)) return Existing;
	FString Pkg, Name;
	if (!SplitAssetPath(Path, Pkg, Name, Err)) return nullptr;
	if (UEditorAssetLibrary::DoesAssetExist(Pkg))
	{
		Err = FString::Printf(TEXT("%s exists but is not a %s"), *Pkg, *Class->GetName());
		return nullptr;
	}
	bCreated = true;
	return CreateAssetObject(Path, Class, Err);
}

// ---------------------------------------------------------------------------------------------
// Struct
// ---------------------------------------------------------------------------------------------

bool OpStruct(const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Out, FString& Err)
{
	const FString Path = Str(Op, TEXT("asset"));
	UUserDefinedStruct* S = Cast<UUserDefinedStruct>(LoadAssetOfClass(Path, UUserDefinedStruct::StaticClass()));
	bool bCreated = false;
	if (!S)
	{
		FString Pkg, Name;
		if (!SplitAssetPath(Path, Pkg, Name, Err)) return false;
		if (UEditorAssetLibrary::DoesAssetExist(Pkg))
		{
			Err = FString::Printf(TEXT("%s exists but is not a user-defined struct"), *Pkg);
			return false;
		}
		UPackage* Package = CreatePackage(*Pkg);
		S = FStructureEditorUtils::CreateUserDefinedStruct(Package, FName(*Name), RF_Public | RF_Standalone | RF_Transactional);
		if (!S)
		{
			Err = TEXT("struct creation failed");
			return false;
		}
		FAssetRegistryModule::AssetCreated(S);
		bCreated = true;
	}
	// The editor creates a struct with one placeholder bool member; drop it once real members exist.
	TArray<FGuid> Placeholders;
	if (bCreated)
	{
		for (const FStructVariableDescription& D : FStructureEditorUtils::GetVarDesc(S)) Placeholders.Add(D.VarGuid);
	}
	TArray<FString> Removed, Added, Changed;
	TArray<FString> RemoveNames;
	Op->TryGetStringArrayField(TEXT("remove"), RemoveNames);
	for (const FString& R : RemoveNames)
	{
		if (FStructVariableDescription* D = FindMember(S, R))
		{
			FStructureEditorUtils::RemoveVariable(S, D->VarGuid);
			Removed.Add(R);
		}
	}
	const TSharedPtr<FJsonObject>* Renames = nullptr;
	if (Op->TryGetObjectField(TEXT("rename"), Renames))
	{
		for (const auto& Pair : (*Renames)->Values)
		{
			const FString Key(*Pair.Key);
			FStructVariableDescription* D = FindMember(S, Key);
			if (!D || !FStructureEditorUtils::RenameVariable(S, D->VarGuid, Pair.Value->AsString()))
			{
				Err = FString::Printf(TEXT("cannot rename member '%s' to '%s'"), *Key, *Pair.Value->AsString());
				return false;
			}
			Changed.Add(Key + TEXT("->") + Pair.Value->AsString());
		}
	}
	const TArray<TSharedPtr<FJsonValue>>* Members = nullptr;
	if (Op->TryGetArrayField(TEXT("members"), Members))
	{
		for (const TSharedPtr<FJsonValue>& MV : *Members)
		{
			const TSharedPtr<FJsonObject> M = MV->AsObject();
			if (!M)
			{
				Err = TEXT("members are objects {name, type, default, tooltip}");
				return false;
			}
			const FString Name = Str(M, TEXT("name"));
			FEdGraphPinType T;
			if (Name.IsEmpty() || !ParsePinTypeSpec(Str(M, TEXT("type")), T, Err))
			{
				if (Err.IsEmpty()) Err = TEXT("member needs a name");
				Err = FString::Printf(TEXT("member '%s': %s"), *Name, *Err);
				return false;
			}
			FString Why;
			if (!FStructureEditorUtils::CanHaveAMemberVariableOfType(S, T, &Why))
			{
				Err = FString::Printf(TEXT("member '%s': %s"), *Name, *Why);
				return false;
			}
			FStructVariableDescription* D = FindMember(S, Name);
			if (!D)
			{
				if (!FStructureEditorUtils::AddVariable(S, T))
				{
					Err = FString::Printf(TEXT("could not add member '%s'"), *Name);
					return false;
				}
				FStructVariableDescription& New = FStructureEditorUtils::GetVarDesc(S).Last();
				const FGuid G = New.VarGuid;
				if (!FStructureEditorUtils::RenameVariable(S, G, Name))
				{
					Err = FString::Printf(TEXT("could not name member '%s' (not a valid or unique name)"), *Name);
					return false;
				}
				D = FStructureEditorUtils::GetVarDescByGuid(S, G);
				Added.Add(Name);
			}
			else if (!(D->ToPinType() == T))
			{
				FStructureEditorUtils::ChangeVariableType(S, D->VarGuid, T);
				D = FStructureEditorUtils::GetVarDescByGuid(S, D->VarGuid);
				Changed.Add(Name);
			}
			const FGuid G = D->VarGuid;
			if (M->HasField(TEXT("default")))
			{
				const FString Def = MemberDefault(T, M->TryGetField(TEXT("default")));
				if (!FStructureEditorUtils::ChangeVariableDefaultValue(S, G, Def))
				{
					const FStructVariableDescription* Now = FStructureEditorUtils::GetVarDescByGuid(S, G);
					if (!Now || Now->DefaultValue != Def)
					{
						Err = FString::Printf(TEXT("member '%s' rejected default '%s'"), *Name, *Def);
						return false;
					}
				}
			}
			const FString Tip = Str(M, TEXT("tooltip"));
			if (!Tip.IsEmpty()) FStructureEditorUtils::ChangeVariableTooltip(S, G, Tip);
			Placeholders.Remove(G);
		}
	}
	if (bCreated && FStructureEditorUtils::GetVarDesc(S).Num() > Placeholders.Num())
	{
		for (const FGuid& G : Placeholders) FStructureEditorUtils::RemoveVariable(S, G);
	}
	const FString Tip = Str(Op, TEXT("tooltip"));
	if (!Tip.IsEmpty()) FStructureEditorUtils::ChangeTooltip(S, Tip);
	FStructureEditorUtils::CompileStructure(S);
	S->MarkPackageDirty();
	Out->SetBoolField(TEXT("created"), bCreated);
	Out->SetField(TEXT("added"), StringArray(Added));
	Out->SetField(TEXT("changed"), StringArray(Changed));
	Out->SetField(TEXT("removed"), StringArray(Removed));
	Out->SetStringField(TEXT("path"), S->GetPathName());
	if (S->Status == UDSS_Error)
	{
		Err = FString::Printf(TEXT("struct does not compile: %s"), *S->ErrorMessage);
		return false;
	}
	return true;
}

TSharedPtr<FJsonObject> ExportStruct(UUserDefinedStruct* S)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("type"), TEXT("struct"));
	TArray<TSharedPtr<FJsonValue>> Members;
	for (const FStructVariableDescription& D : FStructureEditorUtils::GetVarDesc(S))
	{
		TSharedPtr<FJsonObject> M = MakeShared<FJsonObject>();
		M->SetStringField(TEXT("name"), D.FriendlyName);
		M->SetStringField(TEXT("type"), DescribePinTypeSpec(D.ToPinType()));
		M->SetStringField(TEXT("property"), D.VarName.ToString());
		M->SetStringField(TEXT("default"), D.DefaultValue);
		if (!D.ToolTip.IsEmpty()) M->SetStringField(TEXT("tooltip"), D.ToolTip);
		Members.Add(MakeShared<FJsonValueObject>(M));
	}
	J->SetArrayField(TEXT("members"), Members);
	J->SetStringField(TEXT("status"), S->Status == UDSS_Error ? TEXT("error") : S->Status == UDSS_UpToDate ? TEXT("ok") : TEXT("dirty"));
	return J;
}

// ---------------------------------------------------------------------------------------------
// Enum
// ---------------------------------------------------------------------------------------------

int32 EntryCount(const UEnum* E)
{
	return E->NumEnums() - (E->ContainsExistingMax() ? 1 : 0);
}

bool OpEnum(const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Out, FString& Err)
{
	const FString Path = Str(Op, TEXT("asset"));
	UUserDefinedEnum* E = Cast<UUserDefinedEnum>(LoadAssetOfClass(Path, UUserDefinedEnum::StaticClass()));
	bool bCreated = false;
	if (!E)
	{
		FString Pkg, Name;
		if (!SplitAssetPath(Path, Pkg, Name, Err)) return false;
		if (UEditorAssetLibrary::DoesAssetExist(Pkg))
		{
			Err = FString::Printf(TEXT("%s exists but is not a user-defined enum"), *Pkg);
			return false;
		}
		E = Cast<UUserDefinedEnum>(FEnumEditorUtils::CreateUserDefinedEnum(CreatePackage(*Pkg), FName(*Name), RF_Public | RF_Standalone | RF_Transactional));
		if (!E)
		{
			Err = TEXT("enum creation failed");
			return false;
		}
		FAssetRegistryModule::AssetCreated(E);
		bCreated = true;
	}
	const TArray<TSharedPtr<FJsonValue>>* Entries = nullptr;
	if (!Op->TryGetArrayField(TEXT("entries"), Entries))
	{
		Err = TEXT("enum needs \"entries\": [\"Common\", \"Rare\", ...] or [{\"name\":\"Epic\",\"tooltip\":\"...\"}]");
		return false;
	}
	TArray<FString> Names;
	for (const TSharedPtr<FJsonValue>& V : *Entries)
	{
		FString N;
		if (V->Type == EJson::String) N = V->AsString();
		else if (V->Type == EJson::Object) N = Str(V->AsObject(), TEXT("name"));
		if (N.IsEmpty())
		{
			Err = TEXT("enum entries need names");
			return false;
		}
		Names.Add(N);
	}
	// Entries are matched by position: entry I gets display name Names[I]. Existing values keep their internal names,
	// so Blueprints and data that use them stay valid when display names change.
	const bool bReplace = Bool(Op, TEXT("replace"), true);
	while (EntryCount(E) < Names.Num()) FEnumEditorUtils::AddNewEnumeratorForUserDefinedEnum(E);
	if (bReplace)
	{
		while (EntryCount(E) > Names.Num() && EntryCount(E) > 1) FEnumEditorUtils::RemoveEnumeratorFromUserDefinedEnum(E, EntryCount(E) - 1);
	}
	for (int32 I = 0; I < Names.Num(); ++I)
	{
		if (E->GetDisplayNameTextByIndex(I).ToString() == Names[I]) continue;
		if (!FEnumEditorUtils::IsEnumeratorDisplayNameValid(E, I, FText::FromString(Names[I])) ||
			!FEnumEditorUtils::SetEnumeratorDisplayName(E, I, FText::FromString(Names[I])))
		{
			Err = FString::Printf(TEXT("entry %d: display name '%s' is invalid or a duplicate"), I, *Names[I]);
			return false;
		}
	}
	for (int32 I = 0; I < Entries->Num(); ++I)
	{
		if ((*Entries)[I]->Type != EJson::Object) continue;
		const FString Tip = Str((*Entries)[I]->AsObject(), TEXT("tooltip"));
		if (!Tip.IsEmpty()) E->SetMetaData(TEXT("ToolTip"), *Tip, I);
	}
	E->MarkPackageDirty();
	Out->SetBoolField(TEXT("created"), bCreated);
	Out->SetStringField(TEXT("path"), E->GetPathName());
	Out->SetNumberField(TEXT("entries"), EntryCount(E));
	return true;
}

TSharedPtr<FJsonObject> ExportEnum(const UEnum* E)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("type"), TEXT("enum"));
	TArray<TSharedPtr<FJsonValue>> Entries;
	for (int32 I = 0; I < EntryCount(E); ++I)
	{
		TSharedPtr<FJsonObject> X = MakeShared<FJsonObject>();
		X->SetStringField(TEXT("name"), E->GetNameStringByIndex(I));
		X->SetStringField(TEXT("display"), E->GetDisplayNameTextByIndex(I).ToString());
		X->SetNumberField(TEXT("value"), double(E->GetValueByIndex(I)));
		Entries.Add(MakeShared<FJsonValueObject>(X));
	}
	J->SetArrayField(TEXT("entries"), Entries);
	return J;
}

// ---------------------------------------------------------------------------------------------
// Curves
// ---------------------------------------------------------------------------------------------

ERichCurveInterpMode Interp(const FString& S)
{
	const FString L = S.ToLower();
	if (L == TEXT("constant") || L == TEXT("step")) return RCIM_Constant;
	if (L == TEXT("cubic") || L == TEXT("auto") || L == TEXT("smooth")) return RCIM_Cubic;
	return RCIM_Linear;
}

bool ParseKey(const TSharedPtr<FJsonValue>& K, double& T, TArray<double>& Vals, FString& KeyInterp)
{
	TSharedPtr<FJsonValue> V;
	if (K->Type == EJson::Array && K->AsArray().Num() >= 2)
	{
		T = K->AsArray()[0]->AsNumber();
		V = K->AsArray()[1];
		if (K->AsArray().Num() > 2 && K->AsArray()[2]->Type == EJson::String) KeyInterp = K->AsArray()[2]->AsString();
	}
	else if (K->Type == EJson::Object)
	{
		const TSharedPtr<FJsonObject> O = K->AsObject();
		if (!O->TryGetNumberField(TEXT("t"), T)) O->TryGetNumberField(TEXT("time"), T);
		V = O->HasField(TEXT("v")) ? O->TryGetField(TEXT("v")) : O->TryGetField(TEXT("value"));
		O->TryGetStringField(TEXT("interp"), KeyInterp);
	}
	if (!V.IsValid()) return false;
	Vals.Reset();
	if (V->Type == EJson::Array) { for (const TSharedPtr<FJsonValue>& X : V->AsArray()) Vals.Add(X->AsNumber()); }
	else Vals.Add(V->AsNumber());
	return true;
}

bool OpCurve(const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Out, FString& Err)
{
	FString Type = Str(Op, TEXT("type")).ToLower();
	if (Type.IsEmpty()) Type = TEXT("float");
	UClass* Class = Type == TEXT("float") ? UCurveFloat::StaticClass() : Type == TEXT("vector") ? UCurveVector::StaticClass()
		: (Type == TEXT("color") || Type == TEXT("linearcolor") || Type == TEXT("colour")) ? UCurveLinearColor::StaticClass() : nullptr;
	if (!Class)
	{
		Err = FString::Printf(TEXT("curve type '%s' (float, vector, color)"), *Type);
		return false;
	}
	bool bCreated = false;
	UObject* Obj = CreateOrLoad(Str(Op, TEXT("asset")), Class, bCreated, Err);
	if (!Obj) return false;
	Obj->Modify();
	TArray<FRichCurve*> Curves;
	if (UCurveFloat* F = Cast<UCurveFloat>(Obj)) Curves = { &F->FloatCurve };
	else if (UCurveVector* V = Cast<UCurveVector>(Obj)) Curves = { &V->FloatCurves[0], &V->FloatCurves[1], &V->FloatCurves[2] };
	else if (UCurveLinearColor* C = Cast<UCurveLinearColor>(Obj)) Curves = { &C->FloatCurves[0], &C->FloatCurves[1], &C->FloatCurves[2], &C->FloatCurves[3] };
	if (Bool(Op, TEXT("replace"), true))
	{
		for (FRichCurve* C : Curves) C->Reset();
	}
	const FString DefInterp = Str(Op, TEXT("interp"));
	const TArray<TSharedPtr<FJsonValue>>* Keys = nullptr;
	int32 Count = 0;
	if (Op->TryGetArrayField(TEXT("keys"), Keys))
	{
		for (const TSharedPtr<FJsonValue>& K : *Keys)
		{
			double T = 0;
			TArray<double> Vals;
			FString KI = DefInterp;
			if (!ParseKey(K, T, Vals, KI) || (Vals.Num() != 1 && Vals.Num() < Curves.Num()))
			{
				Err = FString::Printf(TEXT("key %d: expected [time, value] with %d value(s)"), Count, Curves.Num());
				return false;
			}
			for (int32 I = 0; I < Curves.Num(); ++I)
			{
				const FKeyHandle H = Curves[I]->UpdateOrAddKey(float(T), float(Vals.Num() == 1 ? Vals[0] : Vals[I]));
				Curves[I]->SetKeyInterpMode(H, Interp(KI));
			}
			++Count;
		}
	}
	for (FRichCurve* C : Curves) C->AutoSetTangents();
	Obj->MarkPackageDirty();
	Out->SetBoolField(TEXT("created"), bCreated);
	Out->SetStringField(TEXT("path"), Obj->GetPathName());
	Out->SetNumberField(TEXT("keys"), Count);
	return true;
}

TArray<TSharedPtr<FJsonValue>> CurveKeys(const FRealCurve& C)
{
	TArray<TSharedPtr<FJsonValue>> Keys;
	for (auto It = C.GetKeyHandleIterator(); It; ++It)
	{
		const TPair<float, float> TV = C.GetKeyTimeValuePair(*It);
		Keys.Add(MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{ MakeShared<FJsonValueNumber>(TV.Key), MakeShared<FJsonValueNumber>(TV.Value) }));
	}
	return Keys;
}

TSharedPtr<FJsonObject> ExportCurve(UObject* Obj)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	TArray<const FRichCurve*> Curves;
	if (const UCurveFloat* F = Cast<UCurveFloat>(Obj)) { J->SetStringField(TEXT("type"), TEXT("curve_float")); Curves = { &F->FloatCurve }; }
	else if (const UCurveVector* V = Cast<UCurveVector>(Obj)) { J->SetStringField(TEXT("type"), TEXT("curve_vector")); Curves = { &V->FloatCurves[0], &V->FloatCurves[1], &V->FloatCurves[2] }; }
	else if (const UCurveLinearColor* C = Cast<UCurveLinearColor>(Obj))
	{
		J->SetStringField(TEXT("type"), TEXT("curve_color"));
		Curves = { &C->FloatCurves[0], &C->FloatCurves[1], &C->FloatCurves[2], &C->FloatCurves[3] };
	}
	TArray<TSharedPtr<FJsonValue>> Keys;
	if (Curves.Num())
	{
		for (auto It = Curves[0]->GetKeyHandleIterator(); It; ++It)
		{
			const float T = Curves[0]->GetKeyTime(*It);
			TSharedPtr<FJsonValue> Val;
			if (Curves.Num() == 1) Val = MakeShared<FJsonValueNumber>(Curves[0]->GetKeyValue(*It));
			else
			{
				TArray<TSharedPtr<FJsonValue>> Vs;
				for (const FRichCurve* C : Curves) Vs.Add(MakeShared<FJsonValueNumber>(C->Eval(T)));
				Val = MakeShared<FJsonValueArray>(Vs);
			}
			Keys.Add(MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{ MakeShared<FJsonValueNumber>(T), Val }));
		}
	}
	J->SetArrayField(TEXT("keys"), Keys);
	return J;
}

/** rows {"Row": [[t, v], ...]} or {"Row": {"0": v, "10": v}} -> the CSV form CreateTableFromCSVString takes. */
bool RowsToCsv(const TSharedPtr<FJsonObject>& Rows, FString& Csv, FString& Err)
{
	TArray<double> Times;
	TMap<FString, TMap<double, double>> Data;
	for (const auto& Pair : Rows->Values)
	{
		const FString Key(*Pair.Key);
		TMap<double, double>& Row = Data.Add(Key);
		if (Pair.Value->Type == EJson::Array)
		{
			for (const TSharedPtr<FJsonValue>& K : Pair.Value->AsArray())
			{
				double T = 0;
				TArray<double> V;
				FString I;
				if (!ParseKey(K, T, V, I) || V.Num() != 1)
				{
					Err = FString::Printf(TEXT("row %s: keys are [time, value]"), *Key);
					return false;
				}
				Row.Add(T, V[0]);
				Times.AddUnique(T);
			}
		}
		else if (Pair.Value->Type == EJson::Object)
		{
			for (const auto& KV : Pair.Value->AsObject()->Values)
			{
				const double T = FCString::Atod(*KV.Key);
				Row.Add(T, KV.Value->AsNumber());
				Times.AddUnique(T);
			}
		}
	}
	Times.Sort();
	Csv = TEXT("Name");
	for (double T : Times) Csv += TEXT(",") + FString::SanitizeFloat(T);
	Csv += TEXT("\n");
	for (const auto& Pair : Data)
	{
		Csv += Pair.Key;
		for (double T : Times)
		{
			const double* V = Pair.Value.Find(T);
			if (!V)
			{
				Err = FString::Printf(TEXT("row %s has no value at time %g (every row needs a key at every time)"), *Pair.Key, T);
				return false;
			}
			Csv += TEXT(",") + FString::SanitizeFloat(*V);
		}
		Csv += TEXT("\n");
	}
	return true;
}

bool OpCurveTable(const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Out, FString& Err)
{
	bool bCreated = false;
	UCurveTable* CT = Cast<UCurveTable>(CreateOrLoad(Str(Op, TEXT("asset")), UCurveTable::StaticClass(), bCreated, Err));
	if (!CT) return false;
	FString Csv = Str(Op, TEXT("csv"));
	const FString Json = Str(Op, TEXT("json"));
	const TSharedPtr<FJsonObject>* Rows = nullptr;
	if (Csv.IsEmpty() && Json.IsEmpty())
	{
		if (!Op->TryGetObjectField(TEXT("rows"), Rows))
		{
			Err = TEXT("curve_table needs csv (\"Name,0,10\\nDamage,5,50\"), json, or rows {\"Damage\": [[0,5],[10,50]]}");
			return false;
		}
		if (!RowsToCsv(*Rows, Csv, Err)) return false;
	}
	CT->Modify();
	const ERichCurveInterpMode Mode = Interp(Str(Op, TEXT("interp")));
	const TArray<FString> Problems = Csv.IsEmpty() ? CT->CreateTableFromJSONString(Json, Mode) : CT->CreateTableFromCSVString(Csv, Mode);
	if (Problems.Num())
	{
		Err = FString::Join(Problems, TEXT("; "));
		return false;
	}
	CT->MarkPackageDirty();
	Out->SetBoolField(TEXT("created"), bCreated);
	Out->SetStringField(TEXT("path"), CT->GetPathName());
	Out->SetNumberField(TEXT("rows"), CT->GetRowMap().Num());
	return true;
}

TSharedPtr<FJsonObject> ExportCurveTable(UCurveTable* CT)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("type"), TEXT("curve_table"));
	TSharedPtr<FJsonObject> Rows = MakeShared<FJsonObject>();
	for (const TPair<FName, FRealCurve*>& Pair : CT->GetRowMap())
	{
		if (Pair.Value) Rows->SetArrayField(Pair.Key.ToString(), CurveKeys(*Pair.Value));
	}
	J->SetObjectField(TEXT("rows"), Rows);
	return J;
}

// ---------------------------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------------------------

TSharedPtr<FJsonObject> HandleDataTypes(const TSharedPtr<FJsonObject>& Params)
{
	const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
	if (!Params->TryGetArrayField(TEXT("operations"), Ops))
	{
		return MCPError(TEXT("operations: [{\"op\":\"struct\"|\"enum\"|\"curve\"|\"curve_table\", ...}]"));
	}
	bool bSave = true, bStop = true;
	Params->TryGetBoolField(TEXT("save"), bSave);
	Params->TryGetBoolField(TEXT("stop_on_error"), bStop);
	TArray<TSharedPtr<FJsonValue>> Results;
	int32 Failed = 0;
	for (int32 I = 0; I < Ops->Num(); ++I)
	{
		const TSharedPtr<FJsonObject> Op = (*Ops)[I]->AsObject();
		TSharedPtr<FJsonObject> R = MakeShared<FJsonObject>();
		R->SetNumberField(TEXT("index"), I);
		FString Kind = Op.IsValid() ? Str(Op, TEXT("op")) : FString();
		R->SetStringField(TEXT("op"), Kind);
		FString Err;
		bool bOk = false;
		if (!Op.IsValid()) Err = TEXT("operation must be an object");
		else if (Kind == TEXT("struct")) bOk = OpStruct(Op, R, Err);
		else if (Kind == TEXT("enum")) bOk = OpEnum(Op, R, Err);
		else if (Kind == TEXT("curve")) bOk = OpCurve(Op, R, Err);
		else if (Kind == TEXT("curve_table")) bOk = OpCurveTable(Op, R, Err);
		else Err = FString::Printf(TEXT("unknown op '%s' (struct, enum, curve, curve_table)"), *Kind);
		if (bOk && bSave)
		{
			FString P;
			R->TryGetStringField(TEXT("path"), P);
			if (UObject* A = P.IsEmpty() ? nullptr : StaticLoadObject(UObject::StaticClass(), nullptr, *P)) R->SetBoolField(TEXT("saved"), SaveAsset(A));
		}
		R->SetBoolField(TEXT("ok"), bOk);
		if (!bOk)
		{
			R->SetStringField(TEXT("error"), Err);
			++Failed;
		}
		Results.Add(MakeShared<FJsonValueObject>(R));
		if (!bOk && bStop) break;
	}
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("ok"), Failed == 0);
	Out->SetNumberField(TEXT("failed"), Failed);
	Out->SetArrayField(TEXT("results"), Results);
	return Out;
}

TSharedPtr<FJsonObject> HandleDataTypesExport(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("asset"));
	UObject* Obj = LoadAssetOfClass(Path, UObject::StaticClass());
	if (!Obj)
	{
		return MCPError(FString::Printf(TEXT("asset '%s' not found"), *Path));
	}
	TSharedPtr<FJsonObject> J;
	if (UUserDefinedStruct* S = Cast<UUserDefinedStruct>(Obj)) J = ExportStruct(S);
	else if (UUserDefinedEnum* E = Cast<UUserDefinedEnum>(Obj)) J = ExportEnum(E);
	else if (Obj->IsA<UCurveFloat>() || Obj->IsA<UCurveVector>() || Obj->IsA<UCurveLinearColor>()) J = ExportCurve(Obj);
	else if (UCurveTable* CT = Cast<UCurveTable>(Obj)) J = ExportCurveTable(CT);
	else if (UDataTable* DT = Cast<UDataTable>(Obj))
	{
		J = MakeShared<FJsonObject>();
		J->SetStringField(TEXT("type"), TEXT("data_table"));
		J->SetStringField(TEXT("row_struct"), DT->GetRowStruct() ? DT->GetRowStruct()->GetPathName() : FString());
		TArray<TSharedPtr<FJsonValue>> Rows;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(DT->GetTableAsJSON(EDataTableExportFlags::UseJsonObjectsForStructs));
		FJsonSerializer::Deserialize(Reader, Rows);
		J->SetArrayField(TEXT("rows"), Rows);
		J->SetNumberField(TEXT("row_count"), DT->GetRowMap().Num());
	}
	else if (UDataAsset* DA = Cast<UDataAsset>(Obj))
	{
		J = MakeShared<FJsonObject>();
		J->SetStringField(TEXT("type"), Obj->IsA<UPrimaryDataAsset>() ? TEXT("primary_data_asset") : TEXT("data_asset"));
		J->SetStringField(TEXT("class"), DA->GetClass()->GetPathName());
		TSharedPtr<FJsonObject> Props = MakeShared<FJsonObject>();
		// Only the properties the asset's own class (and Blueprint parents below UDataAsset) declare.
		for (TFieldIterator<FProperty> It(DA->GetClass()); It; ++It)
		{
			if (It->GetOwnerClass() && It->GetOwnerClass()->IsChildOf(UDataAsset::StaticClass()) && It->GetOwnerClass() != UDataAsset::StaticClass()
				&& It->GetOwnerClass() != UPrimaryDataAsset::StaticClass() && It->HasAnyPropertyFlags(CPF_Edit))
			{
				if (TSharedPtr<FJsonValue> V = FJsonObjectConverter::UPropertyToJsonValue(*It, It->ContainerPtrToValuePtr<void>(DA)))
				{
					Props->SetField(It->GetAuthoredName(), V);
				}
			}
		}
		J->SetObjectField(TEXT("properties"), Props);
	}
	else
	{
		return MCPError(FString::Printf(TEXT("%s is a %s; data_types_export reads structs, enums, curves, curve tables, DataTables and DataAssets"),
			*Path, *Obj->GetClass()->GetName()));
	}
	J->SetStringField(TEXT("path"), Obj->GetPathName());
	return J;
}
} // namespace MCPDataTypePriv

void FMCPDataTypeAuthoring::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("data_types"), &MCPDataTypePriv::HandleDataTypes, false);
	Registry.Register(TEXT("data_types_export"), &MCPDataTypePriv::HandleDataTypesExport, false);
}
