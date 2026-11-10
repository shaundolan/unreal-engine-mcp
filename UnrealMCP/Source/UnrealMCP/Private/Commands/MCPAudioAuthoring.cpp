// metasound_apply / metasound_export / metasound_nodes: MetaSound sources built with the MetaSound Builder API
// (UMetaSoundBuilderSubsystem, the same API Python and Blueprints get). sound_cue_apply: a SoundCue from waves.
#include "Commands/MCPAuthoringCommands.h"

#include "Editor.h"
#include "EditorAssetLibrary.h"
#include "Metasound.h"
#include "MetasoundBuilderBase.h"
#include "MetasoundBuilderSubsystem.h"
#include "MetasoundDocumentInterface.h"
#include "MetasoundEditorSubsystem.h"
#include "MetasoundFrontendDocument.h"
#include "MetasoundFrontendQuery.h"
#include "MetasoundFrontendSearchEngine.h"
#include "MetasoundSource.h"
#include "Misc/PackageName.h"
#include "Sound/SoundCue.h"
#include "Sound/SoundNodeModulator.h"
#include "Sound/SoundNodeRandom.h"
#include "Sound/SoundNodeWavePlayer.h"
#include "Sound/SoundNodeLooping.h"
#include "Sound/SoundNodeMixer.h"
#include "Sound/SoundWave.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "UObject/Package.h"

using namespace MCPAuthoring;

namespace
{
FString Str(const TSharedPtr<FJsonObject>& O, const TCHAR* F)
{
	FString V;
	O->TryGetStringField(F, V);
	return V;
}

FString AudioNorm(const FString& S)
{
	return S.Replace(TEXT(" "), TEXT("")).Replace(TEXT("_"), TEXT("")).Replace(TEXT("."), TEXT("")).ToLower();
}

const TCHAR* ResultName(EMetaSoundBuilderResult R)
{
	return R == EMetaSoundBuilderResult::Succeeded ? TEXT("ok") : TEXT("failed");
}

// ---------------------------------------------------------------------------------------------
// Node classes
// ---------------------------------------------------------------------------------------------

/** Registered node classes (externals: oscillators, filters, envelopes, Wave Player, math, triggers, ...). */
TArray<Metasound::Frontend::FMetaSoundClassInfo> AllNodeClasses()
{
	using namespace Metasound::Frontend;
	TArray<FMetaSoundClassInfo> Out;
	for (const FMetaSoundClassInfo& C : ISearchEngine::Get().FindAllClasses(ISearchEngine::EResultVersion::Highest))
	{
		if (C.ClassType == EMetasoundFrontendClassType::External || C.ClassType == EMetasoundFrontendClassType::Graph
			|| C.ClassType == EMetasoundFrontendClassType::Template)
		{
			Out.Add(C);
		}
	}
	return Out;
}

FString ClassString(const FMetasoundFrontendClassName& N)
{
	return N.ToString();
}

/** "UE.Sine.Audio", "Sine" (+ optional variant), "ADSR Envelope", "Wave Player" (+ "Mono"/"Stereo"), ... */
bool ResolveNodeClass(const FString& Want, const FString& Variant, FMetasoundFrontendClassName& Out, int32& OutMajor, FString& Err)
{
	using namespace Metasound::Frontend;
	const TArray<FMetaSoundClassInfo> All = AllNodeClasses();
	TArray<const FMetaSoundClassInfo*> Matches;
	for (const FMetaSoundClassInfo& C : All)
	{
		const FString Full = ClassString(C.ClassName);
		if (AudioNorm(Full) == AudioNorm(Want))
		{
			Out = C.ClassName;
			OutMajor = C.Version.Major;
			return true;
		}
		if (AudioNorm(C.ClassName.Name.ToString()) == AudioNorm(Want)) Matches.Add(&C);
	}
	if (Matches.Num() == 0)
	{
		TArray<FString> Close;
		for (const FMetaSoundClassInfo& C : All)
		{
			if (ClassString(C.ClassName).Contains(Want, ESearchCase::IgnoreCase) && Close.Num() < 12) Close.Add(ClassString(C.ClassName));
		}
		Err = FString::Printf(TEXT("no MetaSound node class '%s'%s (search with metasound_nodes)"), *Want,
			Close.Num() ? *FString::Printf(TEXT("; similar: %s"), *FString::Join(Close, TEXT(", "))) : TEXT(""));
		return false;
	}
	// Variants: the one asked for, else Float (math, envelopes, Map Range), Audio, none, Mono.
	const FMetaSoundClassInfo* Pick = Matches.Num() == 1 && Variant.IsEmpty() ? Matches[0] : nullptr;
	for (const TCHAR* V : { *Variant, TEXT("Float"), TEXT("Audio"), TEXT(""), TEXT("Mono") })
	{
		for (const FMetaSoundClassInfo* M : Matches)
		{
			const FString MV = M->ClassName.Variant.IsNone() ? FString() : M->ClassName.Variant.ToString();
			if ((FString(V).IsEmpty() && MV.IsEmpty() && Variant.IsEmpty()) || (!FString(V).IsEmpty() && MV.Equals(V, ESearchCase::IgnoreCase)))
			{
				Pick = M;
				break;
			}
		}
		if (Pick || !Variant.IsEmpty()) break;
	}
	if (!Pick && Variant.IsEmpty()) Pick = Matches[0];
	if (!Pick)
	{
		TArray<FString> Vs;
		for (const FMetaSoundClassInfo* M : Matches) Vs.Add(ClassString(M->ClassName));
		Err = FString::Printf(TEXT("'%s' has no variant '%s' (%s)"), *Want, *Variant, *FString::Join(Vs, TEXT(", ")));
		return false;
	}
	Out = Pick->ClassName;
	OutMajor = Pick->Version.Major;
	return true;
}

TSharedPtr<FJsonObject> HandleNodes(const TSharedPtr<FJsonObject>& Params)
{
	using namespace Metasound::Frontend;
	const FString Query = Str(Params, TEXT("query"));
	double Limit = 60;
	Params->TryGetNumberField(TEXT("limit"), Limit);
	TArray<FString> Words;
	Query.ParseIntoArrayWS(Words);
	TArray<TSharedPtr<FJsonValue>> Out;
	int32 Total = 0;
	for (const FMetaSoundClassInfo& C : AllNodeClasses())
	{
		const FString Full = ClassString(C.ClassName);
		FString Hay = Full;
#if WITH_EDITORONLY_DATA
		for (const FMetaSoundClassVertexInfo& V : C.InterfaceInfo.Inputs) Hay += TEXT(" ") + V.Name.ToString();
#endif
		bool bAll = true;
		for (const FString& W : Words) bAll &= Hay.Contains(W, ESearchCase::IgnoreCase);
		if (!bAll) continue;
		++Total;
		if (Out.Num() >= int32(Limit)) continue;
		TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
		J->SetStringField(TEXT("class"), Full);
		J->SetNumberField(TEXT("major_version"), C.Version.Major);
#if WITH_EDITORONLY_DATA
		auto Pins = [](const TArray<FMetaSoundClassVertexInfo>& Vs)
		{
			TArray<TSharedPtr<FJsonValue>> A;
			for (const FMetaSoundClassVertexInfo& V : Vs)
			{
				A.Add(MakeShared<FJsonValueString>(V.Name.ToString() + TEXT(":") + V.TypeName.ToString()));
			}
			return A;
		};
		J->SetArrayField(TEXT("inputs"), Pins(C.InterfaceInfo.Inputs));
		J->SetArrayField(TEXT("outputs"), Pins(C.InterfaceInfo.Outputs));
#endif
		Out.Add(MakeShared<FJsonValueObject>(J));
	}
	TSharedPtr<FJsonObject> R = MakeShared<FJsonObject>();
	R->SetArrayField(TEXT("nodes"), Out);
	R->SetNumberField(TEXT("total"), Total);
	return R;
}

// ---------------------------------------------------------------------------------------------
// Apply
// ---------------------------------------------------------------------------------------------

struct FMSContext
{
	UMetaSoundBuilderBase* Builder = nullptr;
	TMap<FString, FMetaSoundNodeHandle> Aliases;
};

bool LiteralFor(const FName DataType, const TSharedPtr<FJsonValue>& V, FMetasoundFrontendLiteral& Out, FString& Err)
{
	const FString T = DataType.ToString();
	if (!V.IsValid())
	{
		Err = TEXT("missing value");
		return false;
	}
	if (T == TEXT("Bool")) Out.Set(V->Type == EJson::Boolean ? V->AsBool() : V->AsNumber() != 0);
	else if (T == TEXT("Int32") || T.StartsWith(TEXT("Enum:"))) Out.Set(int32(V->AsNumber()));
	else if (T == TEXT("Float") || T == TEXT("Time") || T == TEXT("Frequency")) Out.Set(float(V->AsNumber()));
	else if (T == TEXT("String")) Out.Set(V->AsString());
	else if (T.EndsWith(TEXT("[]")))
	{
		TArray<float> Fs;
		for (const TSharedPtr<FJsonValue>& X : V->AsArray()) Fs.Add(float(X->AsNumber()));
		Out.Set(Fs);
	}
	else if (V->Type == EJson::String && V->AsString().StartsWith(TEXT("/")))
	{
		UObject* O = StaticLoadObject(UObject::StaticClass(), nullptr, *V->AsString(), nullptr, LOAD_NoWarn | LOAD_Quiet);
		if (!O) O = LoadAssetOfClass(V->AsString(), UObject::StaticClass());
		if (!O)
		{
			Err = FString::Printf(TEXT("asset '%s' not found"), *V->AsString());
			return false;
		}
		Out.Set(O);
	}
	else if (V->Type == EJson::Number) Out.Set(float(V->AsNumber()));
	else if (V->Type == EJson::Boolean) Out.Set(V->AsBool());
	else
	{
		Err = FString::Printf(TEXT("cannot make a %s literal from this value"), *T);
		return false;
	}
	return true;
}

/** "Float", "float", "trigger", "audio", "time", "int", "bool", "wave" -> MetaSound data type names. */
FName DataTypeName(const FString& In)
{
	const FString L = In.ToLower();
	if (L == TEXT("float") || L == TEXT("number")) return TEXT("Float");
	if (L == TEXT("int") || L == TEXT("int32") || L == TEXT("integer")) return TEXT("Int32");
	if (L == TEXT("bool") || L == TEXT("boolean")) return TEXT("Bool");
	if (L == TEXT("trigger")) return TEXT("Trigger");
	if (L == TEXT("audio")) return TEXT("Audio");
	if (L == TEXT("time")) return TEXT("Time");
	if (L == TEXT("string")) return TEXT("String");
	if (L == TEXT("wave") || L == TEXT("waveasset") || L == TEXT("soundwave")) return TEXT("WaveAsset");
	if (L == TEXT("float[]")) return TEXT("Float:Array");
	if (L == TEXT("wave[]") || L == TEXT("waveasset[]")) return TEXT("WaveAsset:Array");
	return FName(*In);
}

/** Graph input / output whose name matches: exact, its last dotted part ("UE.Source.OnPlay" <- "OnPlay"), or an alias. */
bool MatchGraphMember(const TArray<FName>& Names, const FString& Want, FName& Out, FString& Err, const TCHAR* What)
{
	FString W = AudioNorm(Want);
	if (W == TEXT("play")) W = TEXT("onplay");
	if (W == TEXT("finished")) W = TEXT("onfinished");
	const bool bLeft = W == TEXT("left") || W == TEXT("outleft") || W == TEXT("audio0") || W == TEXT("audioleft");
	const bool bRight = W == TEXT("right") || W == TEXT("outright") || W == TEXT("audio1") || W == TEXT("audioright");
	const bool bAudio = W == TEXT("audio") || W == TEXT("out") || W == TEXT("mono") || W == TEXT("outmono") || W == TEXT("output");
	TArray<FName> Hits;
	for (const FName& N : Names)
	{
		const FString S = N.ToString();
		FString Last = S;
		S.Split(TEXT("."), nullptr, &Last, ESearchCase::IgnoreCase, ESearchDir::FromEnd);
		if (AudioNorm(S) == W || AudioNorm(Last) == W) { Out = N; return true; }
		if (bLeft && S.EndsWith(TEXT("Audio:0"))) Hits.Add(N);
		else if (bRight && S.EndsWith(TEXT("Audio:1"))) Hits.Add(N);
		else if (bAudio && S.Contains(TEXT("OutputFormat"))) Hits.Add(N);
	}
	if (Hits.Num() == 1) { Out = Hits[0]; return true; }
	TArray<FString> All;
	for (const FName& N : Names) All.Add(N.ToString());
	Err = FString::Printf(TEXT("%s '%s' %s (graph has: %s)"), What, *Want, Hits.Num() > 1 ? TEXT("is ambiguous: use Left/Right") : TEXT("not found"),
		*FString::Join(All, TEXT(", ")));
	return false;
}

bool ResolveNode(FMSContext& Ctx, const FString& Ref, FMetaSoundNodeHandle& Out, FString& Err)
{
	if (const FMetaSoundNodeHandle* H = Ctx.Aliases.Find(Ref)) { Out = *H; return true; }
	FGuid G;
	if (FGuid::Parse(Ref, G))
	{
		Out = FMetaSoundNodeHandle(G);
		if (Ctx.Builder->ContainsNode(Out)) return true;
	}
	Err = FString::Printf(TEXT("unknown node '%s' (an id from this call or a node id from metasound_export; \"graph\" for graph inputs/outputs)"), *Ref);
	return false;
}

/** A node pin by name, case/space-insensitive: returns its real vertex name and data type. */
bool ResolvePin(FMSContext& Ctx, const FMetaSoundNodeHandle& Node, const FString& Want, bool bInput, FName& OutName, FName& OutType, FString& Err)
{
	EMetaSoundBuilderResult R;
	TArray<FString> Names;
	auto Try = [&](FName N, FName T)
	{
		Names.Add(N.ToString());
		if (AudioNorm(N.ToString()) == AudioNorm(Want)) { OutName = N; OutType = T; return true; }
		return false;
	};
	if (bInput)
	{
		for (const FMetaSoundBuilderNodeInputHandle& H : Ctx.Builder->FindNodeInputs(Node, R))
		{
			FName N, T;
			Ctx.Builder->GetNodeInputData(H, N, T, R);
			if (Try(N, T)) return true;
		}
	}
	else
	{
		for (const FMetaSoundBuilderNodeOutputHandle& H : Ctx.Builder->FindNodeOutputs(Node, R))
		{
			FName N, T;
			Ctx.Builder->GetNodeOutputData(H, N, T, R);
			if (Try(N, T)) return true;
		}
	}
	Err = FString::Printf(TEXT("no %s '%s' (has: %s)"), bInput ? TEXT("input") : TEXT("output"), *Want, *FString::Join(Names, TEXT(", ")));
	return false;
}

bool SetInputs(FMSContext& Ctx, const FMetaSoundNodeHandle& Node, const TSharedPtr<FJsonObject>& Inputs, FString& Err)
{
	if (!Inputs.IsValid()) return true;
	for (const auto& Pair : Inputs->Values)
	{
		const FString Key(*Pair.Key);
		FName Name, Type;
		if (!ResolvePin(Ctx, Node, Key, true, Name, Type, Err)) return false;
		FMetasoundFrontendLiteral L;
		if (!LiteralFor(Type, Pair.Value, L, Err))
		{
			Err = FString::Printf(TEXT("input %s (%s): %s"), *Key, *Type.ToString(), *Err);
			return false;
		}
		EMetaSoundBuilderResult R;
		const FMetaSoundBuilderNodeInputHandle H = Ctx.Builder->FindNodeInputByName(Node, Name, R);
		Ctx.Builder->SetNodeInputDefault(H, L, R);
		if (R != EMetaSoundBuilderResult::Succeeded)
		{
			Err = FString::Printf(TEXT("could not set input %s"), *Key);
			return false;
		}
	}
	return true;
}

TSharedPtr<FJsonObject> ObjField(const TSharedPtr<FJsonObject>& O, const TCHAR* F)
{
	const TSharedPtr<FJsonObject>* V = nullptr;
	return O->TryGetObjectField(F, V) ? *V : TSharedPtr<FJsonObject>();
}

bool OpConnect(FMSContext& Ctx, const TSharedPtr<FJsonObject>& Op, FString& Err)
{
	FString FN, FP, TN, TP;
	if (!Str(Op, TEXT("from")).Split(TEXT("."), &FN, &FP) || !Str(Op, TEXT("to")).Split(TEXT("."), &TN, &TP))
	{
		Err = TEXT("connect needs from/to as \"node.pin\" (\"graph.OnPlay\", \"graph.RPM\" for graph inputs; \"graph.Audio\" / \"graph.Left\" for outputs)");
		return false;
	}
	EMetaSoundBuilderResult R = EMetaSoundBuilderResult::Failed;
	const bool bFromGraph = FN.Equals(TEXT("graph"), ESearchCase::IgnoreCase) || FN.Equals(TEXT("input"), ESearchCase::IgnoreCase);
	const bool bToGraph = TN.Equals(TEXT("graph"), ESearchCase::IgnoreCase) || TN.Equals(TEXT("output"), ESearchCase::IgnoreCase);
	if (bFromGraph && bToGraph)
	{
		Err = TEXT("connect a graph input to a node, or a node to a graph output");
		return false;
	}
	if (bFromGraph)
	{
		FName In, ToPin, ToType;
		FMetaSoundNodeHandle To;
		if (!MatchGraphMember(Ctx.Builder->GetGraphInputNames(R), FP, In, Err, TEXT("graph input"))) return false;
		if (!ResolveNode(Ctx, TN, To, Err) || !ResolvePin(Ctx, To, TP, true, ToPin, ToType, Err)) return false;
		Ctx.Builder->ConnectGraphInputToNode(In, To, ToPin, R);
	}
	else if (bToGraph)
	{
		FName Out, FromPin, FromType;
		FMetaSoundNodeHandle From;
		if (!ResolveNode(Ctx, FN, From, Err) || !ResolvePin(Ctx, From, FP, false, FromPin, FromType, Err)) return false;
		if (!MatchGraphMember(Ctx.Builder->GetGraphOutputNames(R), TP, Out, Err, TEXT("graph output"))) return false;
		Ctx.Builder->ConnectNodeToGraphOutput(From, FromPin, Out, R);
	}
	else
	{
		FMetaSoundNodeHandle From, To;
		FName FromPin, FromType, ToPin, ToType;
		if (!ResolveNode(Ctx, FN, From, Err) || !ResolvePin(Ctx, From, FP, false, FromPin, FromType, Err)) return false;
		if (!ResolveNode(Ctx, TN, To, Err) || !ResolvePin(Ctx, To, TP, true, ToPin, ToType, Err)) return false;
		if (FromType != ToType)
		{
			Err = FString::Printf(TEXT("type mismatch: %s is %s, %s is %s (MetaSounds don't convert implicitly: add a converter node)"),
				*Str(Op, TEXT("from")), *FromType.ToString(), *Str(Op, TEXT("to")), *ToType.ToString());
			return false;
		}
		Ctx.Builder->ConnectNodes(From, FromPin, To, ToPin, R);
	}
	if (R != EMetaSoundBuilderResult::Succeeded)
	{
		Err = FString::Printf(TEXT("connect %s -> %s failed (types must match; an input takes one connection)"), *Str(Op, TEXT("from")), *Str(Op, TEXT("to")));
		return false;
	}
	return true;
}

bool ApplyOp(FMSContext& Ctx, const TSharedPtr<FJsonObject>& Op, TSharedPtr<FJsonObject>& Ids, FString& Err)
{
	const FString Kind = Str(Op, TEXT("op"));
	EMetaSoundBuilderResult R = EMetaSoundBuilderResult::Failed;
	if (Kind == TEXT("add_node"))
	{
		FMetasoundFrontendClassName Class;
		int32 Major = 1;
		FString Want = Str(Op, TEXT("class"));
		if (Want.IsEmpty()) Want = Str(Op, TEXT("node"));
		if (!ResolveNodeClass(Want, Str(Op, TEXT("variant")), Class, Major, Err)) return false;
		const FMetaSoundNodeHandle H = Ctx.Builder->AddNodeByClassName(Class, R, Major);
		if (R != EMetaSoundBuilderResult::Succeeded)
		{
			Err = FString::Printf(TEXT("could not add %s"), *ClassString(Class));
			return false;
		}
		const FString Id = Str(Op, TEXT("id"));
		if (!Id.IsEmpty()) Ctx.Aliases.Add(Id, H);
		Ids->SetStringField(Id.IsEmpty() ? ClassString(Class) : Id, H.NodeID.ToString());
		double X = 0, Y = 0;
		if (Op->TryGetNumberField(TEXT("x"), X) && Op->TryGetNumberField(TEXT("y"), Y) && GEditor)
		{
			if (UMetaSoundEditorSubsystem* ES = GEditor->GetEditorSubsystem<UMetaSoundEditorSubsystem>()) ES->SetNodeLocation(Ctx.Builder, H, FVector2D(X, Y), R);
		}
		return SetInputs(Ctx, H, ObjField(Op, TEXT("inputs")), Err);
	}
	if (Kind == TEXT("set") || Kind == TEXT("set_inputs"))
	{
		FMetaSoundNodeHandle H;
		if (!ResolveNode(Ctx, Str(Op, TEXT("node")), H, Err)) return false;
		return SetInputs(Ctx, H, ObjField(Op, TEXT("inputs")), Err);
	}
	if (Kind == TEXT("add_input") || Kind == TEXT("add_output"))
	{
		const FName Name(*Str(Op, TEXT("name")));
		const FName Type = DataTypeName(Str(Op, TEXT("type")).IsEmpty() ? TEXT("Float") : Str(Op, TEXT("type")));
		FMetasoundFrontendLiteral L;
		if (Op->HasField(TEXT("default")) && !LiteralFor(Type, Op->TryGetField(TEXT("default")), L, Err)) return false;
		if (Kind == TEXT("add_input")) Ctx.Builder->AddGraphInputNode(Name, Type, L, R, false);
		else Ctx.Builder->AddGraphOutputNode(Name, Type, L, R, false);
		if (R != EMetaSoundBuilderResult::Succeeded)
		{
			Err = FString::Printf(TEXT("could not add graph %s '%s' of type %s (name taken? type names: Float, Int32, Bool, Trigger, "
				"Audio, Time, String, WaveAsset)"), Kind == TEXT("add_input") ? TEXT("input") : TEXT("output"), *Name.ToString(), *Type.ToString());
			return false;
		}
		return true;
	}
	if (Kind == TEXT("set_input_default"))
	{
		FName Name;
		if (!MatchGraphMember(Ctx.Builder->GetGraphInputNames(R), Str(Op, TEXT("name")), Name, Err, TEXT("graph input"))) return false;
		FName Type, Ignored;
		FMetaSoundBuilderNodeOutputHandle OutH;
		Ctx.Builder->FindGraphInputNode(Name, Type, OutH, R);
		FMetasoundFrontendLiteral L;
		if (!LiteralFor(Type, Op->TryGetField(TEXT("value")), L, Err)) return false;
		Ctx.Builder->SetGraphInputDefault(Name, L, R);
		if (R != EMetaSoundBuilderResult::Succeeded) { Err = TEXT("could not set the default"); return false; }
		return true;
	}
	if (Kind == TEXT("connect")) return OpConnect(Ctx, Op, Err);
	if (Kind == TEXT("disconnect"))
	{
		FString TN, TP;
		FMetaSoundNodeHandle To;
		FName Pin, Type;
		if (!Str(Op, TEXT("to")).Split(TEXT("."), &TN, &TP) || !ResolveNode(Ctx, TN, To, Err) || !ResolvePin(Ctx, To, TP, true, Pin, Type, Err))
		{
			if (Err.IsEmpty()) Err = TEXT("disconnect needs to: \"node.input\"");
			return false;
		}
		Ctx.Builder->DisconnectNodeInput(Ctx.Builder->FindNodeInputByName(To, Pin, R), R);
		return R == EMetaSoundBuilderResult::Succeeded;
	}
	if (Kind == TEXT("remove_node"))
	{
		FMetaSoundNodeHandle H;
		if (!ResolveNode(Ctx, Str(Op, TEXT("node")), H, Err)) return false;
		Ctx.Builder->RemoveNode(H, R);
		return R == EMetaSoundBuilderResult::Succeeded;
	}
	if (Kind == TEXT("add_interface"))
	{
		Ctx.Builder->AddInterface(FName(*Str(Op, TEXT("name"))), R);
		if (R != EMetaSoundBuilderResult::Succeeded) { Err = FString::Printf(TEXT("interface '%s' not found (e.g. UE.Attenuation, UE.Spatialization)"), *Str(Op, TEXT("name"))); return false; }
		return true;
	}
	Err = FString::Printf(TEXT("unknown op '%s' (add_node, set, add_input, add_output, set_input_default, connect, disconnect, remove_node, add_interface)"), *Kind);
	return false;
}

UObject* LoadMetaSound(const FString& Path)
{
	UObject* O = LoadAssetOfClass(Path, UObject::StaticClass());
	return (O && O->GetClass()->ImplementsInterface(UMetaSoundDocumentInterface::StaticClass())) ? O : nullptr;
}

TSharedPtr<FJsonObject> ExportMetaSound(UObject* Obj);

TSharedPtr<FJsonObject> HandleApply(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("asset"));
	FString Pkg, Name, Err;
	if (!SplitAssetPath(Path, Pkg, Name, Err)) return MCPError(Err);
	UMetaSoundBuilderSubsystem* BS = UMetaSoundBuilderSubsystem::Get();
	UMetaSoundEditorSubsystem* ES = GEditor ? GEditor->GetEditorSubsystem<UMetaSoundEditorSubsystem>() : nullptr;
	if (!BS || !ES) return MCPError(TEXT("MetaSound builder subsystems are not available (is the Metasound plugin enabled?)"));

	bool bSave = true, bAudition = false;
	Params->TryGetBoolField(TEXT("save"), bSave);
	Params->TryGetBoolField(TEXT("audition"), bAudition);
	UObject* Existing = LoadMetaSound(Path);
	const bool bCreate = Existing == nullptr;
	if (bCreate && UEditorAssetLibrary::DoesAssetExist(Pkg)) return MCPError(FString::Printf(TEXT("%s exists and is not a MetaSound"), *Pkg));

	FMSContext Ctx;
	EMetaSoundBuilderResult R = EMetaSoundBuilderResult::Failed;
	const FName BuilderName(*FString::Printf(TEXT("UnrealMCP_%s_%s"), *Name, *FGuid::NewGuid().ToString(EGuidFormats::Short)));
	if (bCreate)
	{
		FString Format = Str(Params, TEXT("format")).ToLower();
		bool bOneShot = true;
		Params->TryGetBoolField(TEXT("one_shot"), bOneShot);
		const EMetaSoundOutputAudioFormat Fmt = Format == TEXT("stereo") ? EMetaSoundOutputAudioFormat::Stereo
			: Format == TEXT("quad") ? EMetaSoundOutputAudioFormat::Quad : EMetaSoundOutputAudioFormat::Mono;
		FMetaSoundBuilderNodeOutputHandle OnPlay;
		FMetaSoundBuilderNodeInputHandle OnFinished;
		TArray<FMetaSoundBuilderNodeInputHandle> AudioOuts;
		Ctx.Builder = BS->CreateSourceBuilder(BuilderName, OnPlay, OnFinished, AudioOuts, R, Fmt, bOneShot);
	}
	else
	{
		Ctx.Builder = ES->FindOrBeginBuilding(TScriptInterface<IMetaSoundDocumentInterface>(Existing), R);
	}
	if (!Ctx.Builder || R != EMetaSoundBuilderResult::Succeeded) return MCPError(TEXT("could not start a MetaSound builder"));

	TArray<TSharedPtr<FJsonValue>> Failed;
	TSharedPtr<FJsonObject> Ids = MakeShared<FJsonObject>();
	int32 Applied = 0;
	const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
	if (Params->TryGetArrayField(TEXT("operations"), Ops))
	{
		for (int32 I = 0; I < Ops->Num(); ++I)
		{
			const TSharedPtr<FJsonObject> Op = (*Ops)[I]->AsObject();
			FString OpErr;
			if (Op.IsValid() && ApplyOp(Ctx, Op, Ids, OpErr))
			{
				++Applied;
				continue;
			}
			TSharedPtr<FJsonObject> F = MakeShared<FJsonObject>();
			F->SetNumberField(TEXT("index"), I);
			F->SetStringField(TEXT("op"), Op.IsValid() ? Str(Op, TEXT("op")) : FString());
			F->SetStringField(TEXT("error"), OpErr.IsEmpty() ? FString(TEXT("failed")) : OpErr);
			Failed.Add(MakeShared<FJsonValueObject>(F));
			if (bCreate) break; // nothing is written for a failed create
		}
	}
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	UObject* Asset = Existing;
	if (bCreate)
	{
		if (Failed.Num() == 0)
		{
			TScriptInterface<IMetaSoundDocumentInterface> Built = ES->BuildToAsset(Ctx.Builder, TEXT("UnrealMCP"), Name,
				FPackageName::GetLongPackagePath(Pkg), R, nullptr);
			Asset = Built.GetObject();
			if (!Asset || R != EMetaSoundBuilderResult::Succeeded)
			{
				BS->UnregisterBuilder(BuilderName);
				return MCPError(TEXT("BuildToAsset failed"));
			}
		}
		BS->UnregisterBuilder(BuilderName);
	}
	if (Asset && Failed.Num() == 0)
	{
		Asset->MarkPackageDirty();
		if (bSave) Out->SetBoolField(TEXT("saved"), SaveAsset(Asset));
		if (bAudition && GEditor)
		{
			if (USoundBase* S = Cast<USoundBase>(Asset)) GEditor->PlayPreviewSound(S);
			Out->SetBoolField(TEXT("auditioning"), true);
		}
	}
	Out->SetBoolField(TEXT("ok"), Failed.Num() == 0);
	Out->SetBoolField(TEXT("created"), bCreate && Failed.Num() == 0);
	Out->SetStringField(TEXT("asset"), Asset ? Asset->GetPathName() : Path);
	Out->SetNumberField(TEXT("applied"), Applied);
	Out->SetArrayField(TEXT("failed"), Failed);
	Out->SetObjectField(TEXT("ids"), Ids);
	if (!bCreate && Failed.Num()) Out->SetStringField(TEXT("note"), TEXT("edits to an existing MetaSound apply op by op: the ops before the failure were kept"));
	if (Asset && Failed.Num() == 0) Out->SetObjectField(TEXT("graph"), ExportMetaSound(Asset));
	return Out;
}

// ---------------------------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------------------------

TSharedPtr<FJsonObject> ExportMetaSound(UObject* Obj)
{
	const IMetaSoundDocumentInterface* DI = Cast<IMetaSoundDocumentInterface>(Obj);
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	if (!DI) return J;
	const FMetasoundFrontendDocument& Doc = DI->GetConstDocument();
	J->SetStringField(TEXT("path"), Obj->GetPathName());
	J->SetStringField(TEXT("class"), Obj->GetClass()->GetName());
	if (const UMetaSoundSource* Src = Cast<UMetaSoundSource>(Obj))
	{
		J->SetStringField(TEXT("format"), Src->OutputFormat == EMetaSoundOutputAudioFormat::Stereo ? TEXT("stereo")
			: Src->OutputFormat == EMetaSoundOutputAudioFormat::Mono ? TEXT("mono") : TEXT("other"));
	}
	TArray<FString> Interfaces;
	for (const FMetasoundFrontendVersion& V : Doc.Interfaces) Interfaces.Add(V.Name.ToString());
	J->SetField(TEXT("interfaces"), StringArray(Interfaces));
	J->SetBoolField(TEXT("one_shot"), Interfaces.ContainsByPredicate([](const FString& S) { return S.Contains(TEXT("OneShot")); }));

	auto Members = [](const auto& Vs, bool bInput)
	{
		TArray<TSharedPtr<FJsonValue>> A;
		for (const auto& V : Vs)
		{
			TSharedPtr<FJsonObject> X = MakeShared<FJsonObject>();
			X->SetStringField(TEXT("name"), V.Name.ToString());
			X->SetStringField(TEXT("type"), V.TypeName.ToString());
			if constexpr (std::is_same_v<std::decay_t<decltype(V)>, FMetasoundFrontendClassInput>)
			{
				if (const FMetasoundFrontendLiteral* L = V.FindConstDefault(Metasound::Frontend::DefaultPageID)) X->SetStringField(TEXT("default"), L->ToString());
			}
			A.Add(MakeShared<FJsonValueObject>(X));
		}
		return A;
	};
	const FMetasoundFrontendClassInterface& GI = Doc.RootGraph.GetDefaultInterface();
	J->SetArrayField(TEXT("inputs"), Members(GI.Inputs, true));
	J->SetArrayField(TEXT("outputs"), Members(GI.Outputs, false));

	TMap<FGuid, const FMetasoundFrontendClass*> Classes;
	for (const FMetasoundFrontendClass& C : Doc.Dependencies) Classes.Add(C.ID, &C);
	const FMetasoundFrontendGraph& G = Doc.RootGraph.GetConstDefaultGraph();
	TMap<FGuid, const FMetasoundFrontendNode*> NodeById;
	TArray<TSharedPtr<FJsonValue>> Nodes;
	for (const FMetasoundFrontendNode& N : G.Nodes)
	{
		NodeById.Add(N.GetID(), &N);
		TSharedPtr<FJsonObject> X = MakeShared<FJsonObject>();
		X->SetStringField(TEXT("id"), N.GetID().ToString());
		X->SetStringField(TEXT("name"), N.Name.ToString());
		if (const FMetasoundFrontendClass* const* C = Classes.Find(N.ClassID))
		{
			X->SetStringField(TEXT("class"), (*C)->Metadata.GetClassName().ToString());
			X->SetStringField(TEXT("kind"), LexToString((*C)->Metadata.GetType()));
		}
		TArray<TSharedPtr<FJsonValue>> Ins, Outs;
		TSharedPtr<FJsonObject> Defaults = MakeShared<FJsonObject>();
		for (const FMetasoundFrontendVertex& V : N.Interface.Inputs)
		{
			Ins.Add(MakeShared<FJsonValueString>(V.Name.ToString() + TEXT(":") + V.TypeName.ToString()));
			for (const FMetasoundFrontendVertexLiteral& L : N.InputLiterals)
			{
				if (L.VertexID == V.VertexID) Defaults->SetStringField(V.Name.ToString(), L.Value.ToString());
			}
		}
		for (const FMetasoundFrontendVertex& V : N.Interface.Outputs) Outs.Add(MakeShared<FJsonValueString>(V.Name.ToString() + TEXT(":") + V.TypeName.ToString()));
		X->SetArrayField(TEXT("inputs"), Ins);
		X->SetArrayField(TEXT("outputs"), Outs);
		X->SetObjectField(TEXT("defaults"), Defaults);
		Nodes.Add(MakeShared<FJsonValueObject>(X));
	}
	J->SetArrayField(TEXT("nodes"), Nodes);
	auto VertexName = [&](const FGuid& NodeId, const FGuid& VertexId, bool bInput) -> FString
	{
		const FMetasoundFrontendNode* const* N = NodeById.Find(NodeId);
		if (!N) return FString();
		for (const FMetasoundFrontendVertex& V : bInput ? (*N)->Interface.Inputs : (*N)->Interface.Outputs)
		{
			if (V.VertexID == VertexId) return V.Name.ToString();
		}
		return FString();
	};
	TArray<TSharedPtr<FJsonValue>> Edges;
	for (const FMetasoundFrontendEdge& E : G.Edges)
	{
		TSharedPtr<FJsonObject> X = MakeShared<FJsonObject>();
		X->SetStringField(TEXT("from"), E.FromNodeID.ToString() + TEXT(".") + VertexName(E.FromNodeID, E.FromVertexID, false));
		X->SetStringField(TEXT("to"), E.ToNodeID.ToString() + TEXT(".") + VertexName(E.ToNodeID, E.ToVertexID, true));
		Edges.Add(MakeShared<FJsonValueObject>(X));
	}
	J->SetArrayField(TEXT("edges"), Edges);
	return J;
}

TSharedPtr<FJsonObject> HandleExport(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("asset"));
	UObject* Obj = LoadMetaSound(Path);
	if (!Obj) return MCPError(FString::Printf(TEXT("MetaSound '%s' not found"), *Path));
	return ExportMetaSound(Obj);
}

// ---------------------------------------------------------------------------------------------
// SoundCue basics: waves -> (random) -> (modulator) -> (looping) -> output
// ---------------------------------------------------------------------------------------------

TSharedPtr<FJsonObject> HandleSoundCue(const TSharedPtr<FJsonObject>& Params)
{
	const FString Path = Str(Params, TEXT("asset"));
	TArray<FString> Waves;
	Params->TryGetStringArrayField(TEXT("waves"), Waves);
	if (Waves.Num() == 0) return MCPError(TEXT("sound_cue_apply needs waves: [\"/Game/Audio/SW_Step1\", ...]"));
	TArray<USoundWave*> Loaded;
	for (const FString& W : Waves)
	{
		USoundWave* SW = Cast<USoundWave>(LoadAssetOfClass(W, USoundWave::StaticClass()));
		if (!SW) return MCPError(FString::Printf(TEXT("SoundWave '%s' not found"), *W));
		Loaded.Add(SW);
	}
	USoundCue* Cue = Cast<USoundCue>(LoadAssetOfClass(Path, USoundCue::StaticClass()));
	bool bCreated = false;
	if (!Cue)
	{
		FString Err;
		Cue = Cast<USoundCue>(CreateAssetObject(Path, USoundCue::StaticClass(), Err));
		if (!Cue) return MCPError(Err);
		bCreated = true;
	}
	Cue->Modify();
	FString Mode = Str(Params, TEXT("mode")).ToLower();
	if (Mode.IsEmpty()) Mode = Loaded.Num() > 1 ? TEXT("random") : TEXT("single");
	TArray<USoundNode*> Players;
	for (USoundWave* SW : Loaded)
	{
		USoundNodeWavePlayer* P = Cue->ConstructSoundNode<USoundNodeWavePlayer>();
		P->SetSoundWave(SW);
		Players.Add(P);
	}
	USoundNode* Top = Players[0];
	if (Players.Num() > 1)
	{
		USoundNode* Combine = nullptr;
		if (Mode == TEXT("mix")) Combine = Cue->ConstructSoundNode<USoundNodeMixer>();
		else
		{
			USoundNodeRandom* Rnd = Cue->ConstructSoundNode<USoundNodeRandom>();
			bool bNoRepeat = true;
			Params->TryGetBoolField(TEXT("no_repeats"), bNoRepeat);
			Rnd->bRandomizeWithoutReplacement = bNoRepeat;
			Combine = Rnd;
		}
		Combine->ChildNodes = Players;
		if (USoundNodeRandom* Rnd = Cast<USoundNodeRandom>(Combine))
		{
			Rnd->Weights.Init(1.f, Players.Num());
		}
		if (USoundNodeMixer* Mix = Cast<USoundNodeMixer>(Combine))
		{
			Mix->InputVolume.Init(1.f, Players.Num());
		}
		Top = Combine;
	}
	const TArray<TSharedPtr<FJsonValue>>* Pitch = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Volume = nullptr;
	const bool bPitch = Params->TryGetArrayField(TEXT("pitch_range"), Pitch) && Pitch->Num() == 2;
	const bool bVolume = Params->TryGetArrayField(TEXT("volume_range"), Volume) && Volume->Num() == 2;
	if (bPitch || bVolume)
	{
		USoundNodeModulator* M = Cue->ConstructSoundNode<USoundNodeModulator>();
		if (bPitch) { M->PitchMin = float((*Pitch)[0]->AsNumber()); M->PitchMax = float((*Pitch)[1]->AsNumber()); }
		if (bVolume) { M->VolumeMin = float((*Volume)[0]->AsNumber()); M->VolumeMax = float((*Volume)[1]->AsNumber()); }
		M->ChildNodes = { Top };
		Top = M;
	}
	bool bLoop = false;
	if (Params->TryGetBoolField(TEXT("loop"), bLoop) && bLoop)
	{
		USoundNodeLooping* L = Cue->ConstructSoundNode<USoundNodeLooping>();
		L->ChildNodes = { Top };
		Top = L;
	}
	Cue->FirstNode = Top;
#if WITH_EDITOR
	Cue->LinkGraphNodesFromSoundNodes();
#endif
	Cue->CacheAggregateValues();
	Cue->MarkPackageDirty();
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("ok"), true);
	Out->SetBoolField(TEXT("created"), bCreated);
	Out->SetStringField(TEXT("asset"), Cue->GetPathName());
	Out->SetStringField(TEXT("first_node"), Top->GetClass()->GetName());
	Out->SetNumberField(TEXT("waves"), Players.Num());
	bool bSave = true;
	Params->TryGetBoolField(TEXT("save"), bSave);
	if (bSave) Out->SetBoolField(TEXT("saved"), SaveAsset(Cue));
	return Out;
}
} // namespace

void FMCPAudioAuthoring::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("metasound_apply"), &HandleApply, false);
	Registry.Register(TEXT("metasound_export"), &HandleExport, false);
	Registry.Register(TEXT("metasound_nodes"), &HandleNodes, false);
	Registry.Register(TEXT("sound_cue_apply"), &HandleSoundCue, false);
}
