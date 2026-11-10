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
#include "K2Node_InputKey.h"
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
#include "BlueprintTypePromotion.h"
#include "Curves/CurveFloat.h"
#include "Curves/CurveLinearColor.h"
#include "Curves/CurveVector.h"
#include "Engine/DataTable.h"
#include "Engine/TimelineTemplate.h"
#include "K2Node_AddDelegate.h"
#include "K2Node_AsyncAction.h"
#include "K2Node_BreakStruct.h"
#include "K2Node_CallDelegate.h"
#include "K2Node_ClearDelegate.h"
#include "K2Node_CreateDelegate.h"
#include "K2Node_FormatText.h"
#include "K2Node_GetDataTableRow.h"
#include "K2Node_MakeStruct.h"
#include "K2Node_Message.h"
#include "K2Node_PromotableOperator.h"
#include "K2Node_RemoveDelegate.h"
#include "K2Node_Select.h"
#include "K2Node_SwitchEnum.h"
#include "K2Node_SwitchInteger.h"
#include "K2Node_SwitchName.h"
#include "K2Node_SwitchString.h"
#include "K2Node_Timeline.h"
#include "K2Node_Tunnel.h"
#include "Kismet/BlueprintAsyncActionBase.h"
#include "UObject/Interface.h"

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

/** A class by name ("Actor", "BP_Door_C"), class path, or Blueprint asset path ("/Game/BP_Door" -> its generated class). */
UClass* MCPAuthoring::ResolveClassOrBlueprint(const FString& In)
{
	const FString S = In.TrimStartAndEnd();
	if (UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(S)) return C;
	if (S.StartsWith(TEXT("/")))
	{
		if (UBlueprint* B = Cast<UBlueprint>(LoadAssetOfClass(S.EndsWith(TEXT("_C")) ? S.LeftChop(2) : S, UBlueprint::StaticClass())))
		{
			return B->GeneratedClass ? B->GeneratedClass.Get() : B->SkeletonGeneratedClass.Get();
		}
		return nullptr;
	}
	return S.EndsWith(TEXT("_C")) ? nullptr : FEpicUnrealMCPReflectionCommands::ResolveClass(S + TEXT("_C"));
}

static UClass* ResolveClassEx(const FString& In)
{
	return MCPAuthoring::ResolveClassOrBlueprint(In);
}

/** "/Game/Data/S_Item", "/Game/Data/S_Item.S_Item", "/Script/Engine.HitResult" or a bare name ("S_Item"). */
template <typename T>
static T* FindByPathOrName(const FString& In)
{
	const FString S = In.TrimStartAndEnd();
	if (S.StartsWith(TEXT("/")))
	{
		if (T* O = LoadObject<T>(nullptr, *S, nullptr, LOAD_NoWarn | LOAD_Quiet)) return O;
		FString Pkg, Name, Err;
		if (SplitAssetPath(S, Pkg, Name, Err))
		{
			return LoadObject<T>(nullptr, *(Pkg + TEXT(".") + Name), nullptr, LOAD_NoWarn | LOAD_Quiet);
		}
		return nullptr;
	}
	for (TObjectIterator<T> It; It; ++It)
	{
		if (It->GetName().Equals(S, ESearchCase::IgnoreCase)) return *It;
	}
	return nullptr;
}

UScriptStruct* MCPAuthoring::FindStruct(const FString& NameOrPath) { return FindByPathOrName<UScriptStruct>(NameOrPath); }
UEnum* MCPAuthoring::FindEnum(const FString& NameOrPath) { return FindByPathOrName<UEnum>(NameOrPath); }

static FString TrackTypeName(int32 Type);

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
	if (H == TEXT("exec")) { Out.PinCategory = UEdGraphSchema_K2::PC_Exec; return true; } // macro / tunnel pins only
	if (H == TEXT("struct"))
	{
		UScriptStruct* SS = FindByPathOrName<UScriptStruct>(Arg);
		if (!SS) { Err = FString::Printf(TEXT("struct '%s' not found"), *Arg); return false; }
		return Struct(SS);
	}
	if (H == TEXT("enum"))
	{
		UEnum* E = FindByPathOrName<UEnum>(Arg);
		if (!E) { Err = FString::Printf(TEXT("enum '%s' not found"), *Arg); return false; }
		Out.PinCategory = UEdGraphSchema_K2::PC_Byte;
		Out.PinSubCategoryObject = E;
		return true;
	}
	if (H == TEXT("interface"))
	{
		UClass* IC = ResolveClassEx(Arg);
		if (!IC || !IC->HasAnyClassFlags(CLASS_Interface)) { Err = FString::Printf(TEXT("interface '%s' not found"), *Arg); return false; }
		Out.PinCategory = UEdGraphSchema_K2::PC_Interface;
		Out.PinSubCategoryObject = IC;
		return true;
	}
	const bool bObjectLike = H == TEXT("object") || H == TEXT("class") || H == TEXT("softobject") || H == TEXT("softclass");
	const FString ClassName = bObjectLike ? (Arg.IsEmpty() ? FString(TEXT("Object")) : Arg) : Head;
	if (UClass* C = ResolveClassEx(ClassName))
	{
		Out.PinCategory = H == TEXT("class") ? UEdGraphSchema_K2::PC_Class
			: H == TEXT("softobject") ? UEdGraphSchema_K2::PC_SoftObject
			: H == TEXT("softclass") ? UEdGraphSchema_K2::PC_SoftClass
			: UEdGraphSchema_K2::PC_Object;
		Out.PinSubCategoryObject = C;
		return true;
	}
	Err = FString::Printf(TEXT("unknown type '%s' (bool, int, float, string, name, text, vector, rotator, transform, "
		"object:<Class>, class:<Class>, interface:<Interface>, struct:<Struct>, enum:<Enum>, exec (macros), append [] for arrays)"), *In);
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
	else if (const UK2Node_Message* MS = Cast<UK2Node_Message>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("interface_message"));
		S->SetStringField(TEXT("function"), MS->FunctionReference.GetMemberName().ToString());
		if (UClass* Owner = MS->FunctionReference.GetMemberParentClass()) S->SetStringField(TEXT("interface"), Owner->GetPathName());
	}
	else if (N->IsA(UK2Node_PromotableOperator::StaticClass()))
	{
		const UK2Node_PromotableOperator* PO = static_cast<const UK2Node_PromotableOperator*>(N);
		S->SetStringField(TEXT("kind"), TEXT("operator"));
		S->SetStringField(TEXT("op"), PO->GetOperationName().ToString());
		S->SetStringField(TEXT("function"), PO->FunctionReference.GetMemberName().ToString());
	}
	else if (const UK2Node_BaseMCDelegate* MD = Cast<UK2Node_BaseMCDelegate>(N))
	{
		S->SetStringField(TEXT("kind"), Cast<UK2Node_CallDelegate>(N) ? TEXT("call_dispatcher") : Cast<UK2Node_AddDelegate>(N) ? TEXT("bind_dispatcher")
			: Cast<UK2Node_RemoveDelegate>(N) ? TEXT("unbind_dispatcher") : Cast<UK2Node_ClearDelegate>(N) ? TEXT("clear_dispatcher") : TEXT("dispatcher"));
		S->SetStringField(TEXT("dispatcher"), MD->DelegateReference.GetMemberName().ToString());
		if (!MD->DelegateReference.IsSelfContext())
		{
			if (UClass* Owner = MD->DelegateReference.GetMemberParentClass()) S->SetStringField(TEXT("class"), Owner->GetPathName());
		}
	}
	else if (const UK2Node_CreateDelegate* CD = Cast<UK2Node_CreateDelegate>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("create_delegate"));
		S->SetStringField(TEXT("function"), CD->GetFunctionName().ToString());
	}
	else if (const UK2Node_Timeline* TL = Cast<UK2Node_Timeline>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("timeline"));
		S->SetStringField(TEXT("name"), TL->TimelineName.ToString());
	}
	else if (const UK2Node_Select* SE = Cast<UK2Node_Select>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("select"));
		if (UEdGraphPin* I = SE->GetIndexPin()) S->SetStringField(TEXT("index_type"), DescribePinType(I->PinType));
	}
	else if (const UK2Node_Switch* SW = Cast<UK2Node_Switch>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("switch"));
		const UK2Node_SwitchEnum* SEn = Cast<UK2Node_SwitchEnum>(N);
		S->SetStringField(TEXT("on"), SEn ? (SEn->Enum ? TEXT("enum:") + SEn->Enum->GetName() : FString(TEXT("enum")))
			: Cast<UK2Node_SwitchString>(N) ? TEXT("string") : Cast<UK2Node_SwitchName>(N) ? TEXT("name") : TEXT("int"));
	}
	else if (const UK2Node_MakeStruct* MK = Cast<UK2Node_MakeStruct>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("make_struct"));
		if (MK->StructType) S->SetStringField(TEXT("struct"), MK->StructType->GetPathName());
	}
	else if (const UK2Node_BreakStruct* BK = Cast<UK2Node_BreakStruct>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("break_struct"));
		if (BK->StructType) S->SetStringField(TEXT("struct"), BK->StructType->GetPathName());
	}
	else if (Cast<UK2Node_FormatText>(N)) S->SetStringField(TEXT("kind"), TEXT("format_text"));
	else if (UK2Node_GetDataTableRow* DR = const_cast<UK2Node_GetDataTableRow*>(Cast<UK2Node_GetDataTableRow>(N)))
	{
		S->SetStringField(TEXT("kind"), TEXT("get_data_table_row"));
		if (UEdGraphPin* TP = DR->GetDataTablePin(); TP && TP->DefaultObject) S->SetStringField(TEXT("table"), TP->DefaultObject->GetPathName());
	}
	else if (Cast<UK2Node_BaseAsyncTask>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("async_action"));
		S->SetStringField(TEXT("class"), N->GetClass()->GetName());
	}
	else if (const UK2Node_CallFunction* CF = Cast<UK2Node_CallFunction>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("call_function"));
		if (const UFunction* Fn = CF->GetTargetFunction())
		{
			if (Fn->HasMetaData(FBlueprintMetadata::MD_Latent)) S->SetBoolField(TEXT("latent"), true);
		}
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
		if (VS->VariableReference.IsLocalScope()) S->SetBoolField(TEXT("local"), true);
	}
	else if (const UK2Node_VariableGet* VG = Cast<UK2Node_VariableGet>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("get_var"));
		S->SetStringField(TEXT("variable"), VG->VariableReference.GetMemberName().ToString());
		if (VG->VariableReference.IsLocalScope()) S->SetBoolField(TEXT("local"), true);
	}
	else if (const UK2Node_InputKey* IK = Cast<UK2Node_InputKey>(N))
	{
		S->SetStringField(TEXT("kind"), TEXT("key_event"));
		S->SetStringField(TEXT("key"), IK->InputKey.GetFName().ToString());
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
	else if (const UK2Node_Tunnel* TN = Cast<UK2Node_Tunnel>(N); TN && N->GetClass() == UK2Node_Tunnel::StaticClass())
	{
		S->SetStringField(TEXT("kind"), TN->bCanHaveOutputs ? TEXT("macro_entry") : TEXT("macro_exit"));
	}
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
	if (const UK2Node* K = Cast<UK2Node>(N); K && K->IsNodePure())
	{
		J->SetBoolField(TEXT("pure"), true);
	}
	if (!N->NodeComment.IsEmpty())
	{
		J->SetStringField(TEXT("comment"), N->NodeComment);
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

	J->SetStringField(TEXT("blueprint_type"), BP->BlueprintType == BPTYPE_Interface ? TEXT("interface")
		: BP->BlueprintType == BPTYPE_MacroLibrary ? TEXT("macro_library") : BP->BlueprintType == BPTYPE_FunctionLibrary ? TEXT("function_library")
		: TEXT("normal"));
	TArray<FString> Interfaces;
	for (const FBPInterfaceDescription& I : BP->ImplementedInterfaces)
	{
		if (I.Interface) Interfaces.Add(I.Interface->GetPathName());
	}
	J->SetField(TEXT("interfaces"), StringArray(Interfaces));

	// Pins a user declared on an entry/result/tunnel node: [{name, type}].
	auto UserPins = [](const UK2Node_EditablePinBase* N)
	{
		TArray<TSharedPtr<FJsonValue>> Out;
		if (N)
		{
			for (const TSharedPtr<FUserPinInfo>& P : N->UserDefinedPins)
			{
				TSharedPtr<FJsonObject> PJ = MakeShared<FJsonObject>();
				PJ->SetStringField(TEXT("name"), P->PinName.ToString());
				PJ->SetStringField(TEXT("type"), P->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec ? FString(TEXT("exec")) : DescribePinType(P->PinType));
				Out.Add(MakeShared<FJsonValueObject>(PJ));
			}
		}
		return Out;
	};
	TArray<TSharedPtr<FJsonValue>> Dispatchers;
	for (UEdGraph* G : BP->DelegateSignatureGraphs)
	{
		if (!G) continue;
		TArray<UK2Node_FunctionEntry*> Entries;
		G->GetNodesOfClass(Entries);
		TSharedPtr<FJsonObject> DJ = MakeShared<FJsonObject>();
		DJ->SetStringField(TEXT("name"), G->GetName());
		DJ->SetArrayField(TEXT("inputs"), UserPins(Entries.Num() ? Entries[0] : nullptr));
		Dispatchers.Add(MakeShared<FJsonValueObject>(DJ));
	}
	J->SetArrayField(TEXT("dispatchers"), Dispatchers);

	TArray<TSharedPtr<FJsonValue>> Timelines;
	for (UTimelineTemplate* T : BP->Timelines)
	{
		if (!T) continue;
		TSharedPtr<FJsonObject> TJ = MakeShared<FJsonObject>();
		TJ->SetStringField(TEXT("name"), T->GetVariableName().ToString());
		TJ->SetNumberField(TEXT("length"), T->TimelineLength);
		TJ->SetBoolField(TEXT("loop"), T->bLoop);
		TJ->SetBoolField(TEXT("autoplay"), T->bAutoPlay);
		TArray<TSharedPtr<FJsonValue>> Tracks;
		auto Track = [&](const FTTTrackBase& Tr, int32 Type, TArrayView<const FRichCurve* const> Curves)
		{
			TSharedPtr<FJsonObject> X = MakeShared<FJsonObject>();
			X->SetStringField(TEXT("name"), Tr.GetTrackName().ToString());
			X->SetStringField(TEXT("type"), TrackTypeName(Type));
			TArray<TSharedPtr<FJsonValue>> Keys;
			if (Curves.Num() && Curves[0])
			{
				for (int32 K = 0; K < Curves[0]->GetNumKeys(); ++K)
				{
					const float Time = Curves[0]->Keys[K].Time;
					TArray<TSharedPtr<FJsonValue>> Pair = { MakeShared<FJsonValueNumber>(Time) };
					if (Type == FTTTrackBase::TT_Event) { Keys.Add(MakeShared<FJsonValueNumber>(Time)); continue; }
					if (Curves.Num() == 1) Pair.Add(MakeShared<FJsonValueNumber>(Curves[0]->Eval(Time)));
					else
					{
						TArray<TSharedPtr<FJsonValue>> V;
						for (const FRichCurve* C : Curves) V.Add(MakeShared<FJsonValueNumber>(C ? C->Eval(Time) : 0.0));
						Pair.Add(MakeShared<FJsonValueArray>(V));
					}
					Keys.Add(MakeShared<FJsonValueArray>(Pair));
				}
			}
			X->SetArrayField(TEXT("keys"), Keys);
			Tracks.Add(MakeShared<FJsonValueObject>(X));
		};
		for (const FTTFloatTrack& Tr : T->FloatTracks)
		{
			const FRichCurve* C[] = { Tr.CurveFloat ? &Tr.CurveFloat->FloatCurve : nullptr };
			Track(Tr, FTTTrackBase::TT_FloatInterp, C);
		}
		for (const FTTVectorTrack& Tr : T->VectorTracks)
		{
			const FRichCurve* C[] = { Tr.CurveVector ? &Tr.CurveVector->FloatCurves[0] : nullptr, Tr.CurveVector ? &Tr.CurveVector->FloatCurves[1] : nullptr,
				Tr.CurveVector ? &Tr.CurveVector->FloatCurves[2] : nullptr };
			Track(Tr, FTTTrackBase::TT_VectorInterp, C);
		}
		for (const FTTLinearColorTrack& Tr : T->LinearColorTracks)
		{
			const UCurveLinearColor* LC = Tr.CurveLinearColor;
			const FRichCurve* C[] = { LC ? &LC->FloatCurves[0] : nullptr, LC ? &LC->FloatCurves[1] : nullptr, LC ? &LC->FloatCurves[2] : nullptr,
				LC ? &LC->FloatCurves[3] : nullptr };
			Track(Tr, FTTTrackBase::TT_LinearColorInterp, C);
		}
		for (const FTTEventTrack& Tr : T->EventTracks)
		{
			const FRichCurve* C[] = { Tr.CurveKeys ? &Tr.CurveKeys->FloatCurve : nullptr };
			Track(Tr, FTTTrackBase::TT_Event, C);
		}
		TJ->SetArrayField(TEXT("tracks"), Tracks);
		Timelines.Add(MakeShared<FJsonValueObject>(TJ));
	}
	J->SetArrayField(TEXT("timelines"), Timelines);

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
		TArray<UK2Node_FunctionEntry*> Entries;
		G->GetNodesOfClass(Entries);
		if (Entries.Num())
		{
			const UK2Node_FunctionEntry* E = Entries[0];
			GJ->SetArrayField(TEXT("inputs"), UserPins(E));
			TArray<UK2Node_FunctionResult*> Results;
			G->GetNodesOfClass(Results);
			GJ->SetArrayField(TEXT("outputs"), UserPins(Results.Num() ? Results[0] : nullptr));
			if (E->GetFunctionFlags() & FUNC_BlueprintPure) GJ->SetBoolField(TEXT("pure"), true);
			TArray<TSharedPtr<FJsonValue>> Locals;
			for (const FBPVariableDescription& V : E->LocalVariables)
			{
				TSharedPtr<FJsonObject> LJ = MakeShared<FJsonObject>();
				LJ->SetStringField(TEXT("name"), V.VarName.ToString());
				LJ->SetStringField(TEXT("type"), DescribePinType(V.VarType));
				if (!V.DefaultValue.IsEmpty()) LJ->SetStringField(TEXT("default"), V.DefaultValue);
				Locals.Add(MakeShared<FJsonValueObject>(LJ));
			}
			GJ->SetArrayField(TEXT("local_variables"), Locals);
		}
		else if (FCString::Strcmp(Kind, TEXT("macro")) == 0)
		{
			TArray<UK2Node_Tunnel*> Tunnels;
			G->GetNodesOfClass(Tunnels);
			for (const UK2Node_Tunnel* T : Tunnels)
			{
				if (T->GetClass() != UK2Node_Tunnel::StaticClass()) continue;
				GJ->SetArrayField(T->bCanHaveOutputs ? TEXT("inputs") : TEXT("outputs"), UserPins(T));
			}
		}
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
	// Events of implemented interfaces (functions with no outputs; ones with outputs are function graphs).
	TArray<UClass*> Interfaces;
	for (const FBPInterfaceDescription& I : BP->ImplementedInterfaces)
	{
		if (I.Interface) Interfaces.Add(I.Interface);
	}
	for (UClass* C = Parent; C; C = C->GetSuperClass())
	{
		for (const FImplementedInterface& I : C->Interfaces)
		{
			if (I.Class) Interfaces.AddUnique(I.Class);
		}
	}
	for (UClass* I : Interfaces)
	{
		for (TFieldIterator<UFunction> It(I, EFieldIteratorFlags::IncludeSuper); It; ++It)
		{
			if (It->HasAnyFunctionFlags(FUNC_BlueprintEvent) && (Norm(It->GetName()) == Norm(Name) || Norm(It->GetName()) == Norm(Base)))
			{
				return *It;
			}
		}
	}
	return nullptr;
}

static bool FunctionHasOutputs(const UFunction* F)
{
	for (TFieldIterator<FProperty> It(F); It && (It->PropertyFlags & CPF_Parm); ++It)
	{
		if (It->HasAnyPropertyFlags(CPF_ReturnParm) || (It->HasAnyPropertyFlags(CPF_OutParm) && !It->HasAnyPropertyFlags(CPF_ReferenceParm)))
		{
			return true;
		}
	}
	return false;
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

FString MCPAuthoring::EnumEntryName(const UEnum* E, const FString& In)
{
	FString Want = In;
	Want.Split(TEXT("::"), nullptr, &Want); // "EColor::Red"
	if (Want.IsEmpty()) Want = In;
	for (int32 I = 0; I < E->NumEnums() - (E->ContainsExistingMax() ? 1 : 0); ++I)
	{
		const FString Name = E->GetNameStringByIndex(I);
		if (Name.Equals(Want, ESearchCase::IgnoreCase) || E->GetDisplayNameTextByIndex(I).ToString().Equals(Want, ESearchCase::IgnoreCase)
			|| Norm(E->GetDisplayNameTextByIndex(I).ToString()) == Norm(Want))
		{
			return Name;
		}
	}
	if (Want.IsNumeric())
	{
		const int64 V = FCString::Atoi64(*Want);
		const int32 I = E->GetIndexByValue(V);
		if (I != INDEX_NONE) return E->GetNameStringByIndex(I);
	}
	return In;
}

static bool ApplyPinDefault(UEdGraphPin* Pin, const TSharedPtr<FJsonValue>& Value, FString& Err)
{
	const UEdGraphSchema_K2* K2 = GetDefault<UEdGraphSchema_K2>();
	if (NeedsWiredInput(Pin))
	{
		return WireLiteral(Pin, Value, Err);
	}
	const FName Cat = Pin->PinType.PinCategory;
	FString Str = JsonToDefaultString(Value);
	if (const UEnum* E = Cat == UEdGraphSchema_K2::PC_Byte ? Cast<UEnum>(Pin->PinType.PinSubCategoryObject.Get()) : nullptr)
	{
		Str = MCPAuthoring::EnumEntryName(E, Str); // user enums store "NewEnumerator0"; accept the display name
	}
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

// ---------------------------------------------------------------------------------------------
// Node kinds beyond calls and variables: dispatchers, interfaces, timelines, operators, flow
// ---------------------------------------------------------------------------------------------

static FString OpStr(const TSharedPtr<FJsonObject>& Op, const TCHAR* Field)
{
	FString V;
	Op->TryGetStringField(Field, V);
	return V;
}

/** An event dispatcher (multicast delegate property) on this Blueprint or on another class / Blueprint. */
static FMulticastDelegateProperty* FindDispatcher(FApplyContext& Ctx, const FString& Name, const FString& ClassHint, bool& bSelf, FString& Err)
{
	UBlueprint* BP = Ctx.BP;
	UClass* C = nullptr;
	bSelf = ClassHint.IsEmpty() || ClassHint.Equals(TEXT("self"), ESearchCase::IgnoreCase);
	if (!bSelf)
	{
		C = ResolveClassEx(ClassHint);
		if (!C)
		{
			Err = FString::Printf(TEXT("class '%s' not found (a Blueprint path like /Game/BP_Door works)"), *ClassHint);
			return nullptr;
		}
		if (C == BP->GeneratedClass || C == BP->SkeletonGeneratedClass) bSelf = true;
	}
	auto Find = [&]() -> FMulticastDelegateProperty*
	{
		UClass* Where = bSelf ? BP->SkeletonGeneratedClass.Get() : C;
		if (!Where) return nullptr;
		for (TFieldIterator<FMulticastDelegateProperty> It(Where, EFieldIteratorFlags::IncludeSuper); It; ++It)
		{
			if (Norm(It->GetName()) == Norm(Name)) return *It;
		}
		return nullptr;
	};
	FMulticastDelegateProperty* P = Find();
	if (!P && bSelf)
	{
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP); // a dispatcher added earlier in this batch
		P = Find();
	}
	if (!P)
	{
		TArray<FString> Names;
		if (UClass* Where = bSelf ? BP->SkeletonGeneratedClass.Get() : C)
		{
			for (TFieldIterator<FMulticastDelegateProperty> It(Where, EFieldIteratorFlags::IncludeSuper); It; ++It) Names.Add(It->GetName());
		}
		Err = FString::Printf(TEXT("no event dispatcher '%s' on %s (has: %s; add one with add_dispatcher)"), *Name,
			bSelf ? *BP->GetName() : *C->GetName(), Names.Num() ? *FString::Join(Names, TEXT(", ")) : TEXT("none"));
	}
	return P;
}

template <typename T>
static T* NewDelegateNode(UEdGraph* G, FMulticastDelegateProperty* Prop, bool bSelf)
{
	return NewNode<T>(G, [&](T* N) { N->SetFromProperty(Prop, bSelf, Prop->GetOwnerClass()); });
}

static UFunction* FindFunctionCI(UClass* C, const FString& Name)
{
	if (!C) return nullptr;
	if (UFunction* F = C->FindFunctionByName(FName(*Name))) return F;
	for (TFieldIterator<UFunction> It(C, EFieldIteratorFlags::IncludeSuper); It; ++It)
	{
		if (Norm(It->GetName()) == Norm(Name)) return *It;
	}
	return nullptr;
}

/** Custom event pins matching a delegate signature (so it can be bound to the dispatcher). */
static void AddSignaturePins(UK2Node_EditablePinBase* N, const UFunction* Sig)
{
	const UEdGraphSchema_K2* K2 = GetDefault<UEdGraphSchema_K2>();
	for (TFieldIterator<FProperty> It(Sig); It && (It->PropertyFlags & CPF_Parm); ++It)
	{
		if (It->HasAnyPropertyFlags(CPF_ReturnParm)) continue;
		FEdGraphPinType T;
		if (K2->ConvertPropertyToPinType(*It, T)) N->CreateUserDefinedPin(It->GetFName(), T, EGPD_Output);
	}
}

static ERichCurveInterpMode InterpMode(const FString& S)
{
	const FString L = S.ToLower();
	if (L == TEXT("constant") || L == TEXT("step")) return RCIM_Constant;
	if (L == TEXT("cubic") || L == TEXT("auto") || L == TEXT("smooth")) return RCIM_Cubic;
	return RCIM_Linear;
}

/** keys: [[t, v], ...] or [{"t":..,"v":..,"interp":..}]; v is a number, or [x,y,z(,w)] spread over Curves. */
static bool AddCurveKeys(const TArray<TSharedPtr<FJsonValue>>& Keys, TArrayView<FRichCurve*> Curves, const FString& DefaultInterp, FString& Err)
{
	for (const TSharedPtr<FJsonValue>& K : Keys)
	{
		double T = 0;
		TSharedPtr<FJsonValue> V;
		FString Interp = DefaultInterp;
		if (K->Type == EJson::Array && K->AsArray().Num() >= 2)
		{
			T = K->AsArray()[0]->AsNumber();
			V = K->AsArray()[1];
			if (K->AsArray().Num() > 2 && K->AsArray()[2]->Type == EJson::String) Interp = K->AsArray()[2]->AsString();
		}
		else if (K->Type == EJson::Object)
		{
			const TSharedPtr<FJsonObject> O = K->AsObject();
			if (!O->TryGetNumberField(TEXT("t"), T)) O->TryGetNumberField(TEXT("time"), T);
			V = O->HasField(TEXT("v")) ? O->TryGetField(TEXT("v")) : O->TryGetField(TEXT("value"));
			O->TryGetStringField(TEXT("interp"), Interp);
		}
		if (!V.IsValid())
		{
			Err = TEXT("curve keys are [time, value] or {\"t\":time,\"v\":value}");
			return false;
		}
		TArray<double> Vals;
		if (V->Type == EJson::Array) { for (const TSharedPtr<FJsonValue>& E : V->AsArray()) Vals.Add(E->AsNumber()); }
		else Vals.Add(V->AsNumber());
		if (Vals.Num() < Curves.Num() && !(Vals.Num() == 1))
		{
			Err = FString::Printf(TEXT("key at t=%g needs %d values"), T, Curves.Num());
			return false;
		}
		for (int32 I = 0; I < Curves.Num(); ++I)
		{
			const FKeyHandle H = Curves[I]->AddKey(float(T), float(Vals.Num() == 1 ? Vals[0] : Vals[I]));
			Curves[I]->SetKeyInterpMode(H, InterpMode(Interp));
		}
	}
	return true;
}

static FString TrackTypeName(int32 Type)
{
	switch (Type)
	{
	case FTTTrackBase::TT_Event: return TEXT("event");
	case FTTTrackBase::TT_FloatInterp: return TEXT("float");
	case FTTTrackBase::TT_VectorInterp: return TEXT("vector");
	case FTTTrackBase::TT_LinearColorInterp: return TEXT("color");
	default: return TEXT("unknown");
	}
}

/** Timeline node + template: length, loop, autoplay and float / vector / color / event tracks with keys. */
static UEdGraphNode* CreateTimelineNode(FApplyContext& Ctx, UEdGraph* G, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	UBlueprint* BP = Ctx.BP;
	if (!FBlueprintEditorUtils::DoesSupportTimelines(BP) || !BP->UbergraphPages.Contains(G))
	{
		Err = TEXT("timelines need an Actor Blueprint's event graph (not a function, macro, widget or component)");
		return nullptr;
	}
	if (!BP->GeneratedClass)
	{
		FKismetEditorUtilities::CompileBlueprint(BP, EBlueprintCompileOptions::SkipGarbageCollection); // curves are outered to the class
	}
	const FString Want = OpStr(Op, TEXT("name"));
	if (!Want.IsEmpty() && BP->FindTimelineTemplateByVariableName(FName(*Want)))
	{
		Err = FString::Printf(TEXT("timeline '%s' already exists"), *Want);
		return nullptr;
	}
	UK2Node_Timeline* N = NewNode<UK2Node_Timeline>(G, [](UK2Node_Timeline*) {});
	if (!Want.IsEmpty() && N->TimelineName != FName(*Want) && !FBlueprintEditorUtils::RenameTimeline(BP, N->TimelineName, FName(*Want)))
	{
		Err = FString::Printf(TEXT("cannot name the timeline '%s' (taken by a variable, component or function?)"), *Want);
		return nullptr;
	}
	UTimelineTemplate* TT = BP->FindTimelineTemplateByVariableName(N->TimelineName);
	if (!TT)
	{
		Err = TEXT("timeline template was not created");
		return nullptr;
	}
	TT->Modify();
	N->Modify();
	double Length = 0;
	if (Op->TryGetNumberField(TEXT("length"), Length)) TT->TimelineLength = float(Length);
	bool B = false;
	if (Op->TryGetBoolField(TEXT("loop"), B)) { TT->bLoop = B; N->bLoop = B; }
	if (Op->TryGetBoolField(TEXT("autoplay"), B)) { TT->bAutoPlay = B; N->bAutoPlay = B; }
	if (Op->TryGetBoolField(TEXT("use_last_keyframe"), B)) TT->LengthMode = B ? TL_LastKeyFrame : TL_TimelineLength;
	UClass* Owner = BP->GeneratedClass;
	const TArray<TSharedPtr<FJsonValue>>* Tracks = nullptr;
	if (Op->TryGetArrayField(TEXT("tracks"), Tracks))
	{
		for (const TSharedPtr<FJsonValue>& TV : *Tracks)
		{
			const TSharedPtr<FJsonObject> TO = TV->AsObject();
			if (!TO) { Err = TEXT("tracks are objects {name, type, keys}"); return nullptr; }
			const FName TrackName(*OpStr(TO, TEXT("name")));
			const FString Type = OpStr(TO, TEXT("type")).ToLower();
			const FString Interp = OpStr(TO, TEXT("interp"));
			if (TrackName.IsNone() || !TT->IsNewTrackNameValid(TrackName))
			{
				Err = FString::Printf(TEXT("track name '%s' is empty or already used"), *TrackName.ToString());
				return nullptr;
			}
			const TArray<TSharedPtr<FJsonValue>>* Keys = nullptr;
			TArray<TSharedPtr<FJsonValue>> NoKeys;
			if (!TO->TryGetArrayField(TEXT("keys"), Keys)) Keys = &NoKeys;
			FTTTrackId Id;
			if (Type.IsEmpty() || Type == TEXT("float"))
			{
				FTTFloatTrack T;
				T.SetTrackName(TrackName, TT);
				T.CurveFloat = NewObject<UCurveFloat>(Owner, NAME_None, RF_Public | RF_Transactional);
				FRichCurve* C[] = { &T.CurveFloat->FloatCurve };
				if (!AddCurveKeys(*Keys, C, Interp, Err)) return nullptr;
				Id = FTTTrackId(FTTTrackBase::TT_FloatInterp, TT->FloatTracks.Add(T));
			}
			else if (Type == TEXT("vector"))
			{
				FTTVectorTrack T;
				T.SetTrackName(TrackName, TT);
				T.CurveVector = NewObject<UCurveVector>(Owner, NAME_None, RF_Public | RF_Transactional);
				FRichCurve* C[] = { &T.CurveVector->FloatCurves[0], &T.CurveVector->FloatCurves[1], &T.CurveVector->FloatCurves[2] };
				if (!AddCurveKeys(*Keys, C, Interp, Err)) return nullptr;
				Id = FTTTrackId(FTTTrackBase::TT_VectorInterp, TT->VectorTracks.Add(T));
			}
			else if (Type == TEXT("color") || Type == TEXT("linearcolor") || Type == TEXT("colour"))
			{
				FTTLinearColorTrack T;
				T.SetTrackName(TrackName, TT);
				T.CurveLinearColor = NewObject<UCurveLinearColor>(Owner, NAME_None, RF_Public | RF_Transactional);
				FRichCurve* C[] = { &T.CurveLinearColor->FloatCurves[0], &T.CurveLinearColor->FloatCurves[1],
					&T.CurveLinearColor->FloatCurves[2], &T.CurveLinearColor->FloatCurves[3] };
				if (!AddCurveKeys(*Keys, C, Interp, Err)) return nullptr;
				Id = FTTTrackId(FTTTrackBase::TT_LinearColorInterp, TT->LinearColorTracks.Add(T));
			}
			else if (Type == TEXT("event"))
			{
				FTTEventTrack T;
				T.SetTrackName(TrackName, TT);
				T.CurveKeys = NewObject<UCurveFloat>(Owner, NAME_None, RF_Public | RF_Transactional);
				T.CurveKeys->bIsEventCurve = true;
				for (const TSharedPtr<FJsonValue>& K : *Keys) // event keys are times: [0.5, 1.0] or [[0.5, 0]]
				{
					const double Time = K->Type == EJson::Array && K->AsArray().Num() ? K->AsArray()[0]->AsNumber() : K->AsNumber();
					T.CurveKeys->FloatCurve.AddKey(float(Time), 0.f);
				}
				Id = FTTTrackId(FTTTrackBase::TT_Event, TT->EventTracks.Add(T));
			}
			else
			{
				Err = FString::Printf(TEXT("track type '%s' (float, vector, color, event)"), *Type);
				return nullptr;
			}
			TT->AddDisplayTrack(Id);
		}
	}
	N->ReconstructNode(); // a pin per track
	return N;
}

static FName OperatorName(const FString& In)
{
	static const TMap<FString, FName> Aliases = {
		{ TEXT("+"), TEXT("Add") }, { TEXT("add"), TEXT("Add") }, { TEXT("-"), TEXT("Subtract") }, { TEXT("subtract"), TEXT("Subtract") },
		{ TEXT("*"), TEXT("Multiply") }, { TEXT("multiply"), TEXT("Multiply") }, { TEXT("/"), TEXT("Divide") }, { TEXT("divide"), TEXT("Divide") },
		{ TEXT(">"), TEXT("Greater") }, { TEXT("greater"), TEXT("Greater") }, { TEXT(">="), TEXT("GreaterEqual") }, { TEXT("greaterequal"), TEXT("GreaterEqual") },
		{ TEXT("<"), TEXT("Less") }, { TEXT("less"), TEXT("Less") }, { TEXT("<="), TEXT("LessEqual") }, { TEXT("lessequal"), TEXT("LessEqual") },
		{ TEXT("=="), TEXT("EqualEqual") }, { TEXT("equal"), TEXT("EqualEqual") }, { TEXT("equalequal"), TEXT("EqualEqual") },
		{ TEXT("!="), TEXT("NotEqual") }, { TEXT("notequal"), TEXT("NotEqual") },
	};
	const FName* N = Aliases.Find(Norm(In));
	return N ? *N : FName();
}

/** The 5.x promotable operator node (Add, Multiply, Greater, ...): wildcard pins that take the type of what is connected,
 *  or of "type" when given (float, int, vector, ...). */
static UEdGraphNode* CreateOperatorNode(UEdGraph* G, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FName OpName = OperatorName(OpStr(Op, TEXT("op")).IsEmpty() ? OpStr(Op, TEXT("operator")) : OpStr(Op, TEXT("op")));
	if (OpName.IsNone())
	{
		Err = TEXT("operator \"op\" is one of + - * / > >= < <= == != (or Add, Subtract, Multiply, Divide, Greater, GreaterEqual, Less, "
			"LessEqual, EqualEqual, NotEqual)");
		return nullptr;
	}
	TArray<UFunction*> Funcs;
	FTypePromotion::GetAllFuncsForOp(OpName, Funcs);
	if (Funcs.Num() == 0)
	{
		Err = FString::Printf(TEXT("no functions registered for operator %s (type promotion disabled?)"), *OpName.ToString());
		return nullptr;
	}
	FEdGraphPinType Want;
	const FString TypeStr = OpStr(Op, TEXT("type"));
	if (!TypeStr.IsEmpty() && !ParsePinType(TypeStr, Want, Err)) return nullptr;
	UFunction* Pick = Funcs[0];
	if (!TypeStr.IsEmpty())
	{
		const UEdGraphSchema_K2* K2 = GetDefault<UEdGraphSchema_K2>();
		for (UFunction* F : Funcs)
		{
			int32 Inputs = 0;
			bool bAll = true;
			for (TFieldIterator<FProperty> It(F); It && (It->PropertyFlags & CPF_Parm) && Inputs < 2; ++It)
			{
				if (It->HasAnyPropertyFlags(CPF_ReturnParm)) continue;
				FEdGraphPinType PT;
				K2->ConvertPropertyToPinType(*It, PT);
				bAll &= PT.PinCategory == Want.PinCategory && PT.PinSubCategoryObject == Want.PinSubCategoryObject;
				++Inputs;
			}
			if (bAll && Inputs == 2) { Pick = F; break; }
		}
	}
	UClass* NodeClass = UK2Node_PromotableOperator::StaticClass();
	G->Modify();
	UK2Node_CallFunction* N = NewObject<UK2Node_CallFunction>(G, NodeClass, NAME_None, RF_Transactional);
	G->AddNode(N, false, false);
	N->CreateNewGuid();
	N->SetFromFunction(Pick);
	N->PostPlacedNewNode();
	if (N->Pins.Num() == 0) N->AllocateDefaultPins();
	if (!TypeStr.IsEmpty())
	{
		UK2Node_PromotableOperator* P = static_cast<UK2Node_PromotableOperator*>(N);
		for (UEdGraphPin* Pin : TArray<UEdGraphPin*>(N->Pins))
		{
			if (Pin && Pin->Direction == EGPD_Input && Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Wildcard)
			{
				P->ConvertPinType(Pin, Want);
				break; // the node propagates the type to its other wildcard pins
			}
		}
	}
	return N;
}

/** Select (pure): N option pins chosen by an int, bool or enum index. */
static UEdGraphNode* CreateSelectNode(UEdGraph* G, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	UK2Node_Select* N = NewNode<UK2Node_Select>(G, [](UK2Node_Select*) {});
	const FString IndexType = OpStr(Op, TEXT("index_type"));
	if (!IndexType.IsEmpty())
	{
		FEdGraphPinType T;
		if (!ParsePinType(IndexType, T, Err)) return nullptr;
		if (T.PinCategory != UEdGraphSchema_K2::PC_Int && T.PinCategory != UEdGraphSchema_K2::PC_Boolean && T.PinCategory != UEdGraphSchema_K2::PC_Byte)
		{
			Err = TEXT("select index_type is int, bool, byte or enum:<Enum>");
			return nullptr;
		}
		UEdGraphPin* Index = N->GetIndexPin();
		Index->PinType = T;
		N->PinTypeChanged(Index);
		N->ReconstructNode();
	}
	double Options = 0;
	if (Op->TryGetNumberField(TEXT("options"), Options))
	{
		TArray<UEdGraphPin*> Opts;
		N->GetOptionPins(Opts);
		for (int32 I = Opts.Num(); I < int32(Options) && N->CanAddPin(); ++I) N->AddInputPin();
	}
	const FString ValueType = OpStr(Op, TEXT("type"));
	if (!ValueType.IsEmpty())
	{
		FEdGraphPinType T;
		if (!ParsePinType(ValueType, T, Err)) return nullptr;
		TArray<UEdGraphPin*> Opts;
		N->GetOptionPins(Opts);
		if (Opts.Num())
		{
			Opts[0]->PinType = T;
			N->PinTypeChanged(Opts[0]); // sets every option pin and the return value
			N->ReconstructNode();
		}
	}
	return N;
}

/** Switch on int / string / name / enum: an exec output per case (plus Default). */
static UEdGraphNode* CreateSwitchNode(UEdGraph* G, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	FString On = OpStr(Op, TEXT("on"));
	if (On.IsEmpty()) On = TEXT("int");
	const FString L = On.ToLower();
	TArray<FString> Cases;
	Op->TryGetStringArrayField(TEXT("cases"), Cases);
	if (Cases.Num() == 0)
	{
		const TArray<TSharedPtr<FJsonValue>>* Raw = nullptr;
		if (Op->TryGetArrayField(TEXT("cases"), Raw)) for (const TSharedPtr<FJsonValue>& V : *Raw) Cases.Add(JsonToDefaultString(V));
	}
	bool bDefault = true;
	Op->TryGetBoolField(TEXT("default_pin"), bDefault);
	UK2Node_Switch* N = nullptr;
	if (L == TEXT("int") || L == TEXT("integer"))
	{
		double Start = 0, Count = Cases.Num() ? Cases.Num() : 2;
		if (Cases.Num()) Start = FCString::Atoi(*Cases[0]);
		Op->TryGetNumberField(TEXT("start"), Start);
		Op->TryGetNumberField(TEXT("count"), Count);
		UK2Node_SwitchInteger* S = NewNode<UK2Node_SwitchInteger>(G, [&](UK2Node_SwitchInteger* X) { X->StartIndex = int32(Start); X->bHasDefaultPin = bDefault; });
		for (int32 I = 0; I < int32(Count); ++I) S->AddPinToSwitchNode();
		N = S;
	}
	else if (L == TEXT("string") || L == TEXT("name"))
	{
		if (Cases.Num() == 0)
		{
			Err = TEXT("switch on string/name needs \"cases\": [\"A\", \"B\", ...]");
			return nullptr;
		}
		TArray<FName> Names;
		for (const FString& C : Cases) Names.Add(FName(*C));
		if (L == TEXT("string"))
		{
			N = NewNode<UK2Node_SwitchString>(G, [&](UK2Node_SwitchString* X) { X->PinNames = Names; X->bHasDefaultPin = bDefault; });
		}
		else
		{
			N = NewNode<UK2Node_SwitchName>(G, [&](UK2Node_SwitchName* X) { X->PinNames = Names; X->bHasDefaultPin = bDefault; });
		}
	}
	else if (L.StartsWith(TEXT("enum")))
	{
		FString EnumName;
		On.Split(TEXT(":"), nullptr, &EnumName);
		UEnum* E = FindByPathOrName<UEnum>(EnumName.IsEmpty() ? OpStr(Op, TEXT("enum")) : EnumName);
		if (!E)
		{
			Err = FString::Printf(TEXT("enum '%s' not found (\"on\":\"enum:/Game/Data/E_Rarity\")"), *EnumName);
			return nullptr;
		}
		N = NewNode<UK2Node_SwitchEnum>(G, [&](UK2Node_SwitchEnum* X) { X->SetEnum(E); X->bHasDefaultPin = false; });
	}
	else
	{
		Err = FString::Printf(TEXT("switch \"on\" is int, string, name or enum:<Enum>, not '%s'"), *On);
		return nullptr;
	}
	return N;
}

/** Make / Break a struct: the struct's native make function when it has one (vectors, ...), else the generic node. */
static UEdGraphNode* CreateStructNode(UEdGraph* G, bool bMake, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	UScriptStruct* S = FindByPathOrName<UScriptStruct>(OpStr(Op, TEXT("struct")));
	if (!S)
	{
		Err = FString::Printf(TEXT("struct '%s' not found (/Game/Data/S_Item, Vector, HitResult, ...)"), *OpStr(Op, TEXT("struct")));
		return nullptr;
	}
	const FName Meta = bMake ? FBlueprintMetadata::MD_NativeMakeFunction : FBlueprintMetadata::MD_NativeBreakFunction;
	if (S->HasMetaData(Meta))
	{
		if (UFunction* F = FindObject<UFunction>(nullptr, *S->GetMetaData(Meta)))
		{
			return NewCallFunctionNode(G, F);
		}
	}
	if (bMake)
	{
		if (!UK2Node_MakeStruct::CanBeMade(S))
		{
			Err = FString::Printf(TEXT("struct %s cannot be made in Blueprints (not BlueprintType?)"), *S->GetName());
			return nullptr;
		}
		return NewNode<UK2Node_MakeStruct>(G, [S](UK2Node_MakeStruct* N) { N->StructType = S; });
	}
	return NewNode<UK2Node_BreakStruct>(G, [S](UK2Node_BreakStruct* N) { N->StructType = S; });
}

/** A latent "async action" node from its factory function (UBlueprintAsyncActionBase), or AI Move To. */
static UEdGraphNode* CreateAsyncActionNode(FApplyContext& Ctx, UEdGraph* G, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString Fn = OpStr(Op, TEXT("function"));
	if (Norm(Fn) == TEXT("aimoveto") || Norm(Fn) == TEXT("moveto"))
	{
		UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(TEXT("K2Node_AIMoveTo"));
		if (!C)
		{
			Err = TEXT("K2Node_AIMoveTo is not loaded (AIGraph module)");
			return nullptr;
		}
		G->Modify();
		UEdGraphNode* N = NewObject<UEdGraphNode>(G, C, NAME_None, RF_Transactional);
		G->AddNode(N, false, false);
		N->CreateNewGuid();
		N->PostPlacedNewNode();
		N->AllocateDefaultPins();
		return N;
	}
	UClass* C = ResolveClassEx(OpStr(Op, TEXT("class")));
	UFunction* F = C ? FindFunctionCI(C, Fn) : nullptr;
	if (!F)
	{
		// Without a class, look through every async action class (they are few).
		for (TObjectIterator<UClass> It; It && !F; ++It)
		{
			if (It->IsChildOf(UBlueprintAsyncActionBase::StaticClass()) && !It->HasAnyClassFlags(CLASS_Abstract)) F = FindFunctionCI(*It, Fn);
		}
	}
	const FObjectProperty* Ret = F ? CastField<FObjectProperty>(F->GetReturnProperty()) : nullptr;
	if (!F || !F->HasAnyFunctionFlags(FUNC_Static) || !Ret || !Ret->PropertyClass->IsChildOf(UBlueprintAsyncActionBase::StaticClass()))
	{
		Err = FString::Printf(TEXT("'%s' is not an async action factory (a static function returning a BlueprintAsyncActionBase; "
			"\"AIMoveTo\" works too, Delay is a call_function)"), *Fn);
		return nullptr;
	}
	return NewNode<UK2Node_AsyncAction>(G, [F](UK2Node_AsyncAction* N) { N->InitializeProxyFromFunction(F); });
}

static UEdGraph* FindMacroGraph(UBlueprint* BP, const FString& Name, const FString& Library)
{
	for (UEdGraph* G : BP->MacroGraphs)
	{
		if (G && Norm(G->GetName()) == Norm(Name)) return G;
	}
	if (!Library.IsEmpty())
	{
		if (UBlueprint* Lib = Cast<UBlueprint>(LoadAssetOfClass(Library, UBlueprint::StaticClass())))
		{
			for (UEdGraph* G : Lib->MacroGraphs)
			{
				if (G && Norm(G->GetName()) == Norm(Name)) return G;
			}
		}
	}
	return nullptr;
}

/** Local variable of the function graph G, if it declares one with this name. */
static const FBPVariableDescription* FindLocalVariable(UEdGraph* G, const FName Var)
{
	TArray<UK2Node_FunctionEntry*> Entries;
	if (G) G->GetNodesOfClass(Entries);
	for (UK2Node_FunctionEntry* E : Entries)
	{
		for (const FBPVariableDescription& V : E->LocalVariables)
		{
			if (V.VarName == Var) return &V;
		}
	}
	return nullptr;
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
		if (F->GetOwnerClass() && F->GetOwnerClass()->HasAnyClassFlags(CLASS_Interface) && FunctionHasOutputs(F))
		{
			Err = FString::Printf(TEXT("interface function '%s' returns values, so it is a function graph, not an event: add its nodes with "
				"\"graph\":\"%s\" (implement_interface created it)"), *F->GetName(), *F->GetName());
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
		const FString Dispatcher = Str(TEXT("dispatcher"));
		if (!Dispatcher.IsEmpty())
		{
			// Same parameters as the dispatcher, so it can be bound with bind_dispatcher.
			bool bSelf = true;
			FMulticastDelegateProperty* Prop = FindDispatcher(Ctx, Dispatcher, Str(TEXT("class")), bSelf, Err);
			if (!Prop) return nullptr;
			if (Prop->SignatureFunction) AddSignaturePins(N, Prop->SignatureFunction);
		}
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
		// A class the Blueprint itself is (its parent or any ancestor, or its own generated class) means an inherited
		// or own variable: reference it as a self member. As an external member it would need a Target pin wired to
		// self and fail to compile.
		if (External && (External == BP->GeneratedClass || External == BP->SkeletonGeneratedClass
			|| (BP->ParentClass && BP->ParentClass->IsChildOf(External))))
		{
			External = nullptr;
		}
		if (!External && !FindLocalVariable(G, Var) && BP->SkeletonGeneratedClass && !FindFProperty<FProperty>(BP->SkeletonGeneratedClass, Var))
		{
			// Fresh variables/components need the skeleton regenerated before they resolve.
			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);
		}
		const FBPVariableDescription* Local = External ? nullptr : FindLocalVariable(G, Var);
		auto Setup = [&](FMemberReference& Ref)
		{
			if (Local) Ref.SetLocalMember(Var, G->GetName(), Local->VarGuid);
			else if (External) Ref.SetExternalMember(Var, External);
			else Ref.SetSelfMember(Var);
		};
		UEdGraphNode* N = Kind == TEXT("get_var")
			? static_cast<UEdGraphNode*>(NewNode<UK2Node_VariableGet>(G, [&](UK2Node_VariableGet* V) { Setup(V->VariableReference); }))
			: static_cast<UEdGraphNode*>(NewNode<UK2Node_VariableSet>(G, [&](UK2Node_VariableSet* V) { Setup(V->VariableReference); }));
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("key_event") || Kind == TEXT("input_key"))
	{
		// K2Node_InputKey: "Pressed" / "Released" exec outputs and a "Key" output. The actor must receive input
		// (a possessed pawn, its PlayerController, or EnableInput on any other actor).
		const FKey Key(*Str(TEXT("key")));
		if (!Key.IsValid())
		{
			Err = FString::Printf(TEXT("unknown key '%s' (E, SpaceBar, Enter, Escape, LeftMouseButton, Gamepad_FaceButton_Bottom, ...)"), *Str(TEXT("key")));
			return nullptr;
		}
		if (!BP->UbergraphPages.Contains(G))
		{
			Err = TEXT("key_event nodes can only go in an event graph (EventGraph), not in a function");
			return nullptr;
		}
		auto Flag = [&Op](const TCHAR* F, bool bDefault) { bool B = bDefault; Op->TryGetBoolField(F, B); return B; };
		UK2Node_InputKey* N = NewNode<UK2Node_InputKey>(G, [&](UK2Node_InputKey* K)
		{
			K->InputKey = Key;
			K->bConsumeInput = Flag(TEXT("consume_input"), true);
			K->bExecuteWhenPaused = Flag(TEXT("execute_when_paused"), false);
			K->bOverrideParentBinding = Flag(TEXT("override_parent_binding"), true);
			K->bControl = Flag(TEXT("ctrl"), false);
			K->bAlt = Flag(TEXT("alt"), false);
			K->bShift = Flag(TEXT("shift"), false);
			K->bCommand = Flag(TEXT("cmd"), false);
		});
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
		UEdGraph* Macro = FindMacroGraph(BP, Str(TEXT("macro")), Str(TEXT("library")));
		if (!Macro) Macro = StandardMacro(Str(TEXT("macro")));
		if (!Macro)
		{
			Err = FString::Printf(TEXT("macro '%s' not found: not one of this Blueprint's (add_macro), of \"library\", or a standard macro "
				"(ForLoop, ForEachLoop, WhileLoop, DoOnce, DoN, Gate, FlipFlop, IsValid)"), *Str(TEXT("macro")));
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
	if (Kind == TEXT("interface_message") || Kind == TEXT("message") || Kind == TEXT("interface_call"))
	{
		UClass* I = ResolveClassEx(Str(TEXT("interface")).IsEmpty() ? Str(TEXT("class")) : Str(TEXT("interface")));
		if (!I || !I->HasAnyClassFlags(CLASS_Interface))
		{
			Err = FString::Printf(TEXT("interface '%s' not found (a Blueprint Interface path like /Game/BPI_Interact, or a native interface)"),
				*Str(TEXT("interface")));
			return nullptr;
		}
		UFunction* F = FindFunctionCI(I, Str(TEXT("function")));
		if (!F)
		{
			TArray<FString> Names;
			for (TFieldIterator<UFunction> It(I); It; ++It) Names.Add(It->GetName());
			Err = FString::Printf(TEXT("interface %s has no function '%s' (has: %s)"), *I->GetName(), *Str(TEXT("function")), *FString::Join(Names, TEXT(", ")));
			return nullptr;
		}
		// Message: callable on any object (does nothing if it doesn't implement the interface). Call: on an interface-typed target.
		UEdGraphNode* N = Kind == TEXT("interface_call")
			? static_cast<UEdGraphNode*>(NewNode<UK2Node_CallFunction>(G, [F](UK2Node_CallFunction* C) { C->SetFromFunction(F); }))
			: static_cast<UEdGraphNode*>(NewNode<UK2Node_Message>(G, [F](UK2Node_Message* M) { M->SetFromFunction(F); }));
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("call_dispatcher") || Kind == TEXT("bind_dispatcher") || Kind == TEXT("unbind_dispatcher")
		|| Kind == TEXT("unbind_all") || Kind == TEXT("clear_dispatcher"))
	{
		bool bSelf = true;
		FMulticastDelegateProperty* Prop = FindDispatcher(Ctx, Str(TEXT("dispatcher")), Str(TEXT("class")), bSelf, Err);
		if (!Prop) return nullptr;
		UK2Node_BaseMCDelegate* N = Kind == TEXT("call_dispatcher") ? static_cast<UK2Node_BaseMCDelegate*>(NewDelegateNode<UK2Node_CallDelegate>(G, Prop, bSelf))
			: Kind == TEXT("bind_dispatcher") ? static_cast<UK2Node_BaseMCDelegate*>(NewDelegateNode<UK2Node_AddDelegate>(G, Prop, bSelf))
			: Kind == TEXT("unbind_dispatcher") ? static_cast<UK2Node_BaseMCDelegate*>(NewDelegateNode<UK2Node_RemoveDelegate>(G, Prop, bSelf))
			: static_cast<UK2Node_BaseMCDelegate*>(NewDelegateNode<UK2Node_ClearDelegate>(G, Prop, bSelf));
		PlaceNode(Ctx, G, N, Op);
		const FString EventRef = Str(TEXT("event"));
		if (!EventRef.IsEmpty() && Kind != TEXT("call_dispatcher"))
		{
			// "event": a custom event (alias or id) whose delegate output goes into the Event pin.
			UEdGraphNode* Ev = ResolveRef(Ctx, EventRef);
			UEdGraphPin* Out = Ev ? Ev->FindPin(UK2Node_Event::DelegateOutputName, EGPD_Output) : nullptr;
			UEdGraphPin* In = N->GetDelegatePin();
			if (!Out || !In || !GetDefault<UEdGraphSchema_K2>()->TryCreateConnection(Out, In))
			{
				Err = FString::Printf(TEXT("could not bind event '%s' (a custom_event with the dispatcher's signature: add it with "
					"\"dispatcher\":\"%s\")"), *EventRef, *Prop->GetName());
				return nullptr;
			}
		}
		return N;
	}
	if (Kind == TEXT("create_delegate") || Kind == TEXT("create_event"))
	{
		UK2Node_CreateDelegate* N = NewNode<UK2Node_CreateDelegate>(G, [](UK2Node_CreateDelegate*) {});
		const FString Fn = Str(TEXT("function"));
		if (!Fn.IsEmpty()) N->SetFunction(FName(*Fn));
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("timeline"))
	{
		UEdGraphNode* N = CreateTimelineNode(Ctx, G, Op, Err);
		if (N) PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("operator") || Kind == TEXT("promotable_operator"))
	{
		UEdGraphNode* N = CreateOperatorNode(G, Op, Err);
		if (N) PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("select"))
	{
		UEdGraphNode* N = CreateSelectNode(G, Op, Err);
		if (N) PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("switch"))
	{
		UEdGraphNode* N = CreateSwitchNode(G, Op, Err);
		if (N) PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("make_struct") || Kind == TEXT("break_struct"))
	{
		UEdGraphNode* N = CreateStructNode(G, Kind == TEXT("make_struct"), Op, Err);
		if (N) PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("format_text"))
	{
		UK2Node_FormatText* N = NewNode<UK2Node_FormatText>(G, [](UK2Node_FormatText*) {});
		const FString Format = Str(TEXT("format"));
		if (!Format.IsEmpty())
		{
			UEdGraphPin* FP = N->GetFormatPin();
			GetDefault<UEdGraphSchema_K2>()->TrySetDefaultText(*FP, FText::FromString(Format));
			N->PinDefaultValueChanged(FP); // one wildcard argument pin per {Name}
		}
		PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("async_action") || Kind == TEXT("ai_move_to"))
	{
		if (Kind == TEXT("ai_move_to")) Op->SetStringField(TEXT("function"), TEXT("AIMoveTo"));
		UEdGraphNode* N = CreateAsyncActionNode(Ctx, G, Op, Err);
		if (N) PlaceNode(Ctx, G, N, Op);
		return N;
	}
	if (Kind == TEXT("get_data_table_row"))
	{
		UK2Node_GetDataTableRow* N = NewNode<UK2Node_GetDataTableRow>(G, [](UK2Node_GetDataTableRow*) {});
		const FString Table = Str(TEXT("table"));
		if (!Table.IsEmpty())
		{
			UDataTable* DT = Cast<UDataTable>(LoadAssetOfClass(Table, UDataTable::StaticClass()));
			if (!DT)
			{
				Err = FString::Printf(TEXT("DataTable '%s' not found"), *Table);
				return nullptr;
			}
			GetDefault<UEdGraphSchema_K2>()->TrySetDefaultObject(*N->GetDataTablePin(), DT); // types the Out Row pin
		}
		const FString Row = Str(TEXT("row"));
		if (!Row.IsEmpty() && N->GetRowNamePin()) GetDefault<UEdGraphSchema_K2>()->TrySetDefaultValue(*N->GetRowNamePin(), Row);
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
		"key_event, branch, sequence, cast, self, macro, spawn_actor, interface_message, interface_call, call_dispatcher, bind_dispatcher, "
		"unbind_dispatcher, clear_dispatcher, create_delegate, timeline, operator, select, switch, make_struct, break_struct, format_text, "
		"async_action, ai_move_to, get_data_table_row, node)"), *Kind);
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

static bool OpImplementInterface(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString Name = OpStr(Op, TEXT("interface"));
	UClass* I = ResolveClassEx(Name);
	if (!I || !I->HasAnyClassFlags(CLASS_Interface))
	{
		Err = FString::Printf(TEXT("interface '%s' not found (a Blueprint Interface asset path, or a native interface class)"), *Name);
		return false;
	}
	for (const FBPInterfaceDescription& D : Ctx.BP->ImplementedInterfaces)
	{
		if (D.Interface == I) return true; // already implemented: nothing to do
	}
	if (!FBlueprintEditorUtils::ImplementNewInterface(Ctx.BP, FTopLevelAssetPath(I->GetPathName())))
	{
		Err = FString::Printf(TEXT("could not implement %s (a function name clash?)"), *I->GetName());
		return false;
	}
	Ctx.bStructural = true;
	return true;
}

static bool OpAddDispatcher(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	UBlueprint* BP = Ctx.BP;
	const FName Name(*OpStr(Op, TEXT("name")));
	const UEdGraphSchema_K2* K2 = GetDefault<UEdGraphSchema_K2>();
	if (Name.IsNone() || FindGraph(BP, Name.ToString()) || FBlueprintEditorUtils::FindNewVariableIndex(BP, Name) != INDEX_NONE)
	{
		Err = FString::Printf(TEXT("dispatcher name '%s' is empty or already used"), *Name.ToString());
		return false;
	}
	FEdGraphPinType DelegateType;
	DelegateType.PinCategory = UEdGraphSchema_K2::PC_MCDelegate;
	if (!FBlueprintEditorUtils::AddMemberVariable(BP, Name, DelegateType))
	{
		Err = FString::Printf(TEXT("could not add dispatcher '%s'"), *Name.ToString());
		return false;
	}
	// What FBlueprintEditor::OnAddNewDelegate does: a signature graph with an editable entry node.
	UEdGraph* G = FBlueprintEditorUtils::CreateNewGraph(BP, Name, UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
	G->bEditable = false;
	K2->CreateDefaultNodesForGraph(*G);
	K2->CreateFunctionGraphTerminators(*G, (UClass*)nullptr);
	K2->AddExtraFunctionFlags(G, (FUNC_BlueprintCallable | FUNC_BlueprintEvent | FUNC_Public));
	K2->MarkFunctionEntryAsEditable(G, true);
	BP->DelegateSignatureGraphs.Add(G);
	const TArray<TSharedPtr<FJsonValue>>* Inputs = nullptr;
	if (Op->TryGetArrayField(TEXT("inputs"), Inputs))
	{
		TArray<UK2Node_FunctionEntry*> Entries;
		G->GetNodesOfClass(Entries);
		if (Entries.Num() == 0)
		{
			Err = TEXT("dispatcher signature graph has no entry node");
			return false;
		}
		for (const TSharedPtr<FJsonValue>& V : *Inputs)
		{
			const TSharedPtr<FJsonObject> P = V->AsObject();
			FEdGraphPinType T;
			if (!P || !ParsePinType(P->GetStringField(TEXT("type")), T, Err)) return false;
			Entries[0]->CreateUserDefinedPin(FName(*P->GetStringField(TEXT("name"))), T, EGPD_Output);
		}
	}
	Ctx.bStructural = true;
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);
	return true;
}

static bool OpAddLocalVariable(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString Fn = OpStr(Op, TEXT("function"));
	UEdGraph* G = FindGraph(Ctx.BP, Fn);
	if (!G || !Ctx.BP->FunctionGraphs.Contains(G))
	{
		Err = FString::Printf(TEXT("function '%s' not found (local variables live in a function: add_function first)"), *Fn);
		return false;
	}
	const FName Name(*OpStr(Op, TEXT("name")));
	FEdGraphPinType T;
	if (!ParsePinType(OpStr(Op, TEXT("type")), T, Err)) return false;
	if (FindLocalVariable(G, Name))
	{
		Err = FString::Printf(TEXT("local variable '%s' already exists in %s"), *Name.ToString(), *Fn);
		return false;
	}
	if (!FBlueprintEditorUtils::AddLocalVariable(Ctx.BP, G, Name, T, JsonToDefaultString(Op->TryGetField(TEXT("default")))))
	{
		Err = FString::Printf(TEXT("could not add local variable '%s' (name clash with a member variable?)"), *Name.ToString());
		return false;
	}
	return true;
}

static bool OpAddMacro(FApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString Name = OpStr(Op, TEXT("name"));
	if (Name.IsEmpty() || FindGraph(Ctx.BP, Name))
	{
		Err = FString::Printf(TEXT("macro name '%s' is empty or already used"), *Name);
		return false;
	}
	UEdGraph* G = FBlueprintEditorUtils::CreateNewGraph(Ctx.BP, FName(*Name), UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
	FBlueprintEditorUtils::AddMacroGraph(Ctx.BP, G, true, nullptr);
	TArray<UK2Node_Tunnel*> Tunnels;
	G->GetNodesOfClass(Tunnels);
	UK2Node_Tunnel* Entry = nullptr;
	UK2Node_Tunnel* Exit = nullptr;
	for (UK2Node_Tunnel* T : Tunnels)
	{
		if (T->bCanHaveOutputs && !Entry) Entry = T;
		else if (T->bCanHaveInputs && !Exit) Exit = T;
	}
	if (!Entry || !Exit)
	{
		Err = TEXT("macro graph has no tunnel entry/exit nodes");
		return false;
	}
	auto AddPins = [&](const TCHAR* Field, UK2Node_Tunnel* Target, EEdGraphPinDirection Dir) -> bool
	{
		const TArray<TSharedPtr<FJsonValue>>* List = nullptr;
		if (!Op->TryGetArrayField(Field, List)) return true;
		for (const TSharedPtr<FJsonValue>& V : *List)
		{
			const TSharedPtr<FJsonObject> P = V->AsObject();
			FEdGraphPinType T;
			if (!P || !ParsePinType(P->GetStringField(TEXT("type")), T, Err)) return false;
			Target->CreateUserDefinedPin(FName(*P->GetStringField(TEXT("name"))), T, Dir);
		}
		return true;
	};
	if (!AddPins(TEXT("inputs"), Entry, EGPD_Output) || !AddPins(TEXT("outputs"), Exit, EGPD_Input)) return false;
	Ctx.Aliases.Add(Name + TEXT("_entry"), Entry);
	Ctx.Aliases.Add(Name + TEXT("_exit"), Exit);
	Ctx.Aliases.Add(Name + TEXT("_result"), Exit);
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
		const bool bInterface = Norm(ParentName) == TEXT("interface") || Norm(ParentName) == TEXT("blueprintinterface");
		const bool bMacroLib = Norm(ParentName) == TEXT("macrolibrary") || Norm(ParentName) == TEXT("blueprintmacrolibrary");
		if (bInterface || bMacroLib)
		{
			// Blueprint Interface: add_function ops declare its functions (no outputs = an event in implementers).
			// Macro library: add_macro ops; its macros are usable from any Blueprint with "library".
			FString Pkg, Name, Err;
			if (!SplitAssetPath(Path, Pkg, Name, Err)) return MCPError(Err);
			UClass* MacroParent = bMacroLib ? FEpicUnrealMCPReflectionCommands::ResolveClass(TEXT("Actor")) : nullptr;
			BP = FKismetEditorUtilities::CreateBlueprint(bInterface ? UInterface::StaticClass() : MacroParent, CreatePackage(*Pkg), FName(*Name),
				bInterface ? BPTYPE_Interface : BPTYPE_MacroLibrary, UBlueprint::StaticClass(), UBlueprintGeneratedClass::StaticClass(), FName(TEXT("UnrealMCP")));
			if (!BP) return MCPError(TEXT("Blueprint creation failed"));
			FAssetRegistryModule::AssetCreated(BP);
			BP->MarkPackageDirty();
			return MCPAuthoring::ApplyBlueprintOps(BP, true, Params);
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
				else if (Kind == TEXT("implement_interface")) bOk = OpImplementInterface(Ctx, Op, Err);
				else if (Kind == TEXT("add_dispatcher") || Kind == TEXT("add_event_dispatcher")) bOk = OpAddDispatcher(Ctx, Op, Err);
				else if (Kind == TEXT("add_local_variable")) bOk = OpAddLocalVariable(Ctx, Op, Err);
				else if (Kind == TEXT("add_macro")) bOk = OpAddMacro(Ctx, Op, Err);
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
						"add_local_variable, add_macro, add_dispatcher, implement_interface, add_node, connect, disconnect, set_pins, remove_node, "
						"set_defaults)"), *Kind);
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
		// Nodes with several exec inputs (Timeline Play/Reverse/Stop, Gate Open/Close) need only one wired.
		UEdGraphPin* FirstExec = nullptr;
		bool bAnyWired = false;
		for (UEdGraphPin* P : N->Pins)
		{
			if (P && P->Direction == EGPD_Input && P->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec)
			{
				if (!FirstExec) FirstExec = P;
				bAnyWired |= P->LinkedTo.Num() > 0;
			}
		}
		if (FirstExec && !bAnyWired && !Cast<UK2Node_Tunnel>(N))
		{
			Warnings.Add(FString::Printf(TEXT("'%s' (%s) has an unconnected exec input: it will never run and its outputs stay empty. "
				"Wire something into %s.%s"), *Pair.Key, *N->GetNodeTitle(ENodeTitleType::ListView).ToString(), *Pair.Key, *FirstExec->PinName.ToString()));
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

bool MCPAuthoring::ParsePinTypeSpec(const FString& Spec, FEdGraphPinType& Out, FString& Err)
{
	return ParsePinType(Spec, Out, Err);
}

FString MCPAuthoring::DescribePinTypeSpec(const FEdGraphPinType& Type)
{
	return Type.PinCategory == UEdGraphSchema_K2::PC_Exec ? FString(TEXT("exec")) : DescribePinType(Type);
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
