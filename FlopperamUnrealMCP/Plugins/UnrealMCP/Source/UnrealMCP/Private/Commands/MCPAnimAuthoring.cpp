// animbp_export / animbp_apply: Animation Blueprints (AnimGraph state machines, states, transitions with
// rule graphs, asset players, slots) authored with bp_apply's batched, atomic op machinery.
// anim_asset_apply: montages (segments per slot, sections, links) and notifies (named skeleton notifies,
// notify classes and notify states) on any animation sequence or montage.
#include "Commands/MCPAuthoringCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"

#include "AnimGraphNode_Base.h"
#include "AnimGraphNode_BlendSpacePlayer.h"
#include "AnimGraphNode_Root.h"
#include "AnimGraphNode_SequencePlayer.h"
#include "AnimGraphNode_Slot.h"
#include "AnimGraphNode_StateMachine.h"
#include "AnimGraphNode_StateMachineBase.h"
#include "AnimGraphNode_StateResult.h"
#include "AnimGraphNode_TransitionResult.h"
#include "AnimStateEntryNode.h"
#include "AnimStateNode.h"
#include "AnimStateNodeBase.h"
#include "AnimStateTransitionNode.h"
#include "AnimationBlueprintLibrary.h"
#include "AnimationStateMachineGraph.h"
#include "Animation/AnimBlueprint.h"
#include "Animation/AnimInstance.h"
#include "Animation/AnimMontage.h"
#include "Animation/AnimNotifies/AnimNotify.h"
#include "Animation/AnimNotifies/AnimNotifyState.h"
#include "Animation/AnimSequenceBase.h"
#include "Animation/BlendSpace.h"
#include "Animation/Skeleton.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "EdGraph/EdGraph.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "EditorAssetLibrary.h"
#include "Engine/SkeletalMesh.h"
#include "Factories/AnimBlueprintFactory.h"
#include "Factories/AnimMontageFactory.h"
#include "K2Node_CallFunction.h"
#include "K2Node_VariableGet.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/FeedbackContext.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"

using namespace MCPAuthoring;

namespace
{
FString Str(const TSharedPtr<FJsonObject>& O, const TCHAR* Field)
{
	FString V;
	if (O.IsValid()) O->TryGetStringField(Field, V);
	return V;
}

TSharedPtr<FJsonObject> Obj(const TSharedPtr<FJsonObject>& O, const TCHAR* Field)
{
	const TSharedPtr<FJsonObject>* V = nullptr;
	return O.IsValid() && O->TryGetObjectField(Field, V) ? *V : TSharedPtr<FJsonObject>();
}

template <typename T>
T* LoadAs(const FString& Path)
{
	if (Path.IsEmpty()) return nullptr;
	if (T* Direct = Cast<T>(StaticLoadObject(T::StaticClass(), nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet)))
	{
		return Direct;
	}
	return Cast<T>(LoadAssetOfClass(Path, T::StaticClass()));
}

template <typename T>
T* FirstNodeOf(UEdGraph* G)
{
	if (!G) return nullptr;
	for (UEdGraphNode* N : G->Nodes)
	{
		if (T* Typed = Cast<T>(N)) return Typed;
	}
	return nullptr;
}

UEdGraph* FindAnimGraph(UBlueprint* BP)
{
	for (UEdGraph* G : BP->FunctionGraphs)
	{
		if (G && G->GetFName() == UEdGraphSchema_K2::GN_AnimGraph) return G;
	}
	return nullptr;
}

void AllStateMachines(UBlueprint* BP, TArray<UAnimGraphNode_StateMachineBase*>& Out)
{
	TArray<UEdGraph*> Graphs;
	BP->GetAllGraphs(Graphs);
	for (UEdGraph* G : Graphs)
	{
		for (UEdGraphNode* N : G ? G->Nodes : TArray<TObjectPtr<UEdGraphNode>>())
		{
			if (UAnimGraphNode_StateMachineBase* SM = Cast<UAnimGraphNode_StateMachineBase>(N)) Out.Add(SM);
		}
	}
}

FString StateName(const UEdGraphNode* N)
{
	if (const UAnimStateNodeBase* S = Cast<UAnimStateNodeBase>(N)) return S->GetStateName();
	return FString();
}

/** Alias from this call, then a state machine by name, then a state by name ("State" or "Machine/State"), then a node id. */
UEdGraphNode* ResolveAnimRef(FGraphApplyContext& Ctx, const FString& Ref)
{
	if (UEdGraphNode** A = Ctx.Aliases.Find(Ref)) return *A;
	FString MachineName, Name = Ref;
	Ref.Split(TEXT("/"), &MachineName, &Name);
	TArray<UAnimGraphNode_StateMachineBase*> Machines;
	AllStateMachines(Ctx.BP, Machines);
	for (UAnimGraphNode_StateMachineBase* SM : Machines)
	{
		if (MachineName.IsEmpty() && SM->GetStateMachineName().Equals(Name, ESearchCase::IgnoreCase)) return SM;
	}
	for (UAnimGraphNode_StateMachineBase* SM : Machines)
	{
		if (!MachineName.IsEmpty() && !SM->GetStateMachineName().Equals(MachineName, ESearchCase::IgnoreCase)) continue;
		for (UEdGraphNode* N : SM->EditorStateMachineGraph ? SM->EditorStateMachineGraph->Nodes : TArray<TObjectPtr<UEdGraphNode>>())
		{
			if (Cast<UAnimStateNode>(N) && StateName(N).Equals(Name, ESearchCase::IgnoreCase)) return N;
		}
	}
	return ResolveNodeRef(Ctx, Ref);
}

void SetNodeProperties(UEdGraphNode* N, const TSharedPtr<FJsonObject>& Props, FString& Err)
{
	if (!Props.IsValid()) return;
	TArray<FString> Errors;
	N->Modify();
	FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(N, Props, Errors);
	if (Errors.Num()) Err = FString::Join(Errors, TEXT("; "));
}

template <typename T>
T* NewAnimNode(FGraphApplyContext& Ctx, UEdGraph* G, const TSharedPtr<FJsonObject>& Op)
{
	G->Modify();
	FGraphNodeCreator<T> Creator(*G);
	T* Node = Creator.CreateNode(false);
	Creator.Finalize(); // PostPlacedNewNode creates bound graphs for states, transitions and state machines
	PlaceGraphNode(Ctx, G, Node, Op);
	return Node;
}

/** Asset player for a state or any anim graph: sequence (UAnimSequenceBase) or blendspace. */
UAnimGraphNode_Base* AddAssetPlayer(FGraphApplyContext& Ctx, UEdGraph* G, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString SeqPath = Str(Op, TEXT("sequence"));
	const FString BsPath = Str(Op, TEXT("blendspace"));
	if (!SeqPath.IsEmpty())
	{
		UAnimSequenceBase* Seq = LoadAs<UAnimSequenceBase>(SeqPath);
		if (!Seq || Seq->IsA<UAnimMontage>())
		{
			Err = FString::Printf(TEXT("'%s' is not an animation sequence"), *SeqPath);
			return nullptr;
		}
		UAnimGraphNode_SequencePlayer* P = NewAnimNode<UAnimGraphNode_SequencePlayer>(Ctx, G, Op);
		P->SetAnimationAsset(Seq);
		bool bLoop = true;
		if (Op->TryGetBoolField(TEXT("loop"), bLoop))
		{
			FString E;
			FEpicUnrealMCPReflectionCommands::SetPropertyByPath(P, TEXT("Node.bLoopAnimation"), MakeShared<FJsonValueBoolean>(bLoop), E);
		}
		double Rate = 0;
		if (Op->TryGetNumberField(TEXT("play_rate"), Rate))
		{
			FString E;
			FEpicUnrealMCPReflectionCommands::SetPropertyByPath(P, TEXT("Node.PlayRate"), MakeShared<FJsonValueNumber>(Rate), E);
		}
		return P;
	}
	if (!BsPath.IsEmpty())
	{
		UBlendSpace* BS = LoadAs<UBlendSpace>(BsPath);
		if (!BS)
		{
			Err = FString::Printf(TEXT("'%s' is not a BlendSpace"), *BsPath);
			return nullptr;
		}
		UAnimGraphNode_BlendSpacePlayer* P = NewAnimNode<UAnimGraphNode_BlendSpacePlayer>(Ctx, G, Op);
		P->SetAnimationAsset(BS);
		// "inputs": {"X": "Speed", "Y": "Direction"}: a variable getter wired into each axis pin.
		if (TSharedPtr<FJsonObject> Inputs = Obj(Op, TEXT("inputs")))
		{
			for (const auto& Pair : Inputs->Values)
			{
				FString Var;
				if (!Pair.Value->TryGetString(Var)) continue;
				TSharedPtr<FJsonObject> Spec = MakeShared<FJsonObject>();
				Spec->SetStringField(TEXT("kind"), TEXT("get_var"));
				Spec->SetStringField(TEXT("variable"), Var);
				UEdGraphNode* Get = CreateGraphNode(Ctx, G, Spec, Err);
				if (!Get || !LinkPins(Get, Var, P, FString(*Pair.Key), Err)) return nullptr;
			}
		}
		return P;
	}
	Err = TEXT("pass \"sequence\" (an AnimSequence path) or \"blendspace\" (a BlendSpace path)");
	return nullptr;
}

// ---------------------------------------------------------------------------------------------
// Transition rules: {"var":"Speed","cmp":">","value":10} | {"var":"IsInAir"} | {"var":"IsInAir","not":true}
//                   | {"all":[...]} | {"any":[...]}
// ---------------------------------------------------------------------------------------------

UEdGraphNode* NewK2(FGraphApplyContext& Ctx, UEdGraph* G, const FString& Kind, const FString& Name, const FString& Class, FString& Err)
{
	TSharedPtr<FJsonObject> Spec = MakeShared<FJsonObject>();
	Spec->SetStringField(TEXT("kind"), Kind);
	Spec->SetStringField(Kind == TEXT("get_var") ? TEXT("variable") : TEXT("function"), Name);
	if (!Class.IsEmpty()) Spec->SetStringField(TEXT("class"), Class);
	return CreateGraphNode(Ctx, G, Spec, Err);
}

/** Builds the expression, returns the node whose ReturnValue (or variable pin) is the bool. */
bool BuildRule(FGraphApplyContext& Ctx, UEdGraph* G, const TSharedPtr<FJsonObject>& Rule, UEdGraphNode*& OutNode, FString& OutPin, FString& Err)
{
	const TArray<TSharedPtr<FJsonValue>>* List = nullptr;
	const bool bAll = Rule->TryGetArrayField(TEXT("all"), List);
	if (bAll || Rule->TryGetArrayField(TEXT("any"), List))
	{
		UEdGraphNode* Acc = nullptr;
		FString AccPin;
		for (const TSharedPtr<FJsonValue>& V : *List)
		{
			UEdGraphNode* N = nullptr;
			FString Pin;
			if (!V->AsObject().IsValid() || !BuildRule(Ctx, G, V->AsObject(), N, Pin, Err)) return false;
			if (!Acc)
			{
				Acc = N;
				AccPin = Pin;
				continue;
			}
			UEdGraphNode* Op = NewK2(Ctx, G, TEXT("call_function"), bAll ? TEXT("BooleanAND") : TEXT("BooleanOR"), TEXT("KismetMathLibrary"), Err);
			if (!Op || !LinkPins(Acc, AccPin, Op, TEXT("A"), Err) || !LinkPins(N, Pin, Op, TEXT("B"), Err)) return false;
			Acc = Op;
			AccPin = TEXT("ReturnValue");
		}
		if (!Acc)
		{
			Err = TEXT("empty all/any rule");
			return false;
		}
		OutNode = Acc;
		OutPin = AccPin;
		return true;
	}
	const FString Var = Str(Rule, TEXT("var"));
	if (Var.IsEmpty())
	{
		Err = TEXT("rule needs \"var\" (with optional cmp/value/not) or \"all\"/\"any\" lists");
		return false;
	}
	const int32 VarIdx = FBlueprintEditorUtils::FindNewVariableIndex(Ctx.BP, FName(*Var));
	if (VarIdx == INDEX_NONE)
	{
		Err = FString::Printf(TEXT("rule variable '%s' is not a variable of %s (add_variable it first)"), *Var, *Ctx.BP->GetName());
		return false;
	}
	const FName Cat = Ctx.BP->NewVariables[VarIdx].VarType.PinCategory;
	UEdGraphNode* Get = NewK2(Ctx, G, TEXT("get_var"), Var, FString(), Err);
	if (!Get) return false;
	UEdGraphNode* Out = Get;
	FString Pin = Var;
	FString Cmp = Str(Rule, TEXT("cmp"));
	const TSharedPtr<FJsonValue> Value = Rule->TryGetField(TEXT("value"));
	if (!Cmp.IsEmpty() || Value.IsValid())
	{
		if (Cmp.IsEmpty()) Cmp = TEXT("==");
		static const TMap<FString, FString> Ops = { { TEXT(">"), TEXT("Greater") }, { TEXT(">="), TEXT("GreaterEqual") }, { TEXT("<"), TEXT("Less") },
			{ TEXT("<="), TEXT("LessEqual") }, { TEXT("=="), TEXT("EqualEqual") }, { TEXT("!="), TEXT("NotEqual") } };
		const FString* OpName = Ops.Find(Cmp);
		FString Suffix;
		if (Cat == UEdGraphSchema_K2::PC_Real) Suffix = TEXT("_DoubleDouble");
		else if (Cat == UEdGraphSchema_K2::PC_Int) Suffix = TEXT("_IntInt");
		else if (Cat == UEdGraphSchema_K2::PC_Boolean && (Cmp == TEXT("==") || Cmp == TEXT("!="))) Suffix = TEXT("_BoolBool");
		else if (Cat == UEdGraphSchema_K2::PC_Byte) Suffix = TEXT("_ByteByte");
		if (!OpName || Suffix.IsEmpty())
		{
			Err = FString::Printf(TEXT("cannot compare %s variable '%s' with '%s' (> >= < <= == != on float/int, == != on bool)"),
				*Cat.ToString(), *Var, *Cmp);
			return false;
		}
		UEdGraphNode* C = NewK2(Ctx, G, TEXT("call_function"), *OpName + Suffix, TEXT("KismetMathLibrary"), Err);
		if (!C || !LinkPins(Get, Var, C, TEXT("A"), Err)) return false;
		if (Value.IsValid())
		{
			TSharedPtr<FJsonObject> Pins = MakeShared<FJsonObject>();
			Pins->SetField(TEXT("B"), Value);
			TSharedPtr<FJsonObject> Spec = MakeShared<FJsonObject>();
			Spec->SetObjectField(TEXT("pins"), Pins);
			if (!ApplyPinDefaults(C, Spec, Err)) return false;
		}
		Out = C;
		Pin = TEXT("ReturnValue");
	}
	else if (Cat != UEdGraphSchema_K2::PC_Boolean)
	{
		Err = FString::Printf(TEXT("'%s' is not a bool: give \"cmp\" and \"value\" (e.g. {\"var\":\"%s\",\"cmp\":\">\",\"value\":10})"), *Var, *Var);
		return false;
	}
	bool bNot = false;
	if (Rule->TryGetBoolField(TEXT("not"), bNot) && bNot)
	{
		UEdGraphNode* N = NewK2(Ctx, G, TEXT("call_function"), TEXT("Not_PreBool"), TEXT("KismetMathLibrary"), Err);
		if (!N || !LinkPins(Out, Pin, N, TEXT("A"), Err)) return false;
		Out = N;
		Pin = TEXT("ReturnValue");
	}
	OutNode = Out;
	OutPin = Pin;
	return true;
}

// ---------------------------------------------------------------------------------------------
// animbp_apply ops
// ---------------------------------------------------------------------------------------------

UAnimationStateMachineGraph* MachineGraphOf(UEdGraphNode* N)
{
	if (UAnimGraphNode_StateMachineBase* SM = Cast<UAnimGraphNode_StateMachineBase>(N)) return SM->EditorStateMachineGraph;
	return nullptr;
}

bool SetEntry(UAnimationStateMachineGraph* MG, UAnimStateNode* State, FString& Err)
{
	UAnimStateEntryNode* Entry = MG ? MG->EntryNode.Get() : nullptr;
	if (!Entry) Entry = FirstNodeOf<UAnimStateEntryNode>(MG);
	if (!Entry || Entry->Pins.Num() == 0)
	{
		Err = TEXT("state machine has no entry node");
		return false;
	}
	UEdGraphPin* Out = Entry->Pins[0];
	Out->Modify();
	Out->BreakAllPinLinks(true);
	if (!MG->GetSchema()->TryCreateConnection(Out, State->GetInputPin()))
	{
		Err = TEXT("could not connect the entry to the state");
		return false;
	}
	return true;
}

bool OpAddStateMachine(FGraphApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString GraphName = Str(Op, TEXT("graph"));
	UEdGraph* G = GraphName.IsEmpty() ? FindAnimGraph(Ctx.BP) : ResolveGraph(Ctx, GraphName);
	if (!G)
	{
		Err = FString::Printf(TEXT("graph '%s' not found"), GraphName.IsEmpty() ? TEXT("AnimGraph") : *GraphName);
		return false;
	}
	UAnimGraphNode_StateMachine* SM = NewAnimNode<UAnimGraphNode_StateMachine>(Ctx, G, Op);
	const FString Name = Str(Op, TEXT("name"));
	if (!Name.IsEmpty() && SM->EditorStateMachineGraph)
	{
		SM->OnRenameNode(Name);
	}
	FString Id = Str(Op, TEXT("id"));
	Ctx.Aliases.Add(Id.IsEmpty() ? (Name.IsEmpty() ? SM->GetStateMachineName() : Name) : Id, SM);
	SetNodeProperties(SM, Obj(Op, TEXT("properties")), Err);
	return Err.IsEmpty();
}

bool OpAddState(FGraphApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString MachineRef = Str(Op, TEXT("machine"));
	UAnimationStateMachineGraph* MG = MachineGraphOf(ResolveAnimRef(Ctx, MachineRef));
	if (!MG)
	{
		Err = FString::Printf(TEXT("state machine '%s' not found (add_state_machine first; use its id or name)"), *MachineRef);
		return false;
	}
	const bool bFirst = FirstNodeOf<UAnimStateNode>(MG) == nullptr;
	UAnimStateNode* State = NewAnimNode<UAnimStateNode>(Ctx, MG, Op);
	const FString Name = Str(Op, TEXT("name"));
	if (!Name.IsEmpty()) State->OnRenameNode(Name);
	FString Id = Str(Op, TEXT("id"));
	if (Id.IsEmpty()) Id = State->GetStateName();
	Ctx.Aliases.Add(Id, State);
	UAnimGraphNode_StateResult* Result = FirstNodeOf<UAnimGraphNode_StateResult>(State->BoundGraph);
	if (Result) Ctx.Aliases.Add(Id + TEXT("_result"), Result);

	bool bEntry = false;
	Op->TryGetBoolField(TEXT("entry"), bEntry);
	if ((bEntry || bFirst) && !SetEntry(MG, State, Err)) return false;

	// One-op state content: {"sequence": ...} or {"blendspace": ..., "inputs": {"X": "Speed"}}.
	if (Op->HasField(TEXT("sequence")) || Op->HasField(TEXT("blendspace")))
	{
		TSharedPtr<FJsonObject> PlayerOp = MakeShared<FJsonObject>(*Op);
		PlayerOp->RemoveField(TEXT("x"));
		PlayerOp->RemoveField(TEXT("y"));
		UAnimGraphNode_Base* Player = AddAssetPlayer(Ctx, State->BoundGraph, PlayerOp, Err);
		if (!Player) return false;
		Ctx.Aliases.Add(Id + TEXT("_player"), Player);
		if (!Result || !LinkPins(Player, TEXT("Pose"), Result, TEXT("Result"), Err)) return false;
	}
	SetNodeProperties(State, Obj(Op, TEXT("properties")), Err);
	return Err.IsEmpty();
}

bool OpAddTransition(FGraphApplyContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString FromRef = Str(Op, TEXT("from")), ToRef = Str(Op, TEXT("to"));
	UAnimStateNodeBase* From = Cast<UAnimStateNodeBase>(ResolveAnimRef(Ctx, FromRef));
	UAnimStateNodeBase* To = Cast<UAnimStateNodeBase>(ResolveAnimRef(Ctx, ToRef));
	if (!From || !To)
	{
		Err = FString::Printf(TEXT("state '%s' not found (an id from add_state or a state name)"), *(From ? ToRef : FromRef));
		return false;
	}
	if (From->GetGraph() != To->GetGraph())
	{
		Err = TEXT("both states must be in the same state machine");
		return false;
	}
	UEdGraph* MG = From->GetGraph();
	UAnimStateTransitionNode* T = NewAnimNode<UAnimStateTransitionNode>(Ctx, MG, Op);
	T->CreateConnections(From, To);
	FString Id = Str(Op, TEXT("id"));
	if (Id.IsEmpty()) Id = From->GetStateName() + TEXT("_to_") + To->GetStateName();
	Ctx.Aliases.Add(Id, T);
	UAnimGraphNode_TransitionResult* Result = FirstNodeOf<UAnimGraphNode_TransitionResult>(T->BoundGraph);
	if (Result) Ctx.Aliases.Add(Id + TEXT("_result"), Result);

	double Num = 0;
	if (Op->TryGetNumberField(TEXT("blend"), Num) || Op->TryGetNumberField(TEXT("crossfade"), Num)) T->CrossfadeDuration = float(Num);
	if (Op->TryGetNumberField(TEXT("priority"), Num)) T->PriorityOrder = int32(Num);
	bool bAuto = false;
	if (Op->TryGetBoolField(TEXT("automatic"), bAuto)) T->bAutomaticRuleBasedOnSequencePlayerInState = bAuto;
	if (TSharedPtr<FJsonObject> Rule = Obj(Op, TEXT("rule")))
	{
		UEdGraphNode* N = nullptr;
		FString Pin;
		if (!Result)
		{
			Err = TEXT("transition has no rule graph result node");
			return false;
		}
		if (!BuildRule(Ctx, T->BoundGraph, Rule, N, Pin, Err) || !LinkPins(N, Pin, Result, TEXT("bCanEnterTransition"), Err)) return false;
	}
	SetNodeProperties(T, Obj(Op, TEXT("properties")), Err);
	return Err.IsEmpty();
}

bool AnimExtension(FGraphApplyContext& Ctx, const FString& Kind, const TSharedPtr<FJsonObject>& Op, bool& bOk, FString& Err)
{
	if (Kind == TEXT("add_state_machine")) { bOk = OpAddStateMachine(Ctx, Op, Err); return true; }
	if (Kind == TEXT("add_state")) { bOk = OpAddState(Ctx, Op, Err); return true; }
	if (Kind == TEXT("add_transition")) { bOk = OpAddTransition(Ctx, Op, Err); return true; }
	if (Kind == TEXT("set_entry"))
	{
		UAnimStateNode* State = Cast<UAnimStateNode>(ResolveAnimRef(Ctx, Str(Op, TEXT("state"))));
		if (!State) Err = FString::Printf(TEXT("state '%s' not found"), *Str(Op, TEXT("state")));
		bOk = State && SetEntry(Cast<UAnimationStateMachineGraph>(State->GetGraph()), State, Err);
		return true;
	}
	if (Kind == TEXT("set_properties"))
	{
		UEdGraphNode* N = ResolveAnimRef(Ctx, Str(Op, TEXT("node")));
		if (!N) Err = FString::Printf(TEXT("unknown node '%s'"), *Str(Op, TEXT("node")));
		else SetNodeProperties(N, Obj(Op, TEXT("properties")), Err);
		bOk = N && Err.IsEmpty();
		return true;
	}
	if (Kind == TEXT("connect") || Kind == TEXT("disconnect") || Kind == TEXT("set_pins") || Kind == TEXT("remove_node"))
	{
		// Let refs name states/machines by name too: rewrite them to aliases, then fall through to bp_apply.
		for (const TCHAR* F : { TEXT("from"), TEXT("to"), TEXT("node") })
		{
			FString Ref = Str(Op, F), NodeRef, Pin;
			const bool bPin = FCString::Strcmp(F, TEXT("node")) != 0;
			if (Ref.IsEmpty()) continue;
			if (bPin && !Ref.Split(TEXT("."), &NodeRef, &Pin, ESearchCase::IgnoreCase, ESearchDir::FromEnd)) continue;
			if (!bPin) NodeRef = Ref;
			if (!Ctx.Aliases.Contains(NodeRef))
			{
				if (UEdGraphNode* N = ResolveAnimRef(Ctx, NodeRef)) Ctx.Aliases.Add(NodeRef, N);
			}
		}
		return false;
	}
	if (Kind == TEXT("add_node") || Kind == TEXT("node"))
	{
		FString NodeKind = Str(Op, TEXT("kind")).ToLower();
		if (NodeKind != TEXT("sequence_player") && NodeKind != TEXT("blendspace_player") && NodeKind != TEXT("slot")) return false;
		const FString GraphName = Str(Op, TEXT("graph"));
		UEdGraph* G = GraphName.IsEmpty() ? FindAnimGraph(Ctx.BP) : ResolveGraph(Ctx, GraphName);
		if (!G)
		{
			Err = FString::Printf(TEXT("graph '%s' not found (AnimGraph, or a state id/name)"), *GraphName);
			bOk = false;
			return true;
		}
		UEdGraphNode* N = nullptr;
		if (NodeKind == TEXT("slot"))
		{
			N = NewAnimNode<UAnimGraphNode_Slot>(Ctx, G, Op);
			FString Slot = Str(Op, TEXT("slot"));
			if (!Slot.IsEmpty())
			{
				FEpicUnrealMCPReflectionCommands::SetPropertyByPath(N, TEXT("Node.SlotName"), MakeShared<FJsonValueString>(Slot), Err);
			}
		}
		else
		{
			N = AddAssetPlayer(Ctx, G, Op, Err);
		}
		if (N)
		{
			const FString Id = Str(Op, TEXT("id"));
			if (!Id.IsEmpty()) Ctx.Aliases.Add(Id, N);
			if (Err.IsEmpty()) SetNodeProperties(N, Obj(Op, TEXT("properties")), Err);
			if (Err.IsEmpty()) ApplyPinDefaults(N, Op, Err);
		}
		bOk = N && Err.IsEmpty();
		return true;
	}
	return false;
}

UAnimBlueprint* CreateAnimBlueprint(const TSharedPtr<FJsonObject>& Params, FString& Err)
{
	FString Path = Str(Params, TEXT("blueprint")), Pkg, Name;
	if (!SplitAssetPath(Path, Pkg, Name, Err)) return nullptr;
	USkeleton* Skeleton = LoadAs<USkeleton>(Str(Params, TEXT("skeleton")));
	USkeletalMesh* Preview = LoadAs<USkeletalMesh>(Str(Params, TEXT("preview_mesh")));
	if (!Skeleton && Preview) Skeleton = Preview->GetSkeleton();
	if (!Skeleton)
	{
		if (USkeletalMesh* MeshAsSkeleton = LoadAs<USkeletalMesh>(Str(Params, TEXT("skeleton"))))
		{
			Skeleton = MeshAsSkeleton->GetSkeleton();
			if (!Preview) Preview = MeshAsSkeleton;
		}
	}
	if (!Skeleton)
	{
		Err = FString::Printf(TEXT("AnimBlueprint '%s' not found; to create it pass skeleton (a Skeleton or SkeletalMesh path, e.g. "
			"/Game/Characters/Mannequins/Meshes/SK_Mannequin)"), *Path);
		return nullptr;
	}
	UClass* Parent = UAnimInstance::StaticClass();
	const FString ParentName = Str(Params, TEXT("parent_class"));
	if (!ParentName.IsEmpty())
	{
		Parent = FEpicUnrealMCPReflectionCommands::ResolveClass(ParentName);
		if (!Parent || !Parent->IsChildOf(UAnimInstance::StaticClass()))
		{
			Err = FString::Printf(TEXT("parent_class '%s' is not an AnimInstance class"), *ParentName);
			return nullptr;
		}
	}
	if (UEditorAssetLibrary::DoesAssetExist(Pkg))
	{
		Err = FString::Printf(TEXT("%s exists and is not an Animation Blueprint"), *Pkg);
		return nullptr;
	}
	UAnimBlueprintFactory* Factory = NewObject<UAnimBlueprintFactory>();
	Factory->TargetSkeleton = Skeleton;
	Factory->ParentClass = Parent;
	Factory->PreviewSkeletalMesh = Preview;
	Factory->BlueprintType = BPTYPE_Normal;
	UPackage* Package = CreatePackage(*Pkg);
	UAnimBlueprint* ABP = Cast<UAnimBlueprint>(Factory->FactoryCreateNew(UAnimBlueprint::StaticClass(), Package, FName(*Name),
		RF_Public | RF_Standalone | RF_Transactional, nullptr, GWarn));
	if (!ABP)
	{
		Err = TEXT("AnimBlueprint creation failed");
		return nullptr;
	}
	FAssetRegistryModule::AssetCreated(ABP);
	ABP->MarkPackageDirty();
	return ABP;
}

TSharedPtr<FJsonObject> HandleAnimBpApply(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("blueprint"));
	UAnimBlueprint* ABP = Cast<UAnimBlueprint>(LoadAssetOfClass(Path, UAnimBlueprint::StaticClass()));
	bool bCreated = false;
	if (!ABP)
	{
		FString Err;
		ABP = CreateAnimBlueprint(Params, Err);
		if (!ABP) return MCPError(Err);
		bCreated = true;
	}
	if (!FindAnimGraph(ABP))
	{
		return MCPError(FString::Printf(TEXT("%s has no AnimGraph (template/child AnimBlueprints are not supported)"), *ABP->GetName()));
	}
	TSharedPtr<FJsonObject> Out = ApplyBlueprintOps(ABP, bCreated, Params, &AnimExtension, [](FGraphApplyContext& Ctx)
	{
		// "output" = the AnimGraph's Output Pose node, so {"op":"connect","from":"loco.Pose","to":"output.Result"} works.
		if (UAnimGraphNode_Root* Root = FirstNodeOf<UAnimGraphNode_Root>(FindAnimGraph(Ctx.BP))) Ctx.Aliases.Add(TEXT("output"), Root);
	});
	Out->SetStringField(TEXT("skeleton"), ABP->TargetSkeleton ? ABP->TargetSkeleton->GetPathName() : FString());
	return Out;
}

// ---------------------------------------------------------------------------------------------
// animbp_export
// ---------------------------------------------------------------------------------------------

TSharedPtr<FJsonObject> ExportAnimNode(UEdGraphNode* N, bool bPins)
{
	TSharedPtr<FJsonObject> J = ExportGraphNode(N, bPins);
	if (Cast<UAnimGraphNode_SequencePlayer>(N)) J->SetStringField(TEXT("kind"), TEXT("sequence_player"));
	else if (Cast<UAnimGraphNode_BlendSpacePlayer>(N)) J->SetStringField(TEXT("kind"), TEXT("blendspace_player"));
	else if (Cast<UAnimGraphNode_StateMachineBase>(N)) J->SetStringField(TEXT("kind"), TEXT("state_machine"));
	else if (Cast<UAnimGraphNode_Slot>(N)) J->SetStringField(TEXT("kind"), TEXT("slot"));
	else if (Cast<UAnimGraphNode_Root>(N)) J->SetStringField(TEXT("kind"), TEXT("output"));
	else if (Cast<UAnimGraphNode_StateResult>(N)) J->SetStringField(TEXT("kind"), TEXT("state_result"));
	else if (Cast<UAnimGraphNode_TransitionResult>(N)) J->SetStringField(TEXT("kind"), TEXT("transition_result"));
	if (UAnimGraphNode_Base* AN = Cast<UAnimGraphNode_Base>(N))
	{
		if (UAnimationAsset* Asset = AN->GetAnimationAsset()) J->SetStringField(TEXT("asset"), Asset->GetPathName());
		FString E;
		if (Cast<UAnimGraphNode_Slot>(N))
		{
			if (TSharedPtr<FJsonValue> V = FEpicUnrealMCPReflectionCommands::GetPropertyByPath(N, TEXT("Node.SlotName"), E)) J->SetField(TEXT("slot"), V);
		}
		if (Cast<UAnimGraphNode_SequencePlayer>(N))
		{
			if (TSharedPtr<FJsonValue> V = FEpicUnrealMCPReflectionCommands::GetPropertyByPath(N, TEXT("Node.bLoopAnimation"), E)) J->SetField(TEXT("loop"), V);
		}
		if (UAnimGraphNode_StateMachineBase* SM = Cast<UAnimGraphNode_StateMachineBase>(N)) J->SetStringField(TEXT("name"), SM->GetStateMachineName());
	}
	// What feeds each input pin, by node title (pose links in particular), so a reader sees the chain without pins.
	TArray<TSharedPtr<FJsonValue>> Inputs;
	for (UEdGraphPin* P : N->Pins)
	{
		if (P && P->Direction == EGPD_Input)
		{
			for (UEdGraphPin* L : P->LinkedTo)
			{
				if (!L || !L->GetOwningNode()) continue;
				TSharedPtr<FJsonObject> I = MakeShared<FJsonObject>();
				I->SetStringField(TEXT("pin"), P->PinName.ToString());
				I->SetStringField(TEXT("from"), GraphNodeId(L->GetOwningNode()) + TEXT(".") + L->PinName.ToString());
				I->SetStringField(TEXT("from_title"), L->GetOwningNode()->GetNodeTitle(ENodeTitleType::ListView).ToString());
				Inputs.Add(MakeShared<FJsonValueObject>(I));
			}
		}
	}
	if (Inputs.Num()) J->SetArrayField(TEXT("inputs"), Inputs);
	return J;
}

TArray<TSharedPtr<FJsonValue>> ExportGraphNodes(UEdGraph* G, bool bPins)
{
	TArray<TSharedPtr<FJsonValue>> Out;
	for (UEdGraphNode* N : G ? G->Nodes : TArray<TObjectPtr<UEdGraphNode>>())
	{
		if (N) Out.Add(MakeShared<FJsonValueObject>(ExportAnimNode(N, bPins)));
	}
	return Out;
}

TSharedPtr<FJsonObject> ExportStateMachine(UAnimGraphNode_StateMachineBase* SM, bool bPins)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("name"), SM->GetStateMachineName());
	J->SetStringField(TEXT("id"), GraphNodeId(SM));
	J->SetStringField(TEXT("graph"), SM->GetGraph() ? SM->GetGraph()->GetName() : FString());
	UAnimationStateMachineGraph* MG = SM->EditorStateMachineGraph;
	TArray<TSharedPtr<FJsonValue>> States, Transitions;
	for (UEdGraphNode* N : MG ? MG->Nodes : TArray<TObjectPtr<UEdGraphNode>>())
	{
		if (UAnimStateEntryNode* Entry = Cast<UAnimStateEntryNode>(N))
		{
			if (Entry->Pins.Num() && Entry->Pins[0]->LinkedTo.Num())
			{
				J->SetStringField(TEXT("entry"), StateName(Entry->Pins[0]->LinkedTo[0]->GetOwningNode()));
			}
		}
		else if (UAnimStateTransitionNode* T = Cast<UAnimStateTransitionNode>(N))
		{
			TSharedPtr<FJsonObject> TJ = MakeShared<FJsonObject>();
			TJ->SetStringField(TEXT("id"), GraphNodeId(T));
			TJ->SetStringField(TEXT("from"), StateName(T->GetPreviousState()));
			TJ->SetStringField(TEXT("to"), StateName(T->GetNextState()));
			TJ->SetNumberField(TEXT("blend"), T->CrossfadeDuration);
			TJ->SetNumberField(TEXT("priority"), T->PriorityOrder);
			TJ->SetBoolField(TEXT("automatic"), T->bAutomaticRuleBasedOnSequencePlayerInState);
			TArray<FString> Reads, Calls;
			bool bRuleWired = false;
			for (UEdGraphNode* RN : T->BoundGraph ? T->BoundGraph->Nodes : TArray<TObjectPtr<UEdGraphNode>>())
			{
				if (const UK2Node_VariableGet* VG = Cast<UK2Node_VariableGet>(RN)) Reads.AddUnique(VG->VariableReference.GetMemberName().ToString());
				if (const UK2Node_CallFunction* CF = Cast<UK2Node_CallFunction>(RN)) Calls.Add(CF->FunctionReference.GetMemberName().ToString());
				if (UAnimGraphNode_TransitionResult* R = Cast<UAnimGraphNode_TransitionResult>(RN))
				{
					UEdGraphPin* P = FindNodePin(R, TEXT("bCanEnterTransition"), EGPD_Input);
					bRuleWired = P && (P->LinkedTo.Num() > 0 || P->DefaultValue == TEXT("true"));
				}
			}
			TJ->SetField(TEXT("reads"), StringArray(Reads));
			TJ->SetField(TEXT("calls"), StringArray(Calls));
			TJ->SetBoolField(TEXT("rule_wired"), bRuleWired);
			TJ->SetArrayField(TEXT("rule_nodes"), ExportGraphNodes(T->BoundGraph, bPins));
			Transitions.Add(MakeShared<FJsonValueObject>(TJ));
		}
		else if (UAnimStateNode* S = Cast<UAnimStateNode>(N))
		{
			TSharedPtr<FJsonObject> SJ = MakeShared<FJsonObject>();
			SJ->SetStringField(TEXT("name"), S->GetStateName());
			SJ->SetStringField(TEXT("id"), GraphNodeId(S));
			TArray<FString> Assets;
			for (UEdGraphNode* SN : S->BoundGraph ? S->BoundGraph->Nodes : TArray<TObjectPtr<UEdGraphNode>>())
			{
				if (UAnimGraphNode_Base* AN = Cast<UAnimGraphNode_Base>(SN))
				{
					if (UAnimationAsset* A = AN->GetAnimationAsset()) Assets.Add(A->GetPathName());
				}
			}
			SJ->SetField(TEXT("assets"), StringArray(Assets));
			UAnimGraphNode_StateResult* R = FirstNodeOf<UAnimGraphNode_StateResult>(S->BoundGraph);
			UEdGraphPin* RP = R ? FindNodePin(R, TEXT("Result"), EGPD_Input) : nullptr;
			SJ->SetBoolField(TEXT("pose_wired"), RP && RP->LinkedTo.Num() > 0);
			SJ->SetArrayField(TEXT("nodes"), ExportGraphNodes(S->BoundGraph, bPins));
			States.Add(MakeShared<FJsonValueObject>(SJ));
		}
	}
	J->SetArrayField(TEXT("states"), States);
	J->SetArrayField(TEXT("transitions"), Transitions);
	return J;
}

TSharedPtr<FJsonObject> HandleAnimBpExport(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("blueprint"));
	UAnimBlueprint* ABP = Cast<UAnimBlueprint>(LoadAssetOfClass(Path, UAnimBlueprint::StaticClass()));
	if (!ABP) return MCPError(FString::Printf(TEXT("Animation Blueprint '%s' not found"), *Path));
	bool bPins = false;
	Params->TryGetBoolField(TEXT("include_pins"), bPins);
	TSharedPtr<FJsonObject> J = ExportBlueprintSpec(ABP, { TEXT("EventGraph") }, bPins);
	J->SetStringField(TEXT("skeleton"), ABP->TargetSkeleton ? ABP->TargetSkeleton->GetPathName() : FString());
	if (USkeletalMesh* Preview = ABP->GetPreviewMesh()) J->SetStringField(TEXT("preview_mesh"), Preview->GetPathName());
	UEdGraph* AnimGraph = FindAnimGraph(ABP);
	J->SetArrayField(TEXT("anim_graph"), ExportGraphNodes(AnimGraph, bPins));
	TArray<UAnimGraphNode_StateMachineBase*> Machines;
	AllStateMachines(ABP, Machines);
	TArray<TSharedPtr<FJsonValue>> SMs;
	for (UAnimGraphNode_StateMachineBase* SM : Machines) SMs.Add(MakeShared<FJsonValueObject>(ExportStateMachine(SM, bPins)));
	J->SetArrayField(TEXT("state_machines"), SMs);
	// What reaches the Output Pose, so a checker can tell a wired graph from a dangling one.
	if (UAnimGraphNode_Root* Root = FirstNodeOf<UAnimGraphNode_Root>(AnimGraph))
	{
		TArray<FString> Chain;
		UEdGraphNode* Cur = Root;
		for (int32 Guard = 0; Cur && Guard < 32; ++Guard)
		{
			UEdGraphNode* Next = nullptr;
			for (UEdGraphPin* P : Cur->Pins)
			{
				if (P && P->Direction == EGPD_Input && P->LinkedTo.Num() && P->LinkedTo[0]->GetOwningNode()
					&& P->LinkedTo[0]->GetOwningNode()->IsA<UAnimGraphNode_Base>())
				{
					Next = P->LinkedTo[0]->GetOwningNode();
					break;
				}
			}
			if (Next)
			{
				FString Kind = ExportAnimNode(Next, false)->GetStringField(TEXT("kind"));
				if (UAnimGraphNode_StateMachineBase* SM = Cast<UAnimGraphNode_StateMachineBase>(Next)) Kind += TEXT(":") + SM->GetStateMachineName();
				Chain.Add(Kind);
			}
			Cur = Next;
		}
		J->SetField(TEXT("output_chain"), StringArray(Chain));
	}
	return J;
}

// ---------------------------------------------------------------------------------------------
// anim_asset_apply: montages and notifies
// ---------------------------------------------------------------------------------------------

FName SlotNameOf(const FString& In)
{
	return FName(*(In.IsEmpty() ? FString(TEXT("DefaultGroup.DefaultSlot")) : In));
}

FSlotAnimationTrack* FindSlot(UAnimMontage* M, FName Slot)
{
	for (FSlotAnimationTrack& T : M->SlotAnimTracks)
	{
		if (T.SlotName == Slot) return &T;
	}
	return nullptr;
}

bool AddSegment(UAnimMontage* M, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	UAnimSequenceBase* Seq = LoadAs<UAnimSequenceBase>(Str(Op, TEXT("sequence")));
	if (!Seq || Seq->IsA<UAnimMontage>())
	{
		Err = FString::Printf(TEXT("'%s' is not an animation sequence"), *Str(Op, TEXT("sequence")));
		return false;
	}
	if (M->GetSkeleton() && Seq->GetSkeleton() && !M->GetSkeleton()->IsCompatibleForEditor(Seq->GetSkeleton()))
	{
		Err = FString::Printf(TEXT("%s uses skeleton %s, the montage uses %s"), *Seq->GetName(), *Seq->GetSkeleton()->GetName(), *M->GetSkeleton()->GetName());
		return false;
	}
	const FName Slot = SlotNameOf(Str(Op, TEXT("slot")));
	FSlotAnimationTrack* Track = FindSlot(M, Slot);
	if (!Track)
	{
		Track = &M->AddSlot(Slot);
	}
	FAnimSegment Seg;
	Seg.SetAnimReference(Seq, true);
	double D = 0;
	if (Op->TryGetNumberField(TEXT("start"), D)) Seg.AnimStartTime = float(D);
	if (Op->TryGetNumberField(TEXT("end"), D) && D > 0) Seg.AnimEndTime = float(D);
	if (Op->TryGetNumberField(TEXT("play_rate"), D) && D > 0) Seg.AnimPlayRate = float(D);
	if (Op->TryGetNumberField(TEXT("loop_count"), D) && D >= 1) Seg.LoopingCount = int32(D);
	float StartPos = 0.f;
	for (const FAnimSegment& S : Track->AnimTrack.AnimSegments) StartPos = FMath::Max(StartPos, S.GetEndPos());
	Seg.StartPos = StartPos;
	Track->AnimTrack.AnimSegments.Add(Seg);
	return true;
}

void EnsureNotifyTrack(UAnimSequenceBase* Seq, FName Track)
{
	if (!UAnimationBlueprintLibrary::IsValidAnimNotifyTrackName(Seq, Track))
	{
		UAnimationBlueprintLibrary::AddAnimationNotifyTrack(Seq, Track);
	}
}

bool AddNotify(UAnimSequenceBase* Seq, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	const FString Name = Str(Op, TEXT("name")), ClassName = Str(Op, TEXT("class")), Track = Str(Op, TEXT("track"));
	double Time = 0, Duration = 0;
	Op->TryGetNumberField(TEXT("time"), Time);
	Op->TryGetNumberField(TEXT("duration"), Duration);
	if (Time < 0 || Time > Seq->GetPlayLength() + KINDA_SMALL_NUMBER)
	{
		Err = FString::Printf(TEXT("time %.3f is outside %s (0..%.3f s)"), Time, *Seq->GetName(), Seq->GetPlayLength());
		return false;
	}
	const FName TrackName(*(Track.IsEmpty() ? FString(TEXT("1")) : Track));
	EnsureNotifyTrack(Seq, TrackName);
	if (!ClassName.IsEmpty())
	{
		UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(ClassName);
		if (C && C->IsChildOf(UAnimNotifyState::StaticClass()))
		{
			UAnimNotifyState* S = UAnimationBlueprintLibrary::AddAnimationNotifyStateEvent(Seq, TrackName, float(Time), float(FMath::Max(Duration, 0.01)), C);
			if (!S) { Err = TEXT("could not add the notify state"); return false; }
			TArray<FString> Errors;
			FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(S, Obj(Op, TEXT("properties")), Errors);
			Err = FString::Join(Errors, TEXT("; "));
			return Err.IsEmpty();
		}
		if (!C || !C->IsChildOf(UAnimNotify::StaticClass()))
		{
			Err = FString::Printf(TEXT("'%s' is not an AnimNotify or AnimNotifyState class (AnimNotify_PlaySound, AnimNotify_PlayMontageNotify, "
				"AnimNotifyState_Trail, a Blueprint notify class path ..._C)"), *ClassName);
			return false;
		}
		UAnimNotify* N = UAnimationBlueprintLibrary::AddAnimationNotifyEvent(Seq, TrackName, float(Time), C);
		if (!N) { Err = TEXT("could not add the notify"); return false; }
		TArray<FString> Errors;
		TSharedPtr<FJsonObject> Props = Obj(Op, TEXT("properties"));
		if (!Name.IsEmpty() && N->GetClass()->FindPropertyByName(TEXT("NotifyName")))
		{
			// AnimNotify_PlayMontageNotify: the name reaches PlayMontage's OnNotifyBegin.
			if (!Props.IsValid()) Props = MakeShared<FJsonObject>();
			Props->SetStringField(TEXT("NotifyName"), Name);
		}
		FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(N, Props, Errors);
		Err = FString::Join(Errors, TEXT("; "));
		Seq->RefreshCacheData();
		return Err.IsEmpty();
	}
	if (Name.IsEmpty())
	{
		Err = TEXT("add_notify needs \"name\" (a skeleton notify: fires AnimNotify_<name> in the AnimBP) or \"class\"");
		return false;
	}
	// Named (skeleton) notify: no notify object, the AnimBP gets an "AnimNotify_<Name>" event.
	TSet<FGuid> Before;
	for (const FAnimNotifyEvent& E : Seq->Notifies) Before.Add(E.Guid);
	UAnimationBlueprintLibrary::AddAnimationNotifyEvent(Seq, TrackName, float(Time), nullptr);
	for (FAnimNotifyEvent& E : Seq->Notifies)
	{
		if (!Before.Contains(E.Guid))
		{
			E.NotifyName = FName(*Name);
			if (USkeleton* Skel = Seq->GetSkeleton()) Skel->AddNewAnimationNotify(E.NotifyName);
			Seq->RefreshCacheData();
			return true;
		}
	}
	Err = TEXT("could not add the notify");
	return false;
}

TSharedPtr<FJsonObject> ExportNotifies(UAnimSequenceBase* Seq)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	TArray<TSharedPtr<FJsonValue>> Notifies;
	for (const FAnimNotifyEvent& E : Seq->Notifies)
	{
		TSharedPtr<FJsonObject> N = MakeShared<FJsonObject>();
		N->SetStringField(TEXT("name"), E.NotifyName.ToString());
		N->SetNumberField(TEXT("time"), E.GetTime());
		if (E.Notify) N->SetStringField(TEXT("class"), E.Notify->GetClass()->GetName());
		if (E.NotifyStateClass)
		{
			N->SetStringField(TEXT("class"), E.NotifyStateClass->GetClass()->GetName());
			N->SetNumberField(TEXT("duration"), E.GetDuration());
		}
		Notifies.Add(MakeShared<FJsonValueObject>(N));
	}
	J->SetArrayField(TEXT("notifies"), Notifies);
	return J;
}

TSharedPtr<FJsonObject> ExportMontage(UAnimMontage* M)
{
	TSharedPtr<FJsonObject> J = ExportNotifies(M);
	J->SetStringField(TEXT("path"), M->GetPathName());
	J->SetStringField(TEXT("skeleton"), M->GetSkeleton() ? M->GetSkeleton()->GetPathName() : FString());
	J->SetNumberField(TEXT("length"), M->GetPlayLength());
	TArray<TSharedPtr<FJsonValue>> Slots, Sections;
	for (const FSlotAnimationTrack& T : M->SlotAnimTracks)
	{
		TSharedPtr<FJsonObject> SJ = MakeShared<FJsonObject>();
		SJ->SetStringField(TEXT("slot"), T.SlotName.ToString());
		TArray<TSharedPtr<FJsonValue>> Segs;
		for (const FAnimSegment& S : T.AnimTrack.AnimSegments)
		{
			TSharedPtr<FJsonObject> G = MakeShared<FJsonObject>();
			G->SetStringField(TEXT("sequence"), S.GetAnimReference() ? S.GetAnimReference()->GetPathName() : FString());
			G->SetNumberField(TEXT("start_pos"), S.StartPos);
			G->SetNumberField(TEXT("length"), S.GetLength());
			G->SetNumberField(TEXT("play_rate"), S.AnimPlayRate);
			Segs.Add(MakeShared<FJsonValueObject>(G));
		}
		SJ->SetArrayField(TEXT("segments"), Segs);
		Slots.Add(MakeShared<FJsonValueObject>(SJ));
	}
	for (const FCompositeSection& S : M->CompositeSections)
	{
		TSharedPtr<FJsonObject> SJ = MakeShared<FJsonObject>();
		SJ->SetStringField(TEXT("name"), S.SectionName.ToString());
		SJ->SetNumberField(TEXT("time"), S.GetTime());
		SJ->SetStringField(TEXT("next"), S.NextSectionName.IsNone() ? FString() : S.NextSectionName.ToString());
		Sections.Add(MakeShared<FJsonValueObject>(SJ));
	}
	J->SetArrayField(TEXT("slots"), Slots);
	J->SetArrayField(TEXT("sections"), Sections);
	J->SetNumberField(TEXT("blend_in"), M->BlendIn.GetBlendTime());
	J->SetNumberField(TEXT("blend_out"), M->BlendOut.GetBlendTime());
	return J;
}

UAnimMontage* CreateMontage(const FString& Path, const TSharedPtr<FJsonObject>& Params, FString& Err)
{
	FString Pkg, Name;
	if (!SplitAssetPath(Path, Pkg, Name, Err)) return nullptr;
	if (UEditorAssetLibrary::DoesAssetExist(Pkg))
	{
		Err = FString::Printf(TEXT("%s exists and is not a montage"), *Pkg);
		return nullptr;
	}
	USkeleton* Skeleton = LoadAs<USkeleton>(Str(Params, TEXT("skeleton")));
	UAnimSequenceBase* First = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
	if (Params->TryGetArrayField(TEXT("operations"), Ops))
	{
		for (const TSharedPtr<FJsonValue>& V : *Ops)
		{
			if (V->AsObject().IsValid() && Str(V->AsObject(), TEXT("op")) == TEXT("add_segment"))
			{
				First = LoadAs<UAnimSequenceBase>(Str(V->AsObject(), TEXT("sequence")));
				break;
			}
		}
	}
	if (!Skeleton && First) Skeleton = First->GetSkeleton();
	if (!Skeleton)
	{
		Err = TEXT("to create a montage pass skeleton, or an add_segment op with a sequence");
		return nullptr;
	}
	UAnimMontageFactory* Factory = NewObject<UAnimMontageFactory>();
	Factory->TargetSkeleton = Skeleton;
	UPackage* Package = CreatePackage(*Pkg);
	UAnimMontage* M = Cast<UAnimMontage>(Factory->FactoryCreateNew(UAnimMontage::StaticClass(), Package, FName(*Name),
		RF_Public | RF_Standalone | RF_Transactional, nullptr, GWarn));
	if (!M)
	{
		Err = TEXT("montage creation failed");
		return nullptr;
	}
	FAssetRegistryModule::AssetCreated(M);
	M->MarkPackageDirty();
	return M;
}

TSharedPtr<FJsonObject> HandleAnimAssetApply(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("asset"));
	bool bSave = true, bAtomic = true;
	Params->TryGetBoolField(TEXT("save"), bSave);
	Params->TryGetBoolField(TEXT("atomic"), bAtomic);
	UAnimSequenceBase* Seq = Cast<UAnimSequenceBase>(LoadAssetOfClass(Path, UAnimSequenceBase::StaticClass()));
	bool bCreated = false;
	if (!Seq)
	{
		if (Str(Params, TEXT("create")).ToLower() != TEXT("montage"))
		{
			return MCPError(FString::Printf(TEXT("animation asset '%s' not found (create=\"montage\" makes a montage)"), *Path));
		}
		FString Err;
		Seq = CreateMontage(Path, Params, Err);
		if (!Seq) return MCPError(Err);
		bCreated = true;
	}
	UAnimMontage* Montage = Cast<UAnimMontage>(Seq);

	TArray<TSharedPtr<FJsonValue>> Failed;
	int32 Applied = 0;
	const int32 QueueBefore = (GEditor && GEditor->Trans) ? GEditor->Trans->GetQueueLength() : 0;
	{
		FScopedTransaction Transaction(FText::FromString(FString::Printf(TEXT("MCP: anim_asset_apply %s"), *Seq->GetName())));
		Seq->Modify();
		const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
		Params->TryGetArrayField(TEXT("operations"), Ops);
		for (int32 I = 0; Ops && I < Ops->Num(); ++I)
		{
			const TSharedPtr<FJsonObject> Op = (*Ops)[I]->AsObject();
			const FString Kind = Str(Op, TEXT("op"));
			FString Err;
			bool bOk = false;
			const bool bMontageOp = Kind == TEXT("add_segment") || Kind == TEXT("add_slot") || Kind == TEXT("add_section")
				|| Kind == TEXT("link_section") || Kind == TEXT("remove_section") || Kind == TEXT("set_blend");
			if (bMontageOp && !Montage)
			{
				Err = FString::Printf(TEXT("%s is not a montage"), *Seq->GetName());
			}
			else if (Kind == TEXT("add_segment"))
			{
				bOk = AddSegment(Montage, Op, Err);
				if (bOk)
				{
					Montage->SetCompositeLength(Montage->CalculateSequenceLength());
					Montage->UpdateLinkableElements();
				}
			}
			else if (Kind == TEXT("add_slot"))
			{
				const FName Slot = SlotNameOf(Str(Op, TEXT("slot")));
				if (!FindSlot(Montage, Slot)) Montage->AddSlot(Slot);
				bOk = true;
			}
			else if (Kind == TEXT("add_section"))
			{
				double Time = 0;
				Op->TryGetNumberField(TEXT("time"), Time);
				const FName Name(*Str(Op, TEXT("name")));
				if (Montage->GetSectionIndex(Name) != INDEX_NONE)
				{
					// Re-timing an existing one (e.g. the factory's "Default" at 0) is fine.
					Montage->GetAnimCompositeSection(Montage->GetSectionIndex(Name)).Link(Montage, float(Time));
					bOk = true;
				}
				else
				{
					bOk = Montage->AddAnimCompositeSection(Name, float(Time)) != INDEX_NONE;
					if (!bOk) Err = FString::Printf(TEXT("could not add section '%s'"), *Name.ToString());
				}
				const FString Next = Str(Op, TEXT("next"));
				if (bOk && Op->HasField(TEXT("next")))
				{
					Montage->GetAnimCompositeSection(Montage->GetSectionIndex(Name)).NextSectionName = Next.IsEmpty() ? NAME_None : FName(*Next);
				}
			}
			else if (Kind == TEXT("link_section"))
			{
				const int32 From = Montage->GetSectionIndex(FName(*Str(Op, TEXT("from"))));
				const FString To = Str(Op, TEXT("to"));
				if (From == INDEX_NONE || (!To.IsEmpty() && Montage->GetSectionIndex(FName(*To)) == INDEX_NONE))
				{
					Err = FString::Printf(TEXT("section '%s' not found"), From == INDEX_NONE ? *Str(Op, TEXT("from")) : *To);
				}
				else
				{
					Montage->GetAnimCompositeSection(From).NextSectionName = To.IsEmpty() ? NAME_None : FName(*To);
					bOk = true;
				}
			}
			else if (Kind == TEXT("remove_section"))
			{
				const int32 Idx = Montage->GetSectionIndex(FName(*Str(Op, TEXT("name"))));
				bOk = Idx != INDEX_NONE && Montage->CompositeSections.Num() > 1 && Montage->DeleteAnimCompositeSection(Idx);
				if (!bOk) Err = TEXT("section not found (or it is the last one)");
			}
			else if (Kind == TEXT("set_blend"))
			{
				double V = 0;
				if (Op->TryGetNumberField(TEXT("blend_in"), V)) Montage->BlendIn.SetBlendTime(float(V));
				if (Op->TryGetNumberField(TEXT("blend_out"), V)) Montage->BlendOut.SetBlendTime(float(V));
				if (Op->TryGetNumberField(TEXT("blend_out_trigger_time"), V)) Montage->BlendOutTriggerTime = float(V);
				bOk = true;
			}
			else if (Kind == TEXT("add_notify"))
			{
				bOk = AddNotify(Seq, Op, Err);
			}
			else if (Kind == TEXT("remove_notifies"))
			{
				const int32 N = UAnimationBlueprintLibrary::RemoveAnimationNotifyEventsByName(Seq, FName(*Str(Op, TEXT("name"))));
				bOk = N > 0;
				if (!bOk) Err = FString::Printf(TEXT("no notify named '%s'"), *Str(Op, TEXT("name")));
			}
			else if (Kind == TEXT("set"))
			{
				TArray<FString> Errors;
				FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(Seq, Obj(Op, TEXT("properties")), Errors);
				Err = FString::Join(Errors, TEXT("; "));
				bOk = Errors.Num() == 0;
			}
			else
			{
				Err = FString::Printf(TEXT("unknown op '%s' (add_segment, add_slot, add_section, link_section, remove_section, set_blend, "
					"add_notify, remove_notifies, set)"), *Kind);
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
		if (Montage) Montage->UpdateLinkableElements();
		Seq->RefreshCacheData();
		Seq->PostEditChange();
	}
	const bool bRollback = bAtomic && Failed.Num() > 0;
	bool bRolledBack = false;
	if (bRollback && !bCreated && GEditor && GEditor->Trans && GEditor->Trans->GetQueueLength() > QueueBefore)
	{
		GEditor->UndoTransaction(false);
		bRolledBack = true;
	}
	TSharedPtr<FJsonObject> Out = Montage ? ExportMontage(Montage) : ExportNotifies(Seq);
	Out->SetStringField(TEXT("path"), Seq->GetPathName());
	if (bRollback && bCreated)
	{
		UEditorAssetLibrary::DeleteLoadedAsset(Seq);
		bRolledBack = true;
	}
	bool bSaved = false;
	if (!bRollback && bSave) bSaved = SaveAsset(Seq);
	Out->SetBoolField(TEXT("ok"), Failed.Num() == 0);
	Out->SetBoolField(TEXT("created"), bCreated && !bRolledBack);
	Out->SetNumberField(TEXT("applied"), Applied);
	Out->SetArrayField(TEXT("failed"), Failed);
	Out->SetBoolField(TEXT("rolled_back"), bRolledBack);
	Out->SetBoolField(TEXT("saved"), bSaved);
	return Out;
}

TSharedPtr<FJsonObject> HandleAnimAssetExport(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("asset"));
	UAnimSequenceBase* Seq = Cast<UAnimSequenceBase>(LoadAssetOfClass(Path, UAnimSequenceBase::StaticClass()));
	if (!Seq) return MCPError(FString::Printf(TEXT("animation asset '%s' not found"), *Path));
	TSharedPtr<FJsonObject> Out = Cast<UAnimMontage>(Seq) ? ExportMontage(Cast<UAnimMontage>(Seq)) : ExportNotifies(Seq);
	Out->SetStringField(TEXT("path"), Seq->GetPathName());
	Out->SetStringField(TEXT("class"), Seq->GetClass()->GetName());
	Out->SetNumberField(TEXT("length"), Seq->GetPlayLength());
	return Out;
}
} // namespace

void FMCPAnimAuthoring::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("animbp_export"), &HandleAnimBpExport, false);
	// Both manage their own transaction so a failed batch is undone as one step.
	Registry.Register(TEXT("animbp_apply"), &HandleAnimBpApply, false);
	Registry.Register(TEXT("anim_asset_export"), &HandleAnimAssetExport, false);
	Registry.Register(TEXT("anim_asset_apply"), &HandleAnimAssetApply, false);
}
