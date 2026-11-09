// bp_export / bp_apply: Blueprints as a JSON graph spec, edited by batched ops in one undo step.
#include "Commands/MCPAuthoringCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "EditorAssetLibrary.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "GameFramework/Actor.h"
#include "K2Node_CallArrayFunction.h"
#include "K2Node_CallDataTableFunction.h"
#include "K2Node_CallFunction.h"
#include "K2Node_CommutativeAssociativeBinaryOperator.h"
#include "K2Node_ComponentBoundEvent.h"
#include "Components/Widget.h"
#include "K2Node_CustomEvent.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_Event.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
#include "K2Node_GetArrayItem.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_Self.h"
#include "K2Node_SpawnActorFromClass.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetArrayLibrary.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetStringLibrary.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Kismet/KismetTextLibrary.h"
#include "Blueprint/UserWidget.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/CompilerResultsLog.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Logging/TokenizedMessage.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"

// ---------------------------------------------------------------------------------------------
// Shared authoring helpers
// ---------------------------------------------------------------------------------------------

namespace MCPAuthoring
{
bool SplitAssetPath(const FString& InPath, FString& OutPackage, FString& OutName, FString& OutError)
{
	FString Path = InPath.TrimStartAndEnd();
	if (Path.Contains(TEXT(".")))
	{
		Path = FPackageName::ObjectPathToPackageName(Path);
	}
	if (!Path.StartsWith(TEXT("/")) || !FPackageName::IsValidLongPackageName(Path))
	{
		OutError = FString::Printf(TEXT("'%s' is not a valid asset path (expected /Game/Folder/Name)"), *InPath);
		return false;
	}
	OutPackage = Path;
	OutName = FPackageName::GetLongPackageAssetName(Path);
	return true;
}

UObject* LoadAssetOfClass(const FString& InPath, UClass* Class)
{
	FString Pkg, Name, Err;
	if (!SplitAssetPath(InPath, Pkg, Name, Err))
	{
		return nullptr;
	}
	UObject* Obj = StaticLoadObject(Class, nullptr, *FString::Printf(TEXT("%s.%s"), *Pkg, *Name), nullptr, LOAD_NoWarn | LOAD_Quiet);
	return (Obj && Obj->IsA(Class)) ? Obj : nullptr;
}

UObject* CreateAssetObject(const FString& InPath, UClass* Class, FString& OutError)
{
	FString Pkg, Name;
	if (!SplitAssetPath(InPath, Pkg, Name, OutError))
	{
		return nullptr;
	}
	if (UEditorAssetLibrary::DoesAssetExist(Pkg))
	{
		OutError = FString::Printf(TEXT("%s already exists"), *Pkg);
		return nullptr;
	}
	UPackage* Package = CreatePackage(*Pkg);
	Package->FullyLoad();
	UObject* Obj = NewObject<UObject>(Package, Class, FName(*Name), RF_Public | RF_Standalone | RF_Transactional);
	FAssetRegistryModule::AssetCreated(Obj);
	Obj->MarkPackageDirty();
	return Obj;
}

bool SaveAsset(UObject* Asset)
{
	return Asset && UEditorAssetLibrary::SaveLoadedAsset(Asset, false);
}

FString JsonToDefaultString(const TSharedPtr<FJsonValue>& Value)
{
	if (!Value.IsValid())
	{
		return FString();
	}
	switch (Value->Type)
	{
	case EJson::Boolean:
		return Value->AsBool() ? TEXT("true") : TEXT("false");
	case EJson::Number:
	{
		const double D = Value->AsNumber();
		return FMath::IsNearlyEqual(D, FMath::RoundToDouble(D)) && FMath::Abs(D) < 1e15
			? FString::Printf(TEXT("%lld"), (long long)FMath::RoundToDouble(D)) : FString::SanitizeFloat(D);
	}
	case EJson::Array:
	{
		TArray<FString> Parts;
		for (const TSharedPtr<FJsonValue>& V : Value->AsArray())
		{
			Parts.Add(JsonToDefaultString(V));
		}
		return FString::Join(Parts, TEXT(","));
	}
	case EJson::String:
		return Value->AsString();
	default:
		return FString();
	}
}

TSharedPtr<FJsonValue> StringArray(const TArray<FString>& Items)
{
	TArray<TSharedPtr<FJsonValue>> Out;
	for (const FString& S : Items)
	{
		Out.Add(MakeShared<FJsonValueString>(S));
	}
	return MakeShared<FJsonValueArray>(Out);
}
} // namespace MCPAuthoring

using namespace MCPAuthoring;

// ---------------------------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------------------------

static FString Norm(const FString& S)
{
	return S.Replace(TEXT(" "), TEXT("")).Replace(TEXT("_"), TEXT("")).ToLower();
}

static bool ParsePinType(const FString& In, FEdGraphPinType& Out, FString& Err)
{
	Out = FEdGraphPinType();
	FString S = In.TrimStartAndEnd();
	if (S.EndsWith(TEXT("[]")))
	{
		Out.ContainerType = EPinContainerType::Array;
		S.LeftChopInline(2);
	}
	else if (S.StartsWith(TEXT("array:"), ESearchCase::IgnoreCase))
	{
		Out.ContainerType = EPinContainerType::Array;
		S.RightChopInline(6);
	}
	FString Head = S, Arg;
	S.Split(TEXT(":"), &Head, &Arg);
	const FString H = Head.ToLower();
	auto Struct = [&](UScriptStruct* SS) { Out.PinCategory = UEdGraphSchema_K2::PC_Struct; Out.PinSubCategoryObject = SS; return true; };
	if (H == TEXT("bool") || H == TEXT("boolean")) { Out.PinCategory = UEdGraphSchema_K2::PC_Boolean; return true; }
	if (H == TEXT("int") || H == TEXT("integer") || H == TEXT("int32")) { Out.PinCategory = UEdGraphSchema_K2::PC_Int; return true; }
	if (H == TEXT("int64")) { Out.PinCategory = UEdGraphSchema_K2::PC_Int64; return true; }
	if (H == TEXT("float") || H == TEXT("double") || H == TEXT("real") || H == TEXT("number"))
	{
		Out.PinCategory = UEdGraphSchema_K2::PC_Real;
		Out.PinSubCategory = UEdGraphSchema_K2::PC_Double;
		return true;
	}
	if (H == TEXT("string")) { Out.PinCategory = UEdGraphSchema_K2::PC_String; return true; }
	if (H == TEXT("name")) { Out.PinCategory = UEdGraphSchema_K2::PC_Name; return true; }
	if (H == TEXT("text")) { Out.PinCategory = UEdGraphSchema_K2::PC_Text; return true; }
	if (H == TEXT("byte")) { Out.PinCategory = UEdGraphSchema_K2::PC_Byte; return true; }
	if (H == TEXT("vector")) return Struct(TBaseStructure<FVector>::Get());
	if (H == TEXT("vector2d")) return Struct(TBaseStructure<FVector2D>::Get());
	if (H == TEXT("rotator")) return Struct(TBaseStructure<FRotator>::Get());
	if (H == TEXT("transform")) return Struct(TBaseStructure<FTransform>::Get());
	if (H == TEXT("linearcolor") || H == TEXT("color")) return Struct(TBaseStructure<FLinearColor>::Get());
	if (H == TEXT("struct"))
	{
		UScriptStruct* SS = LoadObject<UScriptStruct>(nullptr, *Arg, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!SS)
		{
			for (TObjectIterator<UScriptStruct> It; It; ++It)
			{
				if (It->GetName().Equals(Arg, ESearchCase::IgnoreCase)) { SS = *It; break; }
			}
		}
		if (!SS) { Err = FString::Printf(TEXT("struct '%s' not found"), *Arg); return false; }
		return Struct(SS);
	}
	if (H == TEXT("enum"))
	{
		UEnum* E = LoadObject<UEnum>(nullptr, *Arg, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!E)
		{
			for (TObjectIterator<UEnum> It; It; ++It)
			{
				if (It->GetName().Equals(Arg, ESearchCase::IgnoreCase)) { E = *It; break; }
			}
		}
		if (!E) { Err = FString::Printf(TEXT("enum '%s' not found"), *Arg); return false; }
		Out.PinCategory = UEdGraphSchema_K2::PC_Byte;
		Out.PinSubCategoryObject = E;
		return true;
	}
	const bool bObjectLike = H == TEXT("object") || H == TEXT("class") || H == TEXT("softobject") || H == TEXT("softclass");
	const FString ClassName = bObjectLike ? (Arg.IsEmpty() ? FString(TEXT("Object")) : Arg) : Head;
	if (UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(ClassName))
	{
		Out.PinCategory = H == TEXT("class") ? UEdGraphSchema_K2::PC_Class
			: H == TEXT("softobject") ? UEdGraphSchema_K2::PC_SoftObject
			: H == TEXT("softclass") ? UEdGraphSchema_K2::PC_SoftClass
			: UEdGraphSchema_K2::PC_Object;
		Out.PinSubCategoryObject = C;
		return true;
	}
	Err = FString::Printf(TEXT("unknown type '%s' (bool, int, float, string, name, text, vector, rotator, transform, "
		"object:<Class>, class:<Class>, struct:<Struct>, enum:<Enum>, append [] for arrays)"), *In);
	return false;
}

static FString DescribePinType(const FEdGraphPinType& T)
{
	FString S = T.PinCategory.ToString();
	if (T.PinCategory == UEdGraphSchema_K2::PC_Real) S = TEXT("float");
	if (UObject* Sub = T.PinSubCategoryObject.Get())
	{
		S += TEXT(":") + Sub->GetName();
	}
	if (T.ContainerType == EPinContainerType::Array) S += TEXT("[]");
	else if (T.ContainerType == EPinContainerType::Set) S = TEXT("set<") + S + TEXT(">");
	else if (T.ContainerType == EPinContainerType::Map) S = TEXT("map<") + S + TEXT(">");
	return S;
}

// ---------------------------------------------------------------------------------------------
// Graph / node / pin lookup
// ---------------------------------------------------------------------------------------------

static FString NodeId(const UEdGraphNode* N)
{
	return N->NodeGuid.ToString(EGuidFormats::Digits);
}

static void AllGraphs(UBlueprint* BP, TArray<UEdGraph*>& Out)
{
	BP->GetAllGraphs(Out);
}

static UEdGraph* FindGraph(UBlueprint* BP, const FString& Name)
{
	if (Name.IsEmpty() || Name.Equals(TEXT("EventGraph"), ESearchCase::IgnoreCase))
	{
		if (UEdGraph* G = FBlueprintEditorUtils::FindEventGraph(BP))
		{
			return G;
		}
	}
	for (UEdGraph* G : BP->UbergraphPages) { if (G && G->GetName().Equals(Name, ESearchCase::IgnoreCase)) return G; }
	for (UEdGraph* G : BP->FunctionGraphs) { if (G && G->GetName().Equals(Name, ESearchCase::IgnoreCase)) return G; }
	for (UEdGraph* G : BP->MacroGraphs) { if (G && G->GetName().Equals(Name, ESearchCase::IgnoreCase)) return G; }
	return nullptr;
}

static UEdGraphNode* FindNodeById(UBlueprint* BP, const FString& Id)
{
	TArray<UEdGraph*> Graphs;
	AllGraphs(BP, Graphs);
	for (UEdGraph* G : Graphs)
	{
		for (UEdGraphNode* N : G->Nodes)
		{
			if (N && (NodeId(N).Equals(Id, ESearchCase::IgnoreCase) || (Id.Len() >= 8 && NodeId(N).StartsWith(Id, ESearchCase::IgnoreCase))))
			{
				return N;
			}
		}
	}
	return nullptr;
}

static FString PinListing(UEdGraphNode* N)
{
	TArray<FString> Names;
	for (UEdGraphPin* P : N->Pins)
	{
		if (P && !P->bHidden)
		{
			Names.Add(FString::Printf(TEXT("%s(%s)"), *P->PinName.ToString(), P->Direction == EGPD_Input ? TEXT("in") : TEXT("out")));
		}
	}
	return FString::Join(Names, TEXT(", "));
}

static UEdGraphPin* FindPinExact(UEdGraphNode* N, const FString& Want, EEdGraphPinDirection Dir)
{
	auto DirOk = [Dir](const UEdGraphPin* P) { return Dir == EGPD_MAX || P->Direction == Dir; };
	for (UEdGraphPin* P : N->Pins)
	{
		if (P && DirOk(P) && Norm(P->PinName.ToString()) == Want) return P;
	}
	for (UEdGraphPin* P : N->Pins)
	{
		if (P && DirOk(P) && (Norm(P->GetDisplayName().ToString()) == Want || Norm(P->PinFriendlyName.ToString()) == Want)) return P;
	}
	return nullptr;
}

static UEdGraphPin* FindPin(UEdGraphNode* N, const FString& InName, EEdGraphPinDirection Dir)
{
	const FString Want = Norm(InName);
	if (UEdGraphPin* P = FindPinExact(N, Want, Dir))
	{
		return P; // a real pin named e.g. "Target" wins over the alias below
	}
	// Set-variable nodes output the new value on a pin named "Output_Get": accept the variable's name.
	if (const UK2Node_VariableSet* VS = Cast<UK2Node_VariableSet>(N);
		VS && Dir != EGPD_Input && Want == Norm(VS->VariableReference.GetMemberName().ToString()))
	{
		if (UEdGraphPin* P = FindPinExact(N, TEXT("outputget"), EGPD_Output)) return P;
	}
	// Cast result pins are named after the class ("AsBP Arena Game Mode"): accept as/result too.
	if (UK2Node_DynamicCast* DC = Cast<UK2Node_DynamicCast>(N);
		DC && Dir != EGPD_Input && (Want == TEXT("as") || Want == TEXT("result") || Want == TEXT("castresult")))
	{
		return DC->GetCastResultPin();
	}
	// The "Get" array node's pins are Array / Dimension 1 / Output; accept Array_Get's parameter names.
	if (Cast<UK2Node_GetArrayItem>(N))
	{
		const TCHAR* ArrayAlias = Want == TEXT("targetarray") ? TEXT("array")
			: (Want == TEXT("index") ? TEXT("dimension1") : ((Want == TEXT("item") || Want == TEXT("returnvalue")) ? TEXT("output") : nullptr));
		if (ArrayAlias) return FindPinExact(N, ArrayAlias, Dir);
	}
	// Friendly aliases for exec and common pins.
	FString Alias;
	if (Want == TEXT("exec") || Want == TEXT("execute") || Want == TEXT("in")) Alias = Norm(UEdGraphSchema_K2::PN_Execute.ToString());
	else if (Want == TEXT("out")) Alias = Norm(UEdGraphSchema_K2::PN_Then.ToString());
	else if (Want == TEXT("return")) Alias = TEXT("returnvalue");
	else if (Want == TEXT("target")) Alias = TEXT("self");
	return Alias.IsEmpty() ? nullptr : FindPinExact(N, Alias, Dir);
}

static FString PinDefault(const UEdGraphPin* P)
{
	if (P->DefaultObject) return P->DefaultObject->GetPathName();
	if (!P->DefaultTextValue.IsEmpty()) return P->DefaultTextValue.ToString();
	return P->DefaultValue;
}

// ---------------------------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------------------------

static TSharedPtr<FJsonObject> NodeSpec(UEdGraphNode* N)
{
	TSharedPtr<FJsonObject> S = MakeShared<FJsonObject>();
	if (const UK2Node_ComponentBoundEvent* CE = Cast<UK2Node_ComponentBoundEvent>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("component_event"));
		S->SetStringField(TEXT("component"), CE->ComponentPropertyName.ToString());
		S->SetStringField(TEXT("event"), CE->DelegatePropertyName.ToString());
	}
	else if (const UK2Node_CustomEvent* CU = Cast<UK2Node_CustomEvent>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("custom_event"));
		S->SetStringField(TEXT("name"), CU->CustomFunctionName.ToString());
	}
	else if (const UK2Node_Event* EV = Cast<UK2Node_Event>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("event"));
		S->SetStringField(TEXT("event"), EV->EventReference.GetMemberName().ToString());
	}
	else if (const UK2Node_CallFunction* CF = Cast<UK2Node_CallFunction>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("call_function"));
		S->SetStringField(TEXT("function"), CF->FunctionReference.GetMemberName().ToString());
		if (UClass* Owner = CF->FunctionReference.GetMemberParentClass())
		{
			S->SetStringField(TEXT("class"), Owner->GetName());
		}
	}
	else if (const UK2Node_VariableSet* VS = Cast<UK2Node_VariableSet>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("set_var"));
		S->SetStringField(TEXT("variable"), VS->VariableReference.GetMemberName().ToString());
	}
	else if (const UK2Node_VariableGet* VG = Cast<UK2Node_VariableGet>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("get_var"));
		S->SetStringField(TEXT("variable"), VG->VariableReference.GetMemberName().ToString());
	}
	else if (Cast<UK2Node_IfThenElse>(N)) S->SetStringField(TEXT("kind"), TEXT("branch"));
	else if (Cast<UK2Node_ExecutionSequence>(N)) S->SetStringField(TEXT("kind"), TEXT("sequence"));
	else if (const UK2Node_DynamicCast* DC = Cast<UK2Node_DynamicCast>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("cast"));
		if (DC->TargetType) S->SetStringField(TEXT("class"), DC->TargetType->GetPathName());
	}
	else if (Cast<UK2Node_Self>(N)) S->SetStringField(TEXT("kind"), TEXT("self"));
	else if (const UK2Node_MacroInstance* MI = Cast<UK2Node_MacroInstance>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("macro"));
		if (UEdGraph* MG = MI->GetMacroGraph()) S->SetStringField(TEXT("macro"), MG->GetName());
	}
	else if (Cast<UK2Node_SpawnActorFromClass>(N)) S->SetStringField(TEXT("kind"), TEXT("spawn_actor"));
	else if (Cast<UK2Node_FunctionEntry>(N)) S->SetStringField(TEXT("kind"), TEXT("function_entry"));
	else if (Cast<UK2Node_FunctionResult>(N)) S->SetStringField(TEXT("kind"), TEXT("function_result"));
	else
	{
		S->SetStringField(TEXT("kind"), TEXT("node"));
		S->SetStringField(TEXT("class"), N->GetClass()->GetName());
	}
	return S;
}

static TSharedPtr<FJsonObject> ExportNode(UEdGraphNode* N, bool bPins)
{
	TSharedPtr<FJsonObject> J = NodeSpec(N);
	J->SetStringField(TEXT("id"), NodeId(N));
	J->SetStringField(TEXT("title"), N->GetNodeTitle(ENodeTitleType::ListView).ToString());
	J->SetNumberField(TEXT("x"), N->NodePosX);
	J->SetNumberField(TEXT("y"), N->NodePosY);
	if (N->GetDesiredEnabledState() == ENodeEnabledState::Disabled)
	{
		J->SetBoolField(TEXT("disabled"), true);
	}
	if (!bPins)
	{
		return J;
	}
	TArray<TSharedPtr<FJsonValue>> Pins;
	for (UEdGraphPin* P : N->Pins)
	{
		if (!P || P->bHidden)
		{
			continue;
		}
		TSharedPtr<FJsonObject> PJ = MakeShared<FJsonObject>();
		PJ->SetStringField(TEXT("name"), P->PinName.ToString());
		const FString Label = P->GetDisplayName().ToString();
		if (!Label.IsEmpty() && Norm(Label) != Norm(P->PinName.ToString()))
		{
			PJ->SetStringField(TEXT("label"), Label);
		}
		PJ->SetStringField(TEXT("dir"), P->Direction == EGPD_Input ? TEXT("in") : TEXT("out"));
		PJ->SetStringField(TEXT("type"), P->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec ? FString(TEXT("exec")) : DescribePinType(P->PinType));
		const FString Def = PinDefault(P);
		if (!Def.IsEmpty() && P->LinkedTo.Num() == 0 && P->Direction == EGPD_Input)
		{
			PJ->SetStringField(TEXT("default"), Def);
		}
		TArray<FString> Links;
		for (UEdGraphPin* L : P->LinkedTo)
		{
			if (L && L->GetOwningNode())
			{
				Links.Add(NodeId(L->GetOwningNode()) + TEXT(".") + L->PinName.ToString());
			}
		}
		if (Links.Num())
		{
			PJ->SetField(TEXT("links"), StringArray(Links));
		}
		Pins.Add(MakeShared<FJsonValueObject>(PJ));
	}
	J->SetArrayField(TEXT("pins"), Pins);
	return J;
}

FString MCPAuthoring::BlueprintStatusName(EBlueprintStatus S)
{
	switch (S)
	{
	case BS_UpToDate: return TEXT("ok");
	case BS_UpToDateWithWarnings: return TEXT("ok_with_warnings");
	case BS_Error: return TEXT("error");
	case BS_Dirty: return TEXT("dirty");
	case BS_BeingCreated: return TEXT("being_created");
	default: return TEXT("unknown");
	}
}

static TSharedPtr<FJsonObject> ExportBlueprint(UBlueprint* BP, const TArray<FString>& OnlyGraphs, bool bPins)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("path"), BP->GetPathName());
	J->SetStringField(TEXT("parent_class"), BP->ParentClass ? BP->ParentClass->GetPathName() : FString());
	if (BP->GeneratedClass) J->SetStringField(TEXT("generated_class"), BP->GeneratedClass->GetPathName());
	J->SetStringField(TEXT("status"), BlueprintStatusName(BP->Status));

	TArray<TSharedPtr<FJsonValue>> Vars;
	UObject* CDO = BP->GeneratedClass ? BP->GeneratedClass->GetDefaultObject(false) : nullptr;
	for (const FBPVariableDescription& V : BP->NewVariables)
	{
		TSharedPtr<FJsonObject> VJ = MakeShared<FJsonObject>();
		VJ->SetStringField(TEXT("name"), V.VarName.ToString());
		VJ->SetStringField(TEXT("type"), DescribePinType(V.VarType));
		VJ->SetBoolField(TEXT("instance_editable"), !(V.PropertyFlags & CPF_DisableEditOnInstance));
		if (!V.Category.IsEmpty()) VJ->SetStringField(TEXT("category"), V.Category.ToString());
		FString Err;
		if (CDO)
		{
			if (TSharedPtr<FJsonValue> Val = FEpicUnrealMCPReflectionCommands::GetPropertyByPath(CDO, V.VarName.ToString(), Err))
			{
				VJ->SetField(TEXT("value"), Val);
			}
		}
		Vars.Add(MakeShared<FJsonValueObject>(VJ));
	}
	J->SetArrayField(TEXT("variables"), Vars);

	TArray<TSharedPtr<FJsonValue>> Comps;
	if (BP->SimpleConstructionScript)
	{
		for (USCS_Node* SN : BP->SimpleConstructionScript->GetAllNodes())
		{
			if (!SN) continue;
			TSharedPtr<FJsonObject> CJ = MakeShared<FJsonObject>();
			CJ->SetStringField(TEXT("name"), SN->GetVariableName().ToString());
			CJ->SetStringField(TEXT("class"), SN->ComponentClass ? SN->ComponentClass->GetName() : FString());
			FString Parent = SN->ParentComponentOrVariableName.ToString();
			for (USCS_Node* Other : BP->SimpleConstructionScript->GetAllNodes())
			{
				if (Other && Other->GetChildNodes().Contains(SN)) Parent = Other->GetVariableName().ToString();
			}
			if (!Parent.IsEmpty() && Parent != TEXT("None")) CJ->SetStringField(TEXT("parent"), Parent);
			Comps.Add(MakeShared<FJsonValueObject>(CJ));
		}
	}
	J->SetArrayField(TEXT("components"), Comps);

	TArray<TSharedPtr<FJsonValue>> Graphs;
	auto AddGraph = [&](UEdGraph* G, const TCHAR* Kind)
	{
		if (!G || (OnlyGraphs.Num() && !OnlyGraphs.ContainsByPredicate([G](const FString& N) { return N.Equals(G->GetName(), ESearchCase::IgnoreCase); })))
		{
			return;
		}
		TSharedPtr<FJsonObject> GJ = MakeShared<FJsonObject>();
		GJ->SetStringField(TEXT("name"), G->GetName());
		GJ->SetStringField(TEXT("kind"), Kind);
		TArray<TSharedPtr<FJsonValue>> Nodes;
		for (UEdGraphNode* N : G->Nodes)
		{
			if (N) Nodes.Add(MakeShared<FJsonValueObject>(ExportNode(N, bPins)));
		}
		GJ->SetArrayField(TEXT("nodes"), Nodes);
		Graphs.Add(MakeShared<FJsonValueObject>(GJ));
	};
	for (UEdGraph* G : BP->UbergraphPages) AddGraph(G, TEXT("event_graph"));
	for (UEdGraph* G : BP->FunctionGraphs) AddGraph(G, TEXT("function"));
	for (UEdGraph* G : BP->MacroGraphs) AddGraph(G, TEXT("macro"));
	J->SetArrayField(TEXT("graphs"), Graphs);
	return J;
}

static TSharedPtr<FJsonObject> ObjField(const TSharedPtr<FJsonObject>& O, const TCHAR* Name)
{
	const TSharedPtr<FJsonObject>* V = nullptr;
	return O->TryGetObjectField(Name, V) ? *V : TSharedPtr<FJsonObject>();
}

static UBlueprint* LoadBlueprint(const FString& Path)
{
	return Cast<UBlueprint>(LoadAssetOfClass(Path, UBlueprint::StaticClass()));
}

static TSharedPtr<FJsonObject> HandleBpExport(const TSharedPtr<FJsonObject>& Params)
{
	FString Path;
	Params->TryGetStringField(TEXT("blueprint"), Path);
	UBlueprint* BP = LoadBlueprint(Path);
	if (!BP)
	{
		return MCPError(FString::Printf(TEXT("Blueprint '%s' not found"), *Path));
	}
	TArray<FString> Only;
	Params->TryGetStringArrayField(TEXT("graphs"), Only);
	bool bPins = true;
	Params->TryGetBoolField(TEXT("include_pins"), bPins);
	return ExportBlueprint(BP, Only, bPins);
}

// ---------------------------------------------------------------------------------------------
// Apply
// ---------------------------------------------------------------------------------------------

using FApplyContext = MCPAuthoring::FGraphApplyContext;

template <typename T>
static T* NewNode(UEdGraph* G, TFunctionRef<void(T*)> Configure)
{
	FGraphNodeCreator<T> Creator(*G);
	T* Node = Creator.CreateNode(false);
	Configure(Node);
	Creator.Finalize();
	return Node;
}

static void PlaceNode(FApplyContext& Ctx, UEdGraph* G, UEdGraphNode* N, const TSharedPtr<FJsonObject>& Op)
{
	double X = 0, Y = 0;
	if (Op->TryGetNumberField(TEXT("x"), X) && Op->TryGetNumberField(TEXT("y"), Y))
	{
		N->NodePosX = int32(X);
		N->NodePosY = int32(Y);
		return;
	}
	// New nodes go in rows of six below whatever the graph already had.
	if (!Ctx.Layout.Contains(G))
	{
		int32 MaxY = -400;
		for (UEdGraphNode* Other : G->Nodes)
		{
			if (Other && Other != N) MaxY = FMath::Max(MaxY, Other->NodePosY);
		}
		Ctx.Layout.Add(G, FIntPoint(0, MaxY + 400));
	}
	FIntPoint& L = Ctx.Layout[G];
	const int32 I = L.X++;
	N->NodePosX = 380 * (I % 6);
	N->NodePosY = L.Y + 260 * (I / 6);
}

static UFunction* FindCallable(UBlueprint* BP, const FString& Name, const FString& ClassHint, FString& Err)
{
	TArray<UClass*> Classes;
	if (!ClassHint.IsEmpty() && !ClassHint.Equals(TEXT("self"), ESearchCase::IgnoreCase))
	{
		UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(ClassHint);
		if (!C)
		{
			Err = FString::Printf(TEXT("class '%s' not found"), *ClassHint);
			return nullptr;
		}
		Classes.Add(C);
	}
	else
	{
		if (BP->SkeletonGeneratedClass) Classes.Add(BP->SkeletonGeneratedClass);
		if (ClassHint.IsEmpty())
		{
			Classes.Append({ UKismetSystemLibrary::StaticClass(), UKismetMathLibrary::StaticClass(), UGameplayStatics::StaticClass(),
				UKismetStringLibrary::StaticClass(), UKismetArrayLibrary::StaticClass(), UKismetTextLibrary::StaticClass(),
				AActor::StaticClass() });
			for (const TCHAR* Extra : { TEXT("Pawn"), TEXT("Character"), TEXT("Controller"), TEXT("PlayerController"), TEXT("AIController"),
				TEXT("PrimitiveComponent"), TEXT("SceneComponent"), TEXT("CharacterMovementComponent"), TEXT("UserWidget"),
				TEXT("WidgetBlueprintLibrary"), TEXT("WidgetLayoutLibrary"), TEXT("AIBlueprintHelperLibrary"), TEXT("BlackboardComponent"),
				TEXT("BTFunctionLibrary"), TEXT("NavigationSystemV1"), TEXT("KismetInputLibrary"), TEXT("BlueprintMapLibrary"),
				TEXT("BlueprintSetLibrary") })
			{
				if (UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(Extra)) Classes.AddUnique(C);
			}
		}
	}
	const FString Want = Norm(Name);
	for (UClass* C : Classes)
	{
		for (const FString& Candidate : { Name, FString(TEXT("K2_")) + Name, Name.Replace(TEXT(" "), TEXT("")) })
		{
			if (UFunction* F = C->FindFunctionByName(FName(*Candidate)))
			{
				if (F->HasAnyFunctionFlags(FUNC_BlueprintCallable | FUNC_BlueprintPure)) return F;
			}
		}
		for (TFieldIterator<UFunction> It(C, EFieldIteratorFlags::IncludeSuper); It; ++It)
		{
			UFunction* F = *It;
			if (!F->HasAnyFunctionFlags(FUNC_BlueprintCallable | FUNC_BlueprintPure)) continue;
			if (Norm(F->GetName()) == Want || Norm(F->GetName()) == TEXT("k2") + Want
#if WITH_EDITOR
				|| Norm(F->GetMetaData(TEXT("DisplayName"))) == Want
#endif
				)
			{
				return F;
			}
		}
	}
	Err = FString::Printf(TEXT("callable function '%s' not found%s (pass \"class\", e.g. KismetSystemLibrary, GameplayStatics, Actor, "
		"or \"self\" for this Blueprint's own functions; search with unreal_api_search)"), *Name,
		ClassHint.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(" on %s"), *ClassHint));
	return nullptr;
}

static UFunction* FindEventFunction(UBlueprint* BP, const FString& Name)
{
	UClass* Parent = BP->ParentClass;
	FString Base = Name;
	Base.RemoveFromStart(TEXT("Event "));
	Base.RemoveFromStart(TEXT("Event"));
	Base = Base.Replace(TEXT(" "), TEXT(""));
	for (const FString& Candidate : { Name, Base, FString(TEXT("Receive")) + Base, FString(TEXT("K2_")) + Base, FString(TEXT("Receive")) + Name })
	{
		if (UFunction* F = Parent ? Parent->FindFunctionByName(FName(*Candidate)) : nullptr)
		{
			if (F->HasAnyFunctionFlags(FUNC_BlueprintEvent)) return F;
		}
	}
	return nullptr;
}

static FObjectProperty* FindComponentProperty(FApplyContext& Ctx, const FString& Name)
{
	auto Lookup = [&]() -> FObjectProperty*
	{
		UClass* C = Ctx.BP->SkeletonGeneratedClass ? Ctx.BP->SkeletonGeneratedClass : Ctx.BP->GeneratedClass;
		if (!C) return nullptr;
		for (TFieldIterator<FObjectProperty> It(C, EFieldIteratorFlags::IncludeSuper); It; ++It)
		{
			if (It->GetName().Equals(Name, ESearchCase::IgnoreCase) && It->PropertyClass &&
				(It->PropertyClass->IsChildOf(UActorComponent::StaticClass()) || It->PropertyClass->IsChildOf(UWidget::StaticClass())))  // widget vars: OnClicked, ...
			{
				return *It;
			}
		}
		return nullptr;
	};
	FObjectProperty* P = Lookup();
	if (!P && Ctx.bStructural)
	{
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Ctx.BP); // regenerate skeleton so new components exist
		Ctx.bStructural = false;
		P = Lookup();
	}
	return P;
}

/** By-ref inputs (const FName& KeyName, ...) need a wired value: Unreal rejects a typed literal. */
static bool NeedsWiredInput(const UEdGraphPin* Pin)
{
	return Pin->PinType.bIsReference && !UEdGraphSchema_K2::IsAutoCreateRefTerm(Pin)
		&& Pin->PinType.PinCategory != UEdGraphSchema_K2::PC_Exec && !Pin->PinType.IsContainer()
		&& Pin->Direction == EGPD_Input;
}

/** Spawn a MakeLiteral-, MakeVector or MakeRotator node next to the node, set it, and wire it into Pin (what a human does). */
static bool WireLiteral(UEdGraphPin* Pin, const TSharedPtr<FJsonValue>& Value, FString& Err)
{
	const UEdGraphSchema_K2* K2 = GetDefault<UEdGraphSchema_K2>();
	UEdGraphNode* Owner = Pin->GetOwningNode();
	UEdGraph* G = Owner->GetGraph();
	const FName Cat = Pin->PinType.PinCategory;
	const UObject* Sub = Pin->PinType.PinSubCategoryObject.Get();
	UClass* Lib = UKismetSystemLibrary::StaticClass();
	FName Fn;
	if (Cat == UEdGraphSchema_K2::PC_Name) Fn = TEXT("MakeLiteralName");
	else if (Cat == UEdGraphSchema_K2::PC_String) Fn = TEXT("MakeLiteralString");
	else if (Cat == UEdGraphSchema_K2::PC_Text) Fn = TEXT("MakeLiteralText");
	else if (Cat == UEdGraphSchema_K2::PC_Int) Fn = TEXT("MakeLiteralInt");
	else if (Cat == UEdGraphSchema_K2::PC_Real) Fn = TEXT("MakeLiteralDouble");
	else if (Cat == UEdGraphSchema_K2::PC_Boolean) Fn = TEXT("MakeLiteralBool");
	else if (Cat == UEdGraphSchema_K2::PC_Byte && !Sub) Fn = TEXT("MakeLiteralByte");
	else if (Cat == UEdGraphSchema_K2::PC_Struct && Sub == TBaseStructure<FVector>::Get()) { Lib = UKismetMathLibrary::StaticClass(); Fn = TEXT("MakeVector"); }
	else if (Cat == UEdGraphSchema_K2::PC_Struct && Sub == TBaseStructure<FRotator>::Get()) { Lib = UKismetMathLibrary::StaticClass(); Fn = TEXT("MakeRotator"); }
	UFunction* F = Fn.IsNone() ? nullptr : Lib->FindFunctionByName(Fn);
	if (!F)
	{
		Err = FString::Printf(TEXT("pin %s is passed by reference and needs a wired input; connect a node to it"), *Pin->PinName.ToString());
		return false;
	}
	G->Modify();
	UK2Node_CallFunction* Lit = NewNode<UK2Node_CallFunction>(G, [F](UK2Node_CallFunction* N) { N->SetFromFunction(F); });
	Lit->NodePosX = Owner->NodePosX - 340;
	Lit->NodePosY = Owner->NodePosY + 140;
	if (Fn == TEXT("MakeVector") || Fn == TEXT("MakeRotator"))
	{
		TArray<TSharedPtr<FJsonValue>> Items;
		if (Value->Type == EJson::Array) Items = Value->AsArray();
		// [x,y,z] for vectors; [pitch,yaw,roll] for rotators (MakeRotator's pins are Roll, Pitch, Yaw).
		const TArray<const TCHAR*> Names = Fn == TEXT("MakeVector") ? TArray<const TCHAR*>{ TEXT("X"), TEXT("Y"), TEXT("Z") }
			: TArray<const TCHAR*>{ TEXT("Pitch"), TEXT("Yaw"), TEXT("Roll") };
		for (int32 I = 0; I < Names.Num() && I < Items.Num(); ++I)
		{
			if (UEdGraphPin* In = FindPin(Lit, Names[I], EGPD_Input)) K2->TrySetDefaultValue(*In, JsonToDefaultString(Items[I]));
		}
	}
	else if (UEdGraphPin* In = FindPin(Lit, TEXT("Value"), EGPD_Input))
	{
		K2->TrySetDefaultValue(*In, JsonToDefaultString(Value));
	}
	UEdGraphPin* Out = FindPin(Lit, TEXT("ReturnValue"), EGPD_Output);
	if (!Out || !K2->TryCreateConnection(Out, Pin))
	{
		Err = FString::Printf(TEXT("could not wire a literal into by-ref pin %s"), *Pin->PinName.ToString());
		return false;
	}
	Lit->NodeConnectionListChanged();
	Owner->NodeConnectionListChanged();
	return true;
}

static bool ApplyPinDefault(UEdGraphPin* Pin, const TSharedPtr<FJsonValue>& Value, FString& Err)
{
	const UEdGraphSchema_K2* K2 = GetDefault<UEdGraphSchema_K2>();
	if (NeedsWiredInput(Pin))
	{
		return WireLiteral(Pin, Value, Err);
	}
	const FName Cat = Pin->PinType.PinCategory;
	const FString Str = JsonToDefaultString(Value);
	if (Cat == UEdGraphSchema_K2::PC_Object || Cat == UEdGraphSchema_K2::PC_Class || Cat == UEdGraphSchema_K2::PC_SoftObject
		|| Cat == UEdGraphSchema_K2::PC_SoftClass || Cat == UEdGraphSchema_K2::PC_Interface)
	{
		UObject* Obj = nullptr;
		if (!Str.IsEmpty())
		{
			Obj = (Cat == UEdGraphSchema_K2::PC_Class || Cat == UEdGraphSchema_K2::PC_SoftClass)
				? FEpicUnrealMCPReflectionCommands::ResolveClass(Str) : StaticLoadObject(UObject::StaticClass(), nullptr, *Str, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Obj)
			{
				Err = FString::Printf(TEXT("cannot load '%s' for pin %s"), *Str, *Pin->PinName.ToString());
				return false;
			}
		}
		K2->TrySetDefaultObject(*Pin, Obj);
		return true;
	}
	K2->TrySetDefaultValue(*Pin, Str);
	if (!Str.IsEmpty() && Pin->DefaultValue.IsEmpty() && Pin->DefaultTextValue.IsEmpty())
	{
		const FString Why = K2->IsPinDefaultValid(Pin, Str, nullptr, FText::FromString(Str));
		Err = FString::Printf(TEXT("pin %s rejected '%s'%s"), *Pin->PinName.ToString(), *Str, Why.IsEmpty() ? TEXT("") : *(TEXT(": ") + Why));
		return false;
	}
	return true;
}

static bool ApplyPins(UEdGraphNode* N, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const TSharedPtr<FJsonObject>* Pins = nullptr;
	if (!Op->TryGetObjectField(TEXT("pins"), Pins))
	{
		return true;
	}
	for (const auto& Pair : (*Pins)->Values)
	{
		const FString PinName(*Pair.Key);
		UEdGraphPin* Pin = FindPin(N, PinName, EGPD_Input);
		if (!Pin)
		{
			Err = FString::Printf(TEXT("no input pin '%s' (pins: %s)"), *PinName, *PinListing(N));
			return false;
		}
		if (!ApplyPinDefault(Pin, Pair.Value, Err))
		{
			return false;
		}
	}
	return true;
}

static UEdGraphNode* ResolveRef(FApplyContext& Ctx, const FString& Ref)
{
	if (UEdGraphNode** A = Ctx.Aliases.Find(Ref)) return *A;
	return FindNodeById(Ctx.BP, Ref);
}

static bool SplitPinRef(const FString& Ref, FString& Node, FString& Pin)
{
	return Ref.Split(TEXT("."), &Node, &Pin);
}

static UEdGraph* StandardMacro(const FString& Name)
{
	UBlueprint* Lib = LoadObject<UBlueprint>(nullptr, TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros"));
	if (!Lib) return nullptr;
	for (UEdGraph* G : Lib->MacroGraphs)
	{
		if (G && Norm(G->GetName()) == Norm(Name)) return G;
	}
	return nullptr;
}

/** The node class the editor's action menu spawns for a function (UBlueprintFunctionNodeSpawner::Create).
 *  A plain K2Node_CallFunction for an ArrayParm function (Array_Length, Array_Add, ...) never resolves its
 *  wildcard pins, so a connected array stays "wildcard" and the Blueprint fails to compile. */
static TSubclassOf<UK2Node_CallFunction> CallNodeClassFor(const UFunction* F)
{
	if (F->HasMetaData(FBlueprintMetadata::MD_CommutativeAssociativeBinaryOperator) && F->HasAllFunctionFlags(FUNC_BlueprintPure))
	{
		return UK2Node_CommutativeAssociativeBinaryOperator::StaticClass();
	}
	if (F->HasMetaData(FBlueprintMetadata::MD_DataTablePin)) return UK2Node_CallDataTableFunction::StaticClass();
	if (F->HasMetaData(FBlueprintMetadata::MD_ArrayParam)) return UK2Node_CallArrayFunction::StaticClass();
	return UK2Node_CallFunction::StaticClass();
}

static UK2Node_CallFunction* NewCallFunctionNode(UEdGraph* G, UFunction* F)
{
	// FGraphNodeCreator, with the node class chosen at runtime.
	G->Modify();
	UK2Node_CallFunction* N = NewObject<UK2Node_CallFunction>(G, CallNodeClassFor(F), NAME_None, RF_Transactional);
	G->AddNode(N, false, false);
	N->CreateNewGuid();
	N->SetFromFunction(F);
	N->PostPlacedNewNode();
	if (N->Pins.Num() == 0) N->AllocateDefaultPins();
	return N;
}

/** True if any pin of N is still an unresolved wildcard while linked (it would fail to compile). */
static UEdGraphPin* LinkedWildcardPin(UEdGraphNode* N)
{
	for (UEdGraphPin* P : N->Pins)
	{
		if (P && !P->bOrphanedPin && P->LinkedTo.Num() > 0 && P->PinType.PinCategory == UEdGraphSchema_K2::PC_Wildcard) return P;
	}
	return nullptr;
}

/** What the graph editor does after a drag-connect or break (FDragConnection::DroppedOnPin): the pin-level
 *  PinConnectionListChanged already ran inside the schema; the node-level notification makes macro instances
 *  (ForEachLoop, ...) reconstruct with their resolved wildcard type. If a linked pin is still a wildcard after
 *  that, reconstruct once more so PostReconstructNode re-propagates from the links (array nodes do). */
static void NotifyConnectionChanged(UEdGraphNode* N)
{
	if (!N || !IsValid(N)) return;
	N->NodeConnectionListChanged();
	if (UK2Node* K = Cast<UK2Node>(N); K && LinkedWildcardPin(K))
	{
		K->ReconstructNode();
	}
}

/** Creates (or, for events, reuses) a node from an add_node op. */
static UEdGraphNode* CreateNodeFromOp(FApplyContext& Ctx, UEdGraph* G, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	UBlueprint* BP = Ctx.BP;
	FString Kind;
	Op->TryGetStringField(TEXT("kind"), Kind);
	Kind = Kind.ToLower();
	auto Str = [&Op](const TCHAR* F) { FString V; Op->TryGetStringField(F, V); return V; };

	if (Kind == TEXT("event"))
	{
		UFunction* F = FindEventFunction(BP, Str(TEXT("event")));
		if (!F)
		{
			Err = FString::Printf(TEXT("no overridable event '%s' on %s (BeginPlay, Tick, ActorBeginOverlap, EndPlay, AnyDamage, Construct, "
				"ExecuteAI, ...; for your own events use custom_event)"), *Str(TEXT("event")), BP->ParentClass ? *BP->ParentClass->GetName() : TEXT("?"));
			return nullptr;
		}
		if (UK2Node_Event* Existing = FBlueprintEditorUtils::FindOverrideForFunction(BP, F->GetOwnerClass(), F->GetFName()))
		{
			Existing->Modify();
			Existing->SetEnabledState(ENodeEnabledState::Enabled, false); // template "ghost" events start disabled
			Existing->NodeComment.Empty();
			Existing->bCommentBubbleVisible = false;
			return Existing;
		}
		UK2Node_Event* N = NewNode<UK2Node_Event>(G, [&](UK2Node_Event* E)
		{
			E->EventReference.SetExternalMember(F->GetFName(), F->GetOwnerClass());
			E->bOverrideFunction = true;
		});
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("custom_event"))
	{
		const FString Name = Str(TEXT("name"));
		UK2Node_CustomEvent* N = NewNode<UK2Node_CustomEvent>(G, [&](UK2Node_CustomEvent* E) { E->CustomFunctionName = FName(*Name); });
		const TArray<TSharedPtr<FJsonValue>>* Inputs = nullptr;
		if (Op->TryGetArrayField(TEXT("inputs"), Inputs))
		{
			for (const TSharedPtr<FJsonValue>& In : *Inputs)
			{
				FEdGraphPinType T;
				const TSharedPtr<FJsonObject> IO = In->AsObject();
				if (!ParsePinType(IO->GetStringField(TEXT("type")), T, Err)) return nullptr;
				N->CreateUserDefinedPin(FName(*IO->GetStringField(TEXT("name"))), T, EGPD_Output);
			}
		}
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("component_event"))
	{
		const FString Comp = Str(TEXT("component"));
		FString EventName = Str(TEXT("event"));
		FObjectProperty* CompProp = FindComponentProperty(Ctx, Comp);
		if (!CompProp)
		{
			Err = FString::Printf(TEXT("component '%s' not found on %s (add_component first)"), *Comp, *BP->GetName());
			return nullptr;
		}
		FMulticastDelegateProperty* Del = nullptr;
		for (const FString& Candidate : { EventName, FString(TEXT("On")) + EventName })
		{
			Del = FindFProperty<FMulticastDelegateProperty>(CompProp->PropertyClass, FName(*Candidate));
			if (Del) break;
		}
		if (!Del)
		{
			Err = FString::Printf(TEXT("%s has no event '%s' (OnComponentBeginOverlap, OnComponentEndOverlap, OnComponentHit, OnClicked, ...)"),
				*CompProp->PropertyClass->GetName(), *EventName);
			return nullptr;
		}
		if (const UK2Node_ComponentBoundEvent* Existing = FKismetEditorUtilities::FindBoundEventForComponent(BP, Del->GetFName(), CompProp->GetFName()))
		{
			return const_cast<UK2Node_ComponentBoundEvent*>(Existing);
		}
		UK2Node_ComponentBoundEvent* N = NewNode<UK2Node_ComponentBoundEvent>(G, [&](UK2Node_ComponentBoundEvent* E)
		{
			E->InitializeComponentBoundEventParams(CompProp, Del);
		});
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("call_function") || Kind == TEXT("call"))
	{
		UFunction* F = FindCallable(BP, Str(TEXT("function")), Str(TEXT("class")), Err);
		if (!F)
		{
			// A custom event or function added earlier in this batch is not in the skeleton class yet.
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);
			Err.Empty();
			F = FindCallable(BP, Str(TEXT("function")), Str(TEXT("class")), Err);
		}
		if (!F) return nullptr;
		// Array_Get is BlueprintInternalUseOnly: the editor's "Get" node is K2Node_GetArrayItem.
		if (F->GetOwnerClass() == UKismetArrayLibrary::StaticClass() && F->GetFName() == TEXT("Array_Get"))
		{
			UEdGraphNode* N = NewNode<UK2Node_GetArrayItem>(G, [](UK2Node_GetArrayItem*) {});
			PlaceNode(Ctx, G, N, Op);
			return N;
		}
		UK2Node_CallFunction* N = NewCallFunctionNode(G, F);
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("get_var") || Kind == TEXT("set_var"))
	{
		const FName Var(*Str(TEXT("variable")));
		const FString ClassHint = Str(TEXT("class"));
		UClass* External = ClassHint.IsEmpty() ? nullptr : FEpicUnrealMCPReflectionCommands::ResolveClass(ClassHint);
		if (!ClassHint.IsEmpty() && !External)
		{
			Err = FString::Printf(TEXT("class '%s' not found"), *ClassHint);
			return nullptr;
		}
		if (!External && BP->SkeletonGeneratedClass && !FindFProperty<FProperty>(BP->SkeletonGeneratedClass, Var))
		{
			// Fresh variables/components need the skeleton regenerated before they resolve.
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);
		}
		auto Setup = [&](FMemberReference& Ref) { if (External) Ref.SetExternalMember(Var, External); else Ref.SetSelfMember(Var); };
		UEdGraphNode* N = Kind == TEXT("get_var")
			? static_cast<UEdGraphNode*>(NewNode<UK2Node_VariableGet>(G, [&](UK2Node_VariableGet* V) { Setup(V->VariableReference); }))
			: static_cast<UEdGraphNode*>(NewNode<UK2Node_VariableSet>(G, [&](UK2Node_VariableSet* V) { Setup(V->VariableReference); }));
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("branch"))
	{
		UEdGraphNode* N = NewNode<UK2Node_IfThenElse>(G, [](UK2Node_IfThenElse*) {});
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("sequence"))
	{
		UK2Node_ExecutionSequence* N = NewNode<UK2Node_ExecutionSequence>(G, [](UK2Node_ExecutionSequence*) {});
		double Outputs = 2;
		Op->TryGetNumberField(TEXT("outputs"), Outputs);
		for (int32 I = 2; I < int32(Outputs); ++I) N->AddInputPin();
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("cast"))
	{
		UClass* Target = FEpicUnrealMCPReflectionCommands::ResolveClass(Str(TEXT("class")));
		if (!Target)
		{
			Err = FString::Printf(TEXT("cast class '%s' not found"), *Str(TEXT("class")));
			return nullptr;
		}
		UK2Node_DynamicCast* N = NewNode<UK2Node_DynamicCast>(G, [&](UK2Node_DynamicCast* C) { C->TargetType = Target; });
		bool bPure = false;
		if (Op->TryGetBoolField(TEXT("pure"), bPure) && bPure) N->SetPurity(true);
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("self"))
	{
		UEdGraphNode* N = NewNode<UK2Node_Self>(G, [](UK2Node_Self*) {});
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("macro"))
	{
		UEdGraph* Macro = StandardMacro(Str(TEXT("macro")));
		if (!Macro)
		{
			Err = FString::Printf(TEXT("standard macro '%s' not found (ForLoop, ForEachLoop, WhileLoop, DoOnce, DoN, Gate, FlipFlop, IsValid)"), *Str(TEXT("macro")));
			return nullptr;
		}
		UEdGraphNode* N = NewNode<UK2Node_MacroInstance>(G, [&](UK2Node_MacroInstance* M) { M->SetMacroGraph(Macro); });
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("spawn_actor"))
	{
		UEdGraphNode* N = NewNode<UK2Node_SpawnActorFromClass>(G, [](UK2Node_SpawnActorFromClass*) {});
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("node"))
	{
		UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(Str(TEXT("class")));
		if (!C || !C->IsChildOf(UEdGraphNode::StaticClass()) || C->HasAnyClassFlags(CLASS_Abstract))
		{
			Err = FString::Printf(TEXT("'%s' is not a concrete graph node class (e.g. K2Node_CreateWidget)"), *Str(TEXT("class")));
			return nullptr;
		}
		G->Modify();
		UEdGraphNode* N = NewObject<UEdGraphNode>(G, C, NAME_None, RF_Transactional);
		G->AddNode(N, false, false);
		N->CreateNewGuid();
		N->PostPlacedNewNode();
		N->AllocateDefaultPins();
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	Err = FString::Printf(TEXT("unknown node kind '%s' (event, custom_event, component_event, call_function, get_var, set_var, "
		"branch, sequence, cast, self, macro, spawn_actor, node)"), *Kind);
	return nullptr;
}

static bool OpAddVariable(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FName Name(*Op->GetStringField(TEXT("name")));
	FEdGraphPinType T;
	FString TypeStr;
	Op->TryGetStringField(TEXT("type"), TypeStr);
	if (!ParsePinType(TypeStr, T, Err)) return false;
	if (FBlueprintEditorUtils::FindNewVariableIndex(Ctx.BP, Name) != INDEX_NONE)
	{
		Err = FString::Printf(TEXT("variable '%s' already exists"), *Name.ToString());
		return false;
	}
	const FString Default = JsonToDefaultString(Op->TryGetField(TEXT("default")));
	if (!FBlueprintEditorUtils::AddMemberVariable(Ctx.BP, Name, T, Default))
	{
		Err = FString::Printf(TEXT("could not add variable '%s' (name taken by a parent class or component?)"), *Name.ToString());
		return false;
	}
	bool bEditable = false;
	if (Op->TryGetBoolField(TEXT("instance_editable"), bEditable))
	{
		FBlueprintEditorUtils::SetBlueprintOnlyEditableFlag(Ctx.BP, Name, !bEditable);
	}
	FString Category;
	if (Op->TryGetStringField(TEXT("category"), Category))
	{
		FBlueprintEditorUtils::SetBlueprintVariableCategory(Ctx.BP, Name, nullptr, FText::FromString(Category));
	}
	Ctx.bStructural = true;
	return true;
}

static bool OpAddComponent(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	USimpleConstructionScript* SCS = Ctx.BP->SimpleConstructionScript;
	if (!SCS)
	{
		Err = TEXT("this Blueprint has no components (not an Actor Blueprint)");
		return false;
	}
	FString Name, ClassName, Parent;
	Op->TryGetStringField(TEXT("name"), Name);
	Op->TryGetStringField(TEXT("class"), ClassName);
	Op->TryGetStringField(TEXT("parent"), Parent);
	UClass* CC = FEpicUnrealMCPReflectionCommands::ResolveClass(ClassName);
	if (!CC || !CC->IsChildOf(UActorComponent::StaticClass()))
	{
		Err = FString::Printf(TEXT("'%s' is not a component class (StaticMeshComponent, SphereComponent, BoxComponent, PointLightComponent, ...)"), *ClassName);
		return false;
	}
	if (SCS->FindSCSNode(FName(*Name)))
	{
		Err = FString::Printf(TEXT("component '%s' already exists"), *Name);
		return false;
	}
	SCS->Modify();
	USCS_Node* Node = SCS->CreateNode(CC, FName(*Name));
	bool bAsRoot = false;
	Op->TryGetBoolField(TEXT("as_root"), bAsRoot);
	if (!Parent.IsEmpty())
	{
		if (USCS_Node* P = SCS->FindSCSNode(FName(*Parent)))
		{
			P->AddChildNode(Node);
		}
		else
		{
			AActor* CDO = Ctx.BP->ParentClass ? Cast<AActor>(Ctx.BP->ParentClass->GetDefaultObject()) : nullptr;
			USceneComponent* Native = nullptr;
			if (CDO)
			{
				for (UActorComponent* C : CDO->GetComponents())
				{
					if (C && C->GetName().Equals(Parent, ESearchCase::IgnoreCase)) Native = Cast<USceneComponent>(C);
				}
			}
			if (!Native)
			{
				Err = FString::Printf(TEXT("parent component '%s' not found"), *Parent);
				return false;
			}
			Node->SetParent(Native);
			SCS->AddNode(Node);
		}
	}
	else if (bAsRoot || !CC->IsChildOf(USceneComponent::StaticClass()))
	{
		if (bAsRoot)
		{
			// Replace the template's DefaultSceneRoot so this component is the actor's root.
			for (USCS_Node* R : TArray<USCS_Node*>(SCS->GetRootNodes()))
			{
				if (R && R != Node && R->GetVariableName() == TEXT("DefaultSceneRoot") && R->GetChildNodes().Num() == 0)
				{
					SCS->RemoveNode(R);
				}
			}
		}
		SCS->AddNode(Node);
	}
	else
	{
		USCS_Node* Root = nullptr;
		for (USCS_Node* R : SCS->GetRootNodes())
		{
			if (R && R->ComponentClass && R->ComponentClass->IsChildOf(USceneComponent::StaticClass())) { Root = R; break; }
		}
		if (Root) Root->AddChildNode(Node); else SCS->AddNode(Node);
	}
	TArray<FString> Errors;
	if (Node->ComponentTemplate)
	{
		FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(Node->ComponentTemplate, ObjField(Op, TEXT("properties")), Errors);
	}
	Ctx.bStructural = true;
	if (Errors.Num())
	{
		Err = FString::Join(Errors, TEXT("; "));
		return false;
	}
	return true;
}

static bool OpSetComponent(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString Name = Op->GetStringField(TEXT("name"));
	USCS_Node* Node = Ctx.BP->SimpleConstructionScript ? Ctx.BP->SimpleConstructionScript->FindSCSNode(FName(*Name)) : nullptr;
	if (!Node || !Node->ComponentTemplate)
	{
		Err = FString::Printf(TEXT("component '%s' is not one this Blueprint added; inherited components are set with "
			"set_defaults {\"%s.Property\": value}"), *Name, *Name);
		return false;
	}
	TArray<FString> Errors;
	Node->ComponentTemplate->Modify();
	FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(Node->ComponentTemplate, ObjField(Op, TEXT("properties")), Errors);
	if (Errors.Num())
	{
		Err = FString::Join(Errors, TEXT("; "));
		return false;
	}
	return true;
}

static bool OpAddFunction(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString Name = Op->GetStringField(TEXT("name"));
	if (FindGraph(Ctx.BP, Name))
	{
		Err = FString::Printf(TEXT("graph '%s' already exists"), *Name);
		return false;
	}
	UEdGraph* G = FBlueprintEditorUtils::CreateNewGraph(Ctx.BP, FName(*Name), UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
	FBlueprintEditorUtils::AddFunctionGraph<UClass>(Ctx.BP, G, true, nullptr);
	TArray<UK2Node_FunctionEntry*> Entries;
	G->GetNodesOfClass(Entries);
	if (Entries.Num() == 0)
	{
		Err = TEXT("function graph has no entry node");
		return false;
	}
	UK2Node_FunctionEntry* Entry = Entries[0];
	bool bPure = false;
	if (Op->TryGetBoolField(TEXT("pure"), bPure) && bPure)
	{
		Entry->AddExtraFlags(FUNC_BlueprintPure);
	}
	auto AddPins = [&](const TCHAR* Field, UK2Node_EditablePinBase* Target, EEdGraphPinDirection Dir) -> bool
	{
		const TArray<TSharedPtr<FJsonValue>>* List = nullptr;
		if (!Op->TryGetArrayField(Field, List)) return true;
		for (const TSharedPtr<FJsonValue>& V : *List)
		{
			const TSharedPtr<FJsonObject> P = V->AsObject();
			FEdGraphPinType T;
			if (!ParsePinType(P->GetStringField(TEXT("type")), T, Err)) return false;
			Target->CreateUserDefinedPin(FName(*P->GetStringField(TEXT("name"))), T, Dir);
		}
		return true;
	};
	if (!AddPins(TEXT("inputs"), Entry, EGPD_Output)) return false;
	const TArray<TSharedPtr<FJsonValue>>* Outputs = nullptr;
	if (Op->TryGetArrayField(TEXT("outputs"), Outputs) && Outputs->Num())
	{
		UK2Node_FunctionResult* Result = NewNode<UK2Node_FunctionResult>(G, [](UK2Node_FunctionResult*) {});
		Result->NodePosX = Entry->NodePosX + 900;
		Result->NodePosY = Entry->NodePosY;
		if (!AddPins(TEXT("outputs"), Result, EGPD_Input)) return false;
		Ctx.Aliases.Add(Name + TEXT("_result"), Result);
	}
	Ctx.Aliases.Add(Name + TEXT("_entry"), Entry);
	Ctx.bStructural = true;
	return true;
}

static bool OpConnect(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	FString From, To, FN, FP, TN, TP;
	Op->TryGetStringField(TEXT("from"), From);
	Op->TryGetStringField(TEXT("to"), To);
	if (!SplitPinRef(From, FN, FP) || !SplitPinRef(To, TN, TP))
	{
		Err = TEXT("connect needs from/to as \"node.pin\" (node = alias from this call or an id from bp_export)");
		return false;
	}
	UEdGraphNode* A = ResolveRef(Ctx, FN);
	UEdGraphNode* B = ResolveRef(Ctx, TN);
	if (!A || !B)
	{
		Err = FString::Printf(TEXT("unknown node '%s'"), *(A ? TN : FN));
		return false;
	}
	UEdGraphPin* PA = FindPin(A, FP, EGPD_Output);
	UEdGraphPin* PB = FindPin(B, TP, EGPD_Input);
	if (!PA)
	{
		Err = FString::Printf(TEXT("'%s' has no output pin '%s' (pins: %s)"), *FN, *FP, *PinListing(A));
		return false;
	}
	if (!PB)
	{
		Err = FString::Printf(TEXT("'%s' has no input pin '%s' (pins: %s)"), *TN, *TP, *PinListing(B));
		return false;
	}
	// The graph's own schema: K2 for event/function graphs, the animation schemas for pose links.
	const UEdGraphSchema* Schema = A->GetGraph() ? A->GetGraph()->GetSchema() : GetDefault<UEdGraphSchema_K2>();
	const FPinConnectionResponse Resp = Schema->CanCreateConnection(PA, PB);
	if (Resp.Response == CONNECT_RESPONSE_DISALLOW)
	{
		Err = FString::Printf(TEXT("cannot connect %s -> %s: %s"), *From, *To, *Resp.Message.ToString());
		return false;
	}
	A->Modify();
	B->Modify();
	if (!Schema->TryCreateConnection(PA, PB))
	{
		Err = FString::Printf(TEXT("connection %s -> %s failed: %s"), *From, *To, *Resp.Message.ToString());
		return false;
	}
	NotifyConnectionChanged(A);
	NotifyConnectionChanged(B);
	return true;
}

static bool OpDisconnect(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	FString From, To, FN, FP, TN, TP;
	Op->TryGetStringField(TEXT("from"), From);
	Op->TryGetStringField(TEXT("to"), To);
	if (!SplitPinRef(From, FN, FP))
	{
		Err = TEXT("disconnect needs from \"node.pin\" (and optionally to)");
		return false;
	}
	UEdGraphNode* A = ResolveRef(Ctx, FN);
	UEdGraphPin* PA = A ? FindPin(A, FP, EGPD_MAX) : nullptr;
	if (!PA)
	{
		Err = FString::Printf(TEXT("pin '%s' not found"), *From);
		return false;
	}
	A->Modify();
	if (SplitPinRef(To, TN, TP))
	{
		UEdGraphNode* B = ResolveRef(Ctx, TN);
		UEdGraphPin* PB = B ? FindPin(B, TP, EGPD_MAX) : nullptr;
		if (!PB)
		{
			Err = FString::Printf(TEXT("pin '%s' not found"), *To);
			return false;
		}
		B->Modify();
		GetDefault<UEdGraphSchema_K2>()->BreakSinglePinLink(PA, PB); // notifies both nodes (wildcards revert)
	}
	else
	{
		GetDefault<UEdGraphSchema_K2>()->BreakPinLinks(*PA, true);
	}
	return true;
}

TSharedPtr<FJsonObject> MCPAuthoring::CompileBlueprintWithResults(UBlueprint* BP)
{
	FCompilerResultsLog Log;
	Log.bSilentMode = true;
	FKismetEditorUtilities::CompileBlueprint(BP, EBlueprintCompileOptions::SkipGarbageCollection, &Log);
	TArray<FString> Errors, Warnings;
	for (const TSharedRef<FTokenizedMessage>& M : Log.Messages)
	{
		const FString Text = M->ToText().ToString();
		if (M->GetSeverity() == EMessageSeverity::Error) Errors.Add(Text);
		else if (M->GetSeverity() == EMessageSeverity::Warning || M->GetSeverity() == EMessageSeverity::PerformanceWarning) Warnings.Add(Text);
	}
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("status"), BlueprintStatusName(BP->Status));
	J->SetField(TEXT("errors"), StringArray(Errors));
	J->SetField(TEXT("warnings"), StringArray(Warnings));
	J->SetBoolField(TEXT("ok"), BP->Status != BS_Error && Errors.Num() == 0);
	return J;
}

static TSharedPtr<FJsonObject> HandleBpApply(const TSharedPtr<FJsonObject>& Params)
{
	FString Path, ParentName;
	Params->TryGetStringField(TEXT("blueprint"), Path);
	Params->TryGetStringField(TEXT("create_parent"), ParentName);

	UBlueprint* BP = LoadBlueprint(Path);
	bool bCreated = false;
	if (!BP)
	{
		if (ParentName.IsEmpty())
		{
			return MCPError(FString::Printf(TEXT("Blueprint '%s' not found (pass create_parent to create it, e.g. Actor, Character, "
				"GameModeBase, PlayerController, AIController, UserWidget, BTTask_BlueprintBase)"), *Path));
		}
		UClass* Parent = FEpicUnrealMCPReflectionCommands::ResolveClass(ParentName);
		if (!Parent || !FKismetEditorUtilities::CanCreateBlueprintOfClass(Parent))
		{
			return MCPError(FString::Printf(TEXT("cannot create a Blueprint of '%s'"), *ParentName));
		}
		FString Pkg, Name, Err;
		if (!SplitAssetPath(Path, Pkg, Name, Err))
		{
			return MCPError(Err);
		}
		if (Parent->IsChildOf(UUserWidget::StaticClass()))
		{
			return MCPError(TEXT("create widget Blueprints with widget_apply (create=true); then add logic with bp_apply"));
		}
		UPackage* Package = CreatePackage(*Pkg);
		BP = FKismetEditorUtilities::CreateBlueprint(Parent, Package, FName(*Name), BPTYPE_Normal, FName(TEXT("UnrealMCP")));
		if (!BP)
		{
			return MCPError(TEXT("Blueprint creation failed"));
		}
		FAssetRegistryModule::AssetCreated(BP);
		BP->MarkPackageDirty();
		bCreated = true;
	}

	return MCPAuthoring::ApplyBlueprintOps(BP, bCreated, Params);
}

TSharedPtr<FJsonObject> MCPAuthoring::ApplyBlueprintOps(UBlueprint* BP, bool bCreated, const TSharedPtr<FJsonObject>& Params,
	const FOpExtension& Extension, TFunction<void(FGraphApplyContext&)> Setup)
{
	bool bCompile = true, bDryRun = false, bAtomic = true, bSave = true;
	Params->TryGetBoolField(TEXT("compile"), bCompile);
	Params->TryGetBoolField(TEXT("dry_run"), bDryRun);
	Params->TryGetBoolField(TEXT("atomic"), bAtomic);
	Params->TryGetBoolField(TEXT("save"), bSave);

	FApplyContext Ctx;
	Ctx.BP = BP;
	TArray<TSharedPtr<FJsonValue>> Failed;
	int32 Applied = 0;
	const int32 QueueBefore = (GEditor && GEditor->Trans) ? GEditor->Trans->GetQueueLength() : 0;
	{
		FScopedTransaction Transaction(FText::FromString(FString::Printf(TEXT("MCP: apply %s"), *BP->GetName())));
		BP->Modify();
		if (BP->SimpleConstructionScript) BP->SimpleConstructionScript->Modify();
		if (Setup) Setup(Ctx);
		const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
		if (Params->TryGetArrayField(TEXT("operations"), Ops))
		{
			for (int32 I = 0; I < Ops->Num(); ++I)
			{
				const TSharedPtr<FJsonObject> Op = (*Ops)[I]->AsObject();
				FString Err, Kind;
				Op->TryGetStringField(TEXT("op"), Kind);
				bool bOk = false;
				if (Extension && Extension(Ctx, Kind, Op, bOk, Err))
				{
					// handled by the domain extension (animbp_apply)
				}
				else if (Kind == TEXT("add_variable")) bOk = OpAddVariable(Ctx, Op, Err);
				else if (Kind == TEXT("remove_variable"))
				{
					FBlueprintEditorUtils::RemoveMemberVariable(BP, FName(*Op->GetStringField(TEXT("name"))));
					Ctx.bStructural = bOk = true;
				}
				else if (Kind == TEXT("add_component")) bOk = OpAddComponent(Ctx, Op, Err);
				else if (Kind == TEXT("set_component")) bOk = OpSetComponent(Ctx, Op, Err);
				else if (Kind == TEXT("add_function")) bOk = OpAddFunction(Ctx, Op, Err);
				else if (Kind == TEXT("set_defaults"))
				{
					Ctx.DeferredDefaults.Add(ObjField(Op, TEXT("properties")));
					bOk = true;
				}
				else if (Kind == TEXT("add_node") || Kind == TEXT("node"))
				{
					FString GraphName;
					Op->TryGetStringField(TEXT("graph"), GraphName);
					UEdGraph* G = MCPAuthoring::ResolveGraph(Ctx, GraphName);
					if (!G)
					{
						Err = FString::Printf(TEXT("graph '%s' not found (EventGraph, a function added with add_function, or the id of a "
							"node that owns a graph, e.g. an AnimBP state or transition)"), *GraphName);
					}
					else
					{
						G->Modify(); // the graph's node list must be in the undo record, or rollback leaves the node behind
						if (UEdGraphNode* N = CreateNodeFromOp(Ctx, G, Op, Err))
						{
							FString Alias;
							if (Op->TryGetStringField(TEXT("id"), Alias) && !Alias.IsEmpty()) Ctx.Aliases.Add(Alias, N);
							bOk = ApplyPins(N, Op, Err);
						}
					}
				}
				else if (Kind == TEXT("connect")) bOk = OpConnect(Ctx, Op, Err);
				else if (Kind == TEXT("disconnect")) bOk = OpDisconnect(Ctx, Op, Err);
				else if (Kind == TEXT("set_pins"))
				{
					FString Ref;
					Op->TryGetStringField(TEXT("node"), Ref);
					if (UEdGraphNode* N = ResolveRef(Ctx, Ref)) { N->Modify(); bOk = ApplyPins(N, Op, Err); }
					else Err = FString::Printf(TEXT("unknown node '%s'"), *Ref);
				}
				else if (Kind == TEXT("remove_node"))
				{
					FString Ref;
					Op->TryGetStringField(TEXT("node"), Ref);
					if (UEdGraphNode* N = ResolveRef(Ctx, Ref))
					{
						if (UEdGraph* NG = N->GetGraph()) NG->Modify(); // graph node list must be in the undo record
						FBlueprintEditorUtils::RemoveNode(BP, N, true);
						bOk = true;
					}
					else Err = FString::Printf(TEXT("unknown node '%s'"), *Ref);
				}
				else
				{
					Err = FString::Printf(TEXT("unknown op '%s' (add_variable, remove_variable, add_component, set_component, add_function, "
						"add_node, connect, disconnect, set_pins, remove_node, set_defaults)"), *Kind);
				}
				if (bOk)
				{
					++Applied;
				}
				else
				{
					TSharedPtr<FJsonObject> F = MakeShared<FJsonObject>();
					F->SetNumberField(TEXT("index"), I);
					F->SetStringField(TEXT("op"), Kind);
					F->SetStringField(TEXT("error"), Err);
					Failed.Add(MakeShared<FJsonValueObject>(F));
				}
			}
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);
	}

	// Lint: an impure node whose exec input is unwired never runs, and its outputs stay at their
	// defaults (None) at runtime with no compile error. Tell the caller now.
	TArray<FString> Warnings;
	for (const auto& Pair : Ctx.Aliases)
	{
		UEdGraphNode* N = Pair.Value;
		if (!N || Cast<UK2Node_Event>(N) || Cast<UK2Node_FunctionEntry>(N) || !IsValid(N) || !N->GetGraph())
		{
			continue;
		}
		for (UEdGraphPin* P : N->Pins)
		{
			if (P && P->Direction == EGPD_Input && P->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec && P->LinkedTo.Num() == 0)
			{
				Warnings.Add(FString::Printf(TEXT("'%s' (%s) has an unconnected exec input: it will never run and its outputs stay empty. "
					"Wire something into %s.%s"), *Pair.Key, *N->GetNodeTitle(ENodeTitleType::ListView).ToString(), *Pair.Key, *P->PinName.ToString()));
				break;
			}
		}
	}

	// A linked pin that is still a wildcard means a type never propagated (the compile error that
	// follows is vague: "The type of X is undetermined"). Name the node and pin.
	for (const auto& Pair : Ctx.Aliases)
	{
		UEdGraphNode* N = Pair.Value;
		if (!N || !IsValid(N) || !N->GetGraph()) continue;
		if (const UEdGraphPin* P = LinkedWildcardPin(N))
		{
			Warnings.Add(FString::Printf(TEXT("'%s' (%s) pin %s is still a wildcard although it is connected: connect a typed pin "
				"(e.g. an array variable) to it, or use a typed function"), *Pair.Key, *N->GetNodeTitle(ENodeTitleType::ListView).ToString(),
				*P->PinName.ToString()));
		}
	}

	TSharedPtr<FJsonObject> Compile = bCompile ? CompileBlueprintWithResults(BP) : nullptr;
	const bool bCompileOk = !Compile.IsValid() || Compile->GetBoolField(TEXT("ok"));
	const bool bRollback = bDryRun || (bAtomic && (Failed.Num() > 0 || !bCompileOk));
	bool bRolledBack = false;
	if (bRollback && !bCreated && GEditor && GEditor->Trans && GEditor->Trans->GetQueueLength() > QueueBefore)
	{
		GEditor->UndoTransaction(false);
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);
		if (bCompile) CompileBlueprintWithResults(BP);
		bRolledBack = true;
	}

	// Class defaults go on the freshly compiled class default object.
	TArray<FString> DefaultErrors;
	if (!bRollback && BP->GeneratedClass)
	{
		UObject* CDO = BP->GeneratedClass->GetDefaultObject();
		for (const TSharedPtr<FJsonObject>& Props : Ctx.DeferredDefaults)
		{
			FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(CDO, Props, DefaultErrors);
		}
		if (Ctx.DeferredDefaults.Num()) FBlueprintEditorUtils::MarkBlueprintAsModified(BP);
	}

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	const FString FinalPath = BP->GetPathName();
	if (bRollback && bCreated)
	{
		UEditorAssetLibrary::DeleteLoadedAsset(BP); // dry run / failed create leaves nothing behind
		bRolledBack = true;
	}
	bool bSaved = false;
	if (!bRollback && bSave)
	{
		bSaved = SaveAsset(BP);
	}
	const bool bOk = Failed.Num() == 0 && bCompileOk && DefaultErrors.Num() == 0;
	Out->SetBoolField(TEXT("ok"), bOk);
	Out->SetStringField(TEXT("blueprint"), FinalPath);
	Out->SetBoolField(TEXT("created"), bCreated && !bRolledBack);
	Out->SetNumberField(TEXT("applied"), Applied);
	Out->SetArrayField(TEXT("failed"), Failed);
	if (DefaultErrors.Num()) Out->SetField(TEXT("default_errors"), StringArray(DefaultErrors));
	if (Warnings.Num()) Out->SetField(TEXT("warnings"), StringArray(Warnings));
	TSharedPtr<FJsonObject> AliasJ = MakeShared<FJsonObject>();
	if (!bRolledBack)
	{
		for (const auto& Pair : Ctx.Aliases) AliasJ->SetStringField(Pair.Key, NodeId(Pair.Value));
	}
	Out->SetObjectField(TEXT("ids"), AliasJ);
	if (Compile.IsValid()) Out->SetObjectField(TEXT("compile"), Compile);
	Out->SetBoolField(TEXT("dry_run"), bDryRun);
	Out->SetBoolField(TEXT("rolled_back"), bRolledBack);
	Out->SetBoolField(TEXT("saved"), bSaved);
	return Out;
}

// ---------------------------------------------------------------------------------------------
// Shared graph machinery (animbp_apply builds on it)
// ---------------------------------------------------------------------------------------------

UEdGraph* MCPAuthoring::ResolveGraph(FGraphApplyContext& Ctx, const FString& Name)
{
	auto SubGraphOf = [](UEdGraphNode* N) -> UEdGraph*
	{
		if (!N) return nullptr;
		for (UEdGraph* G : N->GetSubGraphs())
		{
			if (G) return G;
		}
		return nullptr;
	};
	if (!Name.IsEmpty())
	{
		if (UEdGraphNode** A = Ctx.Aliases.Find(Name))
		{
			if (UEdGraph* G = SubGraphOf(*A)) return G;
		}
	}
	if (UEdGraph* G = FindGraph(Ctx.BP, Name))
	{
		return G;
	}
	TArray<UEdGraph*> All;
	AllGraphs(Ctx.BP, All);
	for (UEdGraph* G : All)
	{
		if (G && G->GetName().Equals(Name, ESearchCase::IgnoreCase)) return G;
	}
	return Name.Len() >= 8 ? SubGraphOf(FindNodeById(Ctx.BP, Name)) : nullptr;
}

UEdGraphNode* MCPAuthoring::ResolveNodeRef(FGraphApplyContext& Ctx, const FString& Ref)
{
	return ResolveRef(Ctx, Ref);
}

UEdGraphNode* MCPAuthoring::CreateGraphNode(FGraphApplyContext& Ctx, UEdGraph* Graph, const TSharedPtr<FJsonObject>& Spec, FString& Err)
{
	Graph->Modify();
	return CreateNodeFromOp(Ctx, Graph, Spec, Err);
}

void MCPAuthoring::PlaceGraphNode(FGraphApplyContext& Ctx, UEdGraph* Graph, UEdGraphNode* Node, const TSharedPtr<FJsonObject>& Spec)
{
	PlaceNode(Ctx, Graph, Node, Spec);
}

bool MCPAuthoring::ApplyPinDefaults(UEdGraphNode* Node, const TSharedPtr<FJsonObject>& Spec, FString& Err)
{
	return ApplyPins(Node, Spec, Err);
}

UEdGraphPin* MCPAuthoring::FindNodePin(UEdGraphNode* Node, const FString& Name, EEdGraphPinDirection Dir)
{
	return FindPin(Node, Name, Dir);
}

bool MCPAuthoring::LinkPins(UEdGraphNode* From, const FString& FromPin, UEdGraphNode* To, const FString& ToPin, FString& Err)
{
	FApplyContext Tmp;
	Tmp.Aliases.Add(TEXT("from"), From);
	Tmp.Aliases.Add(TEXT("to"), To);
	TSharedPtr<FJsonObject> Op = MakeShared<FJsonObject>();
	Op->SetStringField(TEXT("from"), TEXT("from.") + FromPin);
	Op->SetStringField(TEXT("to"), TEXT("to.") + ToPin);
	return OpConnect(Tmp, Op, Err);
}

FString MCPAuthoring::GraphNodeId(const UEdGraphNode* Node)
{
	return NodeId(Node);
}

TSharedPtr<FJsonObject> MCPAuthoring::ExportGraphNode(UEdGraphNode* Node, bool bPins)
{
	return ExportNode(Node, bPins);
}

TSharedPtr<FJsonObject> MCPAuthoring::ExportBlueprintSpec(UBlueprint* Blueprint, const TArray<FString>& OnlyGraphs, bool bPins)
{
	return ExportBlueprint(Blueprint, OnlyGraphs, bPins);
}

void FMCPBlueprintAuthoring::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("bp_export"), &HandleBpExport, false);
	// bp_apply manages its own transaction (so dry runs and failures can be undone as one step).
	Registry.Register(TEXT("bp_apply"), &HandleBpApply, false);
}
