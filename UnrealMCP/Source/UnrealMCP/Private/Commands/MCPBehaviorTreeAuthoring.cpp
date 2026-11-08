// bt_export / bt_build: Blackboard keys + Behavior Tree structure as a nested JSON spec.
// The runtime tree is built directly (AIModule classes); the editor graph is dropped and the BT
// editor regenerates it from the runtime tree (UBehaviorTreeGraph::OnCreated -> SpawnMissingNodes).
#include "Commands/MCPAuthoringCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"

#include "BehaviorTree/BTCompositeNode.h"
#include "BehaviorTree/BTDecorator.h"
#include "BehaviorTree/BTService.h"
#include "BehaviorTree/BTTaskNode.h"
#include "BehaviorTree/BehaviorTree.h"
#include "BehaviorTree/BehaviorTreeTypes.h"
#include "BehaviorTree/BlackboardData.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Bool.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Class.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Enum.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Float.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Int.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Name.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Object.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Rotator.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_String.h"
#include "BehaviorTree/Blackboard/BlackboardKeyType_Vector.h"
#include "Editor.h"
#include "EditorAssetLibrary.h"
#include "Subsystems/AssetEditorSubsystem.h"

using namespace MCPAuthoring;

static FString ShortType(const UClass* C)
{
	FString N = C->GetName();
	for (const TCHAR* P : { TEXT("BTComposite_"), TEXT("BTTask_"), TEXT("BTDecorator_"), TEXT("BTService_"), TEXT("BlackboardKeyType_") })
	{
		if (N.RemoveFromStart(P)) break;
	}
	N.RemoveFromEnd(TEXT("_C"));
	return N;
}

static UClass* ResolveNodeClass(const FString& Type, UClass* Base, const TCHAR* Prefix)
{
	for (const FString& Candidate : { Type, FString(Prefix) + Type })
	{
		UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(Candidate);
		if (C && C->IsChildOf(Base) && !C->HasAnyClassFlags(CLASS_Abstract)) return C;
	}
	return nullptr;
}

// ---------------------------------------------------------------------------------------------
// Blackboard
// ---------------------------------------------------------------------------------------------

static UBlackboardKeyType* MakeKeyType(UBlackboardData* BB, const TSharedPtr<FJsonObject>& K, FString& Err)
{
	FString Type, Base;
	K->TryGetStringField(TEXT("type"), Type);
	K->TryGetStringField(TEXT("base_class"), Base);
	const FString T = Type.ToLower();
	if (T == TEXT("vector")) return NewObject<UBlackboardKeyType_Vector>(BB);
	if (T == TEXT("rotator")) return NewObject<UBlackboardKeyType_Rotator>(BB);
	if (T == TEXT("bool")) return NewObject<UBlackboardKeyType_Bool>(BB);
	if (T == TEXT("int")) return NewObject<UBlackboardKeyType_Int>(BB);
	if (T == TEXT("float")) return NewObject<UBlackboardKeyType_Float>(BB);
	if (T == TEXT("name")) return NewObject<UBlackboardKeyType_Name>(BB);
	if (T == TEXT("string")) return NewObject<UBlackboardKeyType_String>(BB);
	if (T == TEXT("object") || T == TEXT("class"))
	{
		UClass* BaseClass = FEpicUnrealMCPReflectionCommands::ResolveClass(Base.IsEmpty() ? FString(TEXT("Object")) : Base);
		if (!BaseClass)
		{
			Err = FString::Printf(TEXT("base_class '%s' not found"), *Base);
			return nullptr;
		}
		if (T == TEXT("object"))
		{
			UBlackboardKeyType_Object* O = NewObject<UBlackboardKeyType_Object>(BB);
			O->BaseClass = BaseClass;
			return O;
		}
		UBlackboardKeyType_Class* C = NewObject<UBlackboardKeyType_Class>(BB);
		C->BaseClass = BaseClass;
		return C;
	}
	if (T == TEXT("enum"))
	{
		FString EnumName;
		K->TryGetStringField(TEXT("enum"), EnumName);
		UEnum* E = LoadObject<UEnum>(nullptr, *EnumName, nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!E)
		{
			Err = FString::Printf(TEXT("enum '%s' not found"), *EnumName);
			return nullptr;
		}
		UBlackboardKeyType_Enum* En = NewObject<UBlackboardKeyType_Enum>(BB);
		En->EnumType = E;
		return En;
	}
	Err = FString::Printf(TEXT("unknown key type '%s' (vector, rotator, bool, int, float, name, string, object, class, enum)"), *Type);
	return nullptr;
}

static TSharedPtr<FJsonObject> ExportBlackboard(UBlackboardData* BB)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("path"), BB->GetPathName());
	TArray<TSharedPtr<FJsonValue>> Keys;
	for (const FBlackboardEntry& E : BB->Keys)
	{
		TSharedPtr<FJsonObject> K = MakeShared<FJsonObject>();
		K->SetStringField(TEXT("name"), E.EntryName.ToString());
		if (E.KeyType)
		{
			K->SetStringField(TEXT("type"), ShortType(E.KeyType->GetClass()).ToLower());
			if (const UBlackboardKeyType_Object* O = Cast<UBlackboardKeyType_Object>(E.KeyType))
			{
				if (O->BaseClass) K->SetStringField(TEXT("base_class"), O->BaseClass->GetName());
			}
		}
		if (E.bInstanceSynced) K->SetBoolField(TEXT("instance_synced"), true);
		Keys.Add(MakeShared<FJsonValueObject>(K));
	}
	J->SetArrayField(TEXT("keys"), Keys);
	return J;
}

// ---------------------------------------------------------------------------------------------
// Tree build
// ---------------------------------------------------------------------------------------------

struct FBuildCtx
{
	UBehaviorTree* BT = nullptr;
	UBlackboardData* BB = nullptr;
	TArray<FString> Errors;
	int32 Nodes = 0;
};

/** Properties: blackboard key selectors take a key name; everything else goes through reflection. */
static void ApplyNodeProps(FBuildCtx& Ctx, UBTNode* Node, const TSharedPtr<FJsonObject>& Spec, const FString& Where)
{
	FString Name;
	if (Spec->TryGetStringField(TEXT("name"), Name)) Node->NodeName = Name;
	const TSharedPtr<FJsonObject>* Props = nullptr;
	if (!Spec->TryGetObjectField(TEXT("properties"), Props)) return;
	TSharedPtr<FJsonObject> Rest = MakeShared<FJsonObject>();
	for (const auto& Pair : (*Props)->Values)
	{
		const FString Key(*Pair.Key);
		FStructProperty* SP = nullptr;
		for (TFieldIterator<FStructProperty> It(Node->GetClass()); It; ++It)
		{
			if (It->GetName().Equals(Key, ESearchCase::IgnoreCase) && It->Struct == FBlackboardKeySelector::StaticStruct()) { SP = *It; break; }
		}
		FString KeyName;
		if (SP && Pair.Value->TryGetString(KeyName))
		{
			FBlackboardKeySelector* Sel = SP->ContainerPtrToValuePtr<FBlackboardKeySelector>(Node);
			if (!Ctx.BB)
			{
				Ctx.Errors.Add(FString::Printf(TEXT("%s.%s: the tree has no blackboard"), *Where, *Key));
				continue;
			}
			if (Ctx.BB->GetKeyID(FName(*KeyName)) == FBlackboard::InvalidKey)
			{
				Ctx.Errors.Add(FString::Printf(TEXT("%s.%s: blackboard has no key '%s'"), *Where, *Key, *KeyName));
				continue;
			}
			Sel->SelectedKeyName = FName(*KeyName);
			Sel->ResolveSelectedKey(*Ctx.BB);
			continue;
		}
		Rest->SetField(Key, Pair.Value);
	}
	TArray<FString> Errs;
	FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(Node, Rest, Errs);
	for (const FString& E : Errs) Ctx.Errors.Add(Where + TEXT(": ") + E);
}

template <typename TBase>
static TBase* MakeNode(FBuildCtx& Ctx, const TSharedPtr<FJsonObject>& Spec, const TCHAR* Prefix, const FString& Where)
{
	FString Type;
	Spec->TryGetStringField(TEXT("type"), Type);
	UClass* C = ResolveNodeClass(Type, TBase::StaticClass(), Prefix);
	if (!C)
	{
		Ctx.Errors.Add(FString::Printf(TEXT("%s: '%s' is not a %s class"), *Where, *Type, *TBase::StaticClass()->GetName()));
		return nullptr;
	}
	TBase* Node = NewObject<TBase>(Ctx.BT, C, NAME_None, RF_Transactional);
	ApplyNodeProps(Ctx, Node, Spec, Where);
	++Ctx.Nodes;
	return Node;
}

static void AttachDecoratorsAndServices(FBuildCtx& Ctx, const TSharedPtr<FJsonObject>& Spec, TArray<TObjectPtr<UBTDecorator>>* Decorators,
	TArray<TObjectPtr<UBTService>>* Services, const FString& Where)
{
	const TArray<TSharedPtr<FJsonValue>>* List = nullptr;
	if (Spec->TryGetArrayField(TEXT("decorators"), List))
	{
		if (!Decorators) Ctx.Errors.Add(Where + TEXT(": the root node cannot have decorators"));
		for (int32 I = 0; Decorators && I < List->Num(); ++I)
		{
			if (UBTDecorator* D = MakeNode<UBTDecorator>(Ctx, (*List)[I]->AsObject(), TEXT("BTDecorator_"), FString::Printf(TEXT("%s.decorators[%d]"), *Where, I)))
			{
				Decorators->Add(D);
			}
		}
	}
	if (Services && Spec->TryGetArrayField(TEXT("services"), List))
	{
		for (int32 I = 0; I < List->Num(); ++I)
		{
			if (UBTService* S = MakeNode<UBTService>(Ctx, (*List)[I]->AsObject(), TEXT("BTService_"), FString::Printf(TEXT("%s.services[%d]"), *Where, I)))
			{
				Services->Add(S);
			}
		}
	}
}

static UBTCompositeNode* BuildComposite(FBuildCtx& Ctx, const TSharedPtr<FJsonObject>& Spec, const FString& Where)
{
	UBTCompositeNode* Comp = MakeNode<UBTCompositeNode>(Ctx, Spec, TEXT("BTComposite_"), Where);
	if (!Comp) return nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Svc = nullptr;
	if (Spec->TryGetArrayField(TEXT("services"), Svc))
	{
		TSharedPtr<FJsonObject> OnlyServices = MakeShared<FJsonObject>();
		OnlyServices->SetArrayField(TEXT("services"), *Svc);
		AttachDecoratorsAndServices(Ctx, OnlyServices, nullptr, &Comp->Services, Where);
	}
	const TArray<TSharedPtr<FJsonValue>>* Kids = nullptr;
	if (!Spec->TryGetArrayField(TEXT("children"), Kids) || Kids->Num() == 0)
	{
		Ctx.Errors.Add(Where + TEXT(": a composite needs children"));
		return Comp;
	}
	for (int32 I = 0; I < Kids->Num(); ++I)
	{
		const TSharedPtr<FJsonObject> K = (*Kids)[I]->AsObject();
		const FString KW = FString::Printf(TEXT("%s.children[%d]"), *Where, I);
		FString Type;
		K->TryGetStringField(TEXT("type"), Type);
		FBTCompositeChild Child;
		if (ResolveNodeClass(Type, UBTCompositeNode::StaticClass(), TEXT("BTComposite_")))
		{
			Child.ChildComposite = BuildComposite(Ctx, K, KW);
			// Decorators on a composite child live on the parent's child entry.
			const TArray<TSharedPtr<FJsonValue>>* Ds = nullptr;
			if (K->TryGetArrayField(TEXT("decorators"), Ds))
			{
				TSharedPtr<FJsonObject> OnlyDecorators = MakeShared<FJsonObject>();
				OnlyDecorators->SetArrayField(TEXT("decorators"), *Ds);
				AttachDecoratorsAndServices(Ctx, OnlyDecorators, &Child.Decorators, nullptr, KW);
			}
		}
		else
		{
			UBTTaskNode* Task = MakeNode<UBTTaskNode>(Ctx, K, TEXT("BTTask_"), KW);
			Child.ChildTask = Task;
			if (Task) AttachDecoratorsAndServices(Ctx, K, &Child.Decorators, &Task->Services, KW);
		}
		Comp->Children.Add(Child);
	}
	return Comp;
}

static TSharedPtr<FJsonObject> ExportBTNode(UBTNode* Node)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("type"), ShortType(Node->GetClass()));
	J->SetStringField(TEXT("name"), Node->GetNodeName());
	TSharedPtr<FJsonObject> Props = MakeShared<FJsonObject>();
	for (TFieldIterator<FProperty> It(Node->GetClass(), EFieldIteratorFlags::IncludeSuper); It; ++It)
	{
		FProperty* P = *It;
		if (P->GetOwnerClass() == UBTNode::StaticClass() || P->GetOwnerClass() == UBTCompositeNode::StaticClass()
			|| P->GetOwnerClass() == UBTTaskNode::StaticClass() || !P->HasAnyPropertyFlags(CPF_Edit))
		{
			continue;
		}
		if (const FStructProperty* SP = CastField<FStructProperty>(P); SP && SP->Struct == FBlackboardKeySelector::StaticStruct())
		{
			const FBlackboardKeySelector* Sel = SP->ContainerPtrToValuePtr<FBlackboardKeySelector>(Node);
			Props->SetStringField(P->GetName(), Sel->SelectedKeyName.ToString());
		}
		else if (const FStructProperty* VP = CastField<FStructProperty>(P); VP && VP->Struct->GetName().StartsWith(TEXT("ValueOrBBKey_")))
		{
			FString Err;
			TSharedPtr<FJsonValue> Key = FEpicUnrealMCPReflectionCommands::GetPropertyByPath(Node, P->GetName() + TEXT(".Key"), Err);
			FString KeyName;
			if (Key.IsValid() && Key->TryGetString(KeyName) && !KeyName.IsEmpty() && KeyName != TEXT("None"))
			{
				TSharedPtr<FJsonObject> KO = MakeShared<FJsonObject>();
				KO->SetStringField(TEXT("key"), KeyName);
				Props->SetObjectField(P->GetName(), KO);
			}
			else if (TSharedPtr<FJsonValue> V = FEpicUnrealMCPReflectionCommands::GetPropertyByPath(Node, P->GetName() + TEXT(".DefaultValue"), Err))
			{
				Props->SetField(P->GetName(), V);
			}
		}
		else if (CastField<FNumericProperty>(P) || CastField<FBoolProperty>(P) || CastField<FEnumProperty>(P) || CastField<FNameProperty>(P) || CastField<FStrProperty>(P))
		{
			FString Err;
			if (TSharedPtr<FJsonValue> V = FEpicUnrealMCPReflectionCommands::GetPropertyByPath(Node, P->GetName(), Err)) Props->SetField(P->GetName(), V);
		}
	}
	if (Props->Values.Num()) J->SetObjectField(TEXT("properties"), Props);
	return J;
}

static TSharedPtr<FJsonObject> ExportComposite(UBTCompositeNode* Comp)
{
	TSharedPtr<FJsonObject> J = ExportBTNode(Comp);
	TArray<TSharedPtr<FJsonValue>> Services, Kids;
	for (UBTService* S : Comp->Services) if (S) Services.Add(MakeShared<FJsonValueObject>(ExportBTNode(S)));
	if (Services.Num()) J->SetArrayField(TEXT("services"), Services);
	for (const FBTCompositeChild& C : Comp->Children)
	{
		TSharedPtr<FJsonObject> K = C.ChildComposite ? ExportComposite(C.ChildComposite) : C.ChildTask ? ExportBTNode(C.ChildTask) : MakeShared<FJsonObject>();
		if (C.ChildTask)
		{
			TArray<TSharedPtr<FJsonValue>> TS;
			for (UBTService* S : C.ChildTask->Services) if (S) TS.Add(MakeShared<FJsonValueObject>(ExportBTNode(S)));
			if (TS.Num()) K->SetArrayField(TEXT("services"), TS);
		}
		TArray<TSharedPtr<FJsonValue>> Ds;
		for (UBTDecorator* D : C.Decorators) if (D) Ds.Add(MakeShared<FJsonValueObject>(ExportBTNode(D)));
		if (Ds.Num()) K->SetArrayField(TEXT("decorators"), Ds);
		Kids.Add(MakeShared<FJsonValueObject>(K));
	}
	J->SetArrayField(TEXT("children"), Kids);
	return J;
}

static TSharedPtr<FJsonObject> HandleBtExport(const TSharedPtr<FJsonObject>& Params)
{
	FString Path;
	Params->TryGetStringField(TEXT("tree"), Path);
	UBehaviorTree* BT = Cast<UBehaviorTree>(LoadAssetOfClass(Path, UBehaviorTree::StaticClass()));
	if (!BT)
	{
		if (UBlackboardData* BB = Cast<UBlackboardData>(LoadAssetOfClass(Path, UBlackboardData::StaticClass())))
		{
			TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
			J->SetObjectField(TEXT("blackboard"), ExportBlackboard(BB));
			return J;
		}
		return MCPError(FString::Printf(TEXT("Behavior Tree or Blackboard '%s' not found"), *Path));
	}
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("path"), BT->GetPathName());
	if (BT->BlackboardAsset) J->SetObjectField(TEXT("blackboard"), ExportBlackboard(BT->BlackboardAsset));
	if (BT->RootNode) J->SetObjectField(TEXT("root"), ExportComposite(BT->RootNode));
	return J;
}

static TSharedPtr<FJsonObject> HandleBtBuild(const TSharedPtr<FJsonObject>& Params)
{
	FString TreePath, BBPath;
	Params->TryGetStringField(TEXT("tree"), TreePath);
	Params->TryGetStringField(TEXT("blackboard"), BBPath);
	bool bSave = true;
	Params->TryGetBoolField(TEXT("save"), bSave);
	const TArray<TSharedPtr<FJsonValue>>* Keys = nullptr;
	Params->TryGetArrayField(TEXT("keys"), Keys);
	const TSharedPtr<FJsonObject>* RootSpec = nullptr;
	Params->TryGetObjectField(TEXT("root"), RootSpec);

	// Blackboard: load or create, upsert keys.
	UBlackboardData* BB = nullptr;
	bool bCreatedBB = false;
	if (!BBPath.IsEmpty())
	{
		BB = Cast<UBlackboardData>(LoadAssetOfClass(BBPath, UBlackboardData::StaticClass()));
		if (!BB)
		{
			FString Err;
			BB = Cast<UBlackboardData>(CreateAssetObject(BBPath, UBlackboardData::StaticClass(), Err));
			if (!BB) return MCPError(Err);
			bCreatedBB = true;
		}
	}
	TArray<FString> Errors;
	if (Keys)
	{
		if (!BB) return MCPError(TEXT("keys need a blackboard path"));
		BB->Modify();
		for (int32 I = 0; I < Keys->Num(); ++I)
		{
			const TSharedPtr<FJsonObject> K = (*Keys)[I]->AsObject();
			FString Name, Err;
			K->TryGetStringField(TEXT("name"), Name);
			UBlackboardKeyType* KT = MakeKeyType(BB, K, Err);
			if (!KT || Name.IsEmpty())
			{
				Errors.Add(FString::Printf(TEXT("keys[%d]: %s"), I, Name.IsEmpty() ? TEXT("missing name") : *Err));
				continue;
			}
			bool bSynced = false;
			K->TryGetBoolField(TEXT("instance_synced"), bSynced);
			FBlackboardEntry* Existing = BB->Keys.FindByPredicate([&](const FBlackboardEntry& E) { return E.EntryName == FName(*Name); });
			FBlackboardEntry& E = Existing ? *Existing : BB->Keys.AddDefaulted_GetRef();
			E.EntryName = FName(*Name);
			E.KeyType = KT;
			E.bInstanceSynced = bSynced;
		}
		BB->MarkPackageDirty();
	}

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	UBehaviorTree* BT = nullptr;
	bool bCreatedBT = false;
	if (!TreePath.IsEmpty())
	{
		BT = Cast<UBehaviorTree>(LoadAssetOfClass(TreePath, UBehaviorTree::StaticClass()));
		if (!BT)
		{
			FString Err;
			BT = Cast<UBehaviorTree>(CreateAssetObject(TreePath, UBehaviorTree::StaticClass(), Err));
			if (!BT) return MCPError(Err);
			bCreatedBT = true;
		}
		if (BB) BT->BlackboardAsset = BB;
	}
	int32 NodeCount = 0;
	if (RootSpec && BT && Errors.Num() == 0)
	{
		FBuildCtx Ctx;
		Ctx.BT = BT;
		Ctx.BB = BT->BlackboardAsset;
		if ((*RootSpec)->HasField(TEXT("decorators")))
		{
			Ctx.Errors.Add(TEXT("root: the root composite cannot have decorators"));
		}
		UBTCompositeNode* Root = BuildComposite(Ctx, *RootSpec, TEXT("root"));
		Errors.Append(Ctx.Errors);
		NodeCount = Ctx.Nodes;
		if (Errors.Num() == 0 && Root)
		{
			// Swap in the new tree only when the whole spec built cleanly.
			if (GEditor)
			{
				GEditor->GetEditorSubsystem<UAssetEditorSubsystem>()->CloseAllEditorsForAsset(BT);
			}
			BT->Modify();
			BT->RootNode = Root;
			BT->RootDecorators.Reset();
#if WITH_EDITORONLY_DATA
			BT->BTGraph = nullptr; // the BT editor rebuilds its graph from RootNode on next open
#endif
			BT->MarkPackageDirty();
		}
	}
	else if (RootSpec && !BT)
	{
		Errors.Add(TEXT("root needs a tree path"));
	}

	const bool bOk = Errors.Num() == 0;
	bool bSaved = false;
	if (bOk && bSave)
	{
		if (BB) bSaved = SaveAsset(BB);
		if (BT) bSaved = SaveAsset(BT) && (BB == nullptr || bSaved);
	}
	if (!bOk)
	{
		// Nothing half-built stays around: drop assets this call created.
		if (bCreatedBT && BT) UEditorAssetLibrary::DeleteLoadedAsset(BT);
		if (bCreatedBB && BB) UEditorAssetLibrary::DeleteLoadedAsset(BB);
	}
	Out->SetBoolField(TEXT("ok"), bOk);
	Out->SetField(TEXT("errors"), StringArray(Errors));
	Out->SetNumberField(TEXT("nodes"), NodeCount);
	if (BT && bOk) Out->SetStringField(TEXT("tree"), BT->GetPathName());
	if (BB && bOk) Out->SetObjectField(TEXT("blackboard"), ExportBlackboard(BB));
	Out->SetBoolField(TEXT("saved"), bSaved);
	return Out;
}

void FMCPBehaviorTreeAuthoring::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("bt_export"), &HandleBtExport, false);
	Registry.Register(TEXT("bt_build"), &HandleBtBuild, true);
}
