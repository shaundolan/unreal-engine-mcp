#include "UnrealMCPToolset.h"

#include "Async/Future.h"
#include "Containers/Ticker.h"
#include "Editor.h"
#include "EpicUnrealMCPBridge.h"
#include "HAL/PlatformTime.h"
#include "Misc/Guid.h"
#include "Policies/CondensedJsonPrintPolicy.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

DEFINE_LOG_CATEGORY_STATIC(LogUnrealMCPToolsets, Log, All);

namespace UnrealMCPToolsets
{
	using FResult = TValueOrError<FString, FString>;

	FString ToJson(const TSharedRef<FJsonObject>& Obj)
	{
		FString Out;
		TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out);
		FJsonSerializer::Serialize(Obj, Writer);
		return Out;
	}

	TSharedPtr<FJsonObject> ParseObject(const FString& Text)
	{
		TSharedPtr<FJsonObject> Obj;
		TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Obj))
		{
			return nullptr;
		}
		return Obj;
	}

	/** A double-quoted Python (and JSON) string literal, like json.dumps(s) on the Python side. */
	FString PyStr(const FString& S)
	{
		FString Out;
		Out.Reserve(S.Len() + 16);
		Out.AppendChar(TEXT('"'));
		for (const TCHAR C : S)
		{
			switch (C)
			{
			case TEXT('"'): Out += TEXT("\\\""); break;
			case TEXT('\\'): Out += TEXT("\\\\"); break;
			case TEXT('\n'): Out += TEXT("\\n"); break;
			case TEXT('\r'): Out += TEXT("\\r"); break;
			case TEXT('\t'): Out += TEXT("\\t"); break;
			default:
				if (C < 0x20)
				{
					Out += FString::Printf(TEXT("\\u%04x"), uint32(C));
				}
				else
				{
					Out.AppendChar(C);
				}
			}
		}
		Out.AppendChar(TEXT('"'));
		return Out;
	}

	UEpicUnrealMCPBridge* GetBridge()
	{
		return GEditor ? GEditor->GetEditorSubsystem<UEpicUnrealMCPBridge>() : nullptr;
	}

	/** Bridge reply {status, result | error} -> tool result. ok_flag tools report failure as result.ok == false. */
	FResult FinishCommandReply(const TSharedRef<FJsonObject>& Reply, bool bOkFlag)
	{
		FString Status;
		Reply->TryGetStringField(TEXT("status"), Status);
		if (Status != TEXT("success"))
		{
			FString Error = TEXT("Unknown error");
			Reply->TryGetStringField(TEXT("error"), Error);
			return MakeError(Error);
		}
		const TSharedPtr<FJsonObject>* ResultPtr = nullptr;
		TSharedRef<FJsonObject> Result = Reply->TryGetObjectField(TEXT("result"), ResultPtr) && ResultPtr && ResultPtr->IsValid()
			? ResultPtr->ToSharedRef() : MakeShared<FJsonObject>();
		bool bOk = true;
		if (bOkFlag && Result->TryGetBoolField(TEXT("ok"), bOk) && !bOk)
		{
			// Keep the whole structured report (failed ops, compile errors): it is what the agent needs to fix it.
			return MakeError(ToJson(Result));
		}
		Result->SetBoolField(TEXT("success"), true);
		return MakeValue(ToJson(Result));
	}
}

using namespace UnrealMCPToolsets;

TSharedPtr<FUnrealMCPToolset> FUnrealMCPToolset::FromManifest(const TSharedPtr<FJsonObject>& ToolsetJson,
	const FString& InVersion, const TSharedRef<const FString>& InPythonWrapper)
{
	if (!ToolsetJson.IsValid())
	{
		return nullptr;
	}
	TSharedRef<FUnrealMCPToolset> Toolset = MakeShared<FUnrealMCPToolset>();
	Toolset->Version = InVersion;
	Toolset->PythonWrapper = InPythonWrapper;
	if (!ToolsetJson->TryGetStringField(TEXT("name"), Toolset->Name) || Toolset->Name.IsEmpty())
	{
		UE_LOG(LogUnrealMCPToolsets, Error, TEXT("UnrealMCPToolsets: toolset entry without a name"));
		return nullptr;
	}
	ToolsetJson->TryGetStringField(TEXT("description"), Toolset->Description);

	// The schema Epic's registry serves (describe_toolset, eager tools/list): fully qualified "Toolset.tool" names.
	TSharedRef<FJsonObject> Schema = MakeShared<FJsonObject>();
	Schema->SetStringField(TEXT("name"), Toolset->Name);
	Schema->SetStringField(TEXT("version"), InVersion);
	Schema->SetStringField(TEXT("description"), Toolset->Description);
	TArray<TSharedPtr<FJsonValue>> SchemaTools;

	const TArray<TSharedPtr<FJsonValue>>* ToolValues = nullptr;
	ToolsetJson->TryGetArrayField(TEXT("tools"), ToolValues);
	for (const TSharedPtr<FJsonValue>& ToolValue : ToolValues ? *ToolValues : TArray<TSharedPtr<FJsonValue>>())
	{
		const TSharedPtr<FJsonObject> Tool = ToolValue.IsValid() ? ToolValue->AsObject() : nullptr;
		FString ToolName;
		const TSharedPtr<FJsonObject>* Backend = nullptr;
		const TSharedPtr<FJsonObject>* InputSchema = nullptr;
		if (!Tool.IsValid() || !Tool->TryGetStringField(TEXT("name"), ToolName)
			|| !Tool->TryGetObjectField(TEXT("backend"), Backend) || !Tool->TryGetObjectField(TEXT("inputSchema"), InputSchema))
		{
			UE_LOG(LogUnrealMCPToolsets, Error, TEXT("UnrealMCPToolsets: malformed tool in %s"), *Toolset->Name);
			continue;
		}

		FToolSpec Spec;
		FString Kind;
		(*Backend)->TryGetStringField(TEXT("kind"), Kind);
		if (Kind == TEXT("command"))
		{
			Spec.Kind = EKind::Command;
			(*Backend)->TryGetStringField(TEXT("command"), Spec.Target);
		}
		else if (Kind == TEXT("editor_script"))
		{
			Spec.Kind = EKind::EditorScript;
			(*Backend)->TryGetStringField(TEXT("script"), Spec.ScriptBody);
		}
		else if (Kind == TEXT("frame_stats"))
		{
			Spec.Kind = EKind::FrameStats;
		}
		else
		{
			UE_LOG(LogUnrealMCPToolsets, Error, TEXT("UnrealMCPToolsets: %s.%s has unknown backend kind '%s'"), *Toolset->Name, *ToolName, *Kind);
			continue;
		}
		(*Backend)->TryGetBoolField(TEXT("ok_flag"), Spec.bOkFlag);
		(*Backend)->TryGetNumberField(TEXT("timeout_s"), Spec.TimeoutS);
		const TSharedPtr<FJsonObject>* Rename = nullptr;
		if ((*Backend)->TryGetObjectField(TEXT("rename"), Rename))
		{
			for (const auto& Pair : (*Rename)->Values)
			{
				Spec.Rename.Add(FString(*Pair.Key), Pair.Value->AsString());
			}
		}
		const TSharedPtr<FJsonObject>* Properties = nullptr;
		if ((*InputSchema)->TryGetObjectField(TEXT("properties"), Properties))
		{
			for (const auto& Pair : (*Properties)->Values)
			{
				const TSharedPtr<FJsonObject> Prop = Pair.Value.IsValid() ? Pair.Value->AsObject() : nullptr;
				if (Prop.IsValid() && Prop->HasField(TEXT("default")))
				{
					Spec.Defaults.Add(FString(*Pair.Key), Prop->TryGetField(TEXT("default")));
				}
			}
		}
		Toolset->Tools.Add(ToolName, MoveTemp(Spec));

		TSharedRef<FJsonObject> SchemaTool = MakeShared<FJsonObject>();
		SchemaTool->SetStringField(TEXT("name"), Toolset->Name + TEXT(".") + ToolName);
		FString ToolDescription;
		Tool->TryGetStringField(TEXT("description"), ToolDescription);
		SchemaTool->SetStringField(TEXT("description"), ToolDescription);
		SchemaTool->SetObjectField(TEXT("inputSchema"), *InputSchema);
		SchemaTools.Add(MakeShared<FJsonValueObject>(SchemaTool));
	}
	Schema->SetArrayField(TEXT("tools"), SchemaTools);
	Toolset->SchemaJson = ToJson(Schema);
	return Toolset;
}

TSharedRef<FJsonObject> FUnrealMCPToolset::PrepareParams(const FToolSpec& Spec, const TSharedPtr<FJsonObject>& Arguments) const
{
	// Mirror of unreal_mcp.native_toolsets.native_params (tests/test_native_toolsets.py checks it against the
	// Python tools): schema defaults, then the caller's arguments, nulls dropped, then renames.
	TMap<FString, TSharedPtr<FJsonValue>> Merged = Spec.Defaults;
	if (Arguments.IsValid())
	{
		for (const auto& Pair : Arguments->Values)
		{
			Merged.Add(FString(*Pair.Key), Pair.Value);
		}
	}
	TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Merged)
	{
		if (!Pair.Value.IsValid() || Pair.Value->Type == EJson::Null)
		{
			continue;
		}
		const FString* Renamed = Spec.Rename.Find(Pair.Key);
		Params->SetField(Renamed ? *Renamed : Pair.Key, Pair.Value);
	}
	return Params;
}

TFuture<TValueOrError<FString, FString>> FUnrealMCPToolset::ExecuteToolInternal(const FString& ToolName, const FString& JsonInput)
{
	const FToolSpec* Spec = Tools.Find(ToolName);
	if (!Spec)
	{
		return MakeFulfilledPromise<FResult>(MakeError(FString::Printf(TEXT("Unknown tool %s.%s"), *Name, *ToolName))).GetFuture();
	}
	TSharedPtr<FJsonObject> Arguments = JsonInput.TrimStartAndEnd().IsEmpty() ? MakeShared<FJsonObject>() : ParseObject(JsonInput);
	if (!Arguments.IsValid())
	{
		return MakeFulfilledPromise<FResult>(MakeError(FString::Printf(TEXT("%s: arguments are not a JSON object"), *ToolName))).GetFuture();
	}
	const TSharedRef<FJsonObject> Params = PrepareParams(*Spec, Arguments);
	switch (Spec->Kind)
	{
	case EKind::Command:
		return MakeFulfilledPromise<FResult>(RunCommand(*Spec, Params)).GetFuture();
	case EKind::EditorScript:
		return MakeFulfilledPromise<FResult>(RunEditorScript(*Spec, Params)).GetFuture();
	case EKind::FrameStats:
	default:
		return RunFrameStats(*Spec, Params);
	}
}

FResult FUnrealMCPToolset::RunCommand(const FToolSpec& Spec, const TSharedRef<FJsonObject>& Params) const
{
	UEpicUnrealMCPBridge* Bridge = GetBridge();
	if (!Bridge)
	{
		return MakeError(TEXT("UnrealMCP bridge subsystem is not available"));
	}
	// Epic's registry calls tools on the game thread, where the bridge runs the handler inline; timeout_s only bounds
	// the wait of an off-thread caller, and the handlers see exactly what the TCP server would give them.
	if (!IsInGameThread() && !Params->HasField(TEXT("timeout_s")))
	{
		Params->SetNumberField(TEXT("timeout_s"), Spec.TimeoutS);
	}
	return FinishCommandReply(Bridge->ExecuteCommandJson(Spec.Target, Params), Spec.bOkFlag);
}

FResult FUnrealMCPToolset::RunEditorScript(const FToolSpec& Spec, const TSharedRef<FJsonObject>& Params) const
{
	UEpicUnrealMCPBridge* Bridge = GetBridge();
	if (!Bridge || !PythonWrapper.IsValid())
	{
		return MakeError(TEXT("UnrealMCP bridge subsystem is not available"));
	}
	// Same text unreal_mcp.editor_scripts.render() + python_exec.build_script() produce on the Python side.
	const FString Source = TEXT("ARGS = __import__('json').loads(") + PyStr(ToJson(Params)) + TEXT(")\n") + Spec.ScriptBody;
	const FString Marker = TEXT("<<MCP:") + FGuid::NewGuid().ToString(EGuidFormats::Digits).ToLower() + TEXT(">>");
	const FString Code = PythonWrapper->Replace(TEXT("__MCP_SRC__"), *PyStr(Source), ESearchCase::CaseSensitive)
		.Replace(TEXT("__MCP_MARK__"), *PyStr(Marker), ESearchCase::CaseSensitive)
		.Replace(TEXT("__MCP_GC__"), TEXT("False"), ESearchCase::CaseSensitive);

	TSharedRef<FJsonObject> PyParams = MakeShared<FJsonObject>();
	PyParams->SetStringField(TEXT("code"), Code);
	PyParams->SetStringField(TEXT("mode"), TEXT("file"));
	PyParams->SetNumberField(TEXT("timeout_s"), Spec.TimeoutS);
	const TSharedRef<FJsonObject> Reply = Bridge->ExecuteCommandJson(TEXT("execute_python"), PyParams);

	FString Status;
	Reply->TryGetStringField(TEXT("status"), Status);
	if (Status != TEXT("success"))
	{
		FString Error = TEXT("execute_python failed");
		Reply->TryGetStringField(TEXT("error"), Error);
		return MakeError(Error);
	}
	const TSharedPtr<FJsonObject>* PyResult = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Log = nullptr;
	if (!Reply->TryGetObjectField(TEXT("result"), PyResult) || !(*PyResult)->TryGetArrayField(TEXT("log"), Log))
	{
		return MakeError(TEXT("execute_python returned no log"));
	}

	TSharedPtr<FJsonObject> Parsed;
	TArray<TSharedPtr<FJsonValue>> LogErrors;
	for (const TSharedPtr<FJsonValue>& Entry : *Log)
	{
		const TSharedPtr<FJsonObject> E = Entry.IsValid() ? Entry->AsObject() : nullptr;
		if (!E.IsValid())
		{
			continue;
		}
		const FString Output = E->GetStringField(TEXT("output"));
		const int32 Idx = Output.Find(Marker, ESearchCase::CaseSensitive);
		if (Idx != INDEX_NONE)
		{
			if (!Parsed.IsValid())
			{
				Parsed = ParseObject(Output.RightChop(Idx + Marker.Len()).TrimStartAndEnd());
			}
		}
		else if (E->GetStringField(TEXT("type")) == TEXT("Error"))
		{
			LogErrors.Add(MakeShared<FJsonValueString>(Output));
		}
	}
	if (!Parsed.IsValid())
	{
		return MakeError(TEXT("script produced no result marker (editor Python crashed or timed out?)"));
	}
	bool bOk = false;
	Parsed->TryGetBoolField(TEXT("ok"), bOk);
	if (!bOk)
	{
		FString Traceback = TEXT("editor script failed");
		Parsed->TryGetStringField(TEXT("traceback"), Traceback);
		return MakeError(Traceback);
	}
	// run_editor_script: flatten the script's `result` dict; result["success"] = False is a failure it detected.
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	const TSharedPtr<FJsonObject>* ResultObj = nullptr;
	if (Parsed->TryGetObjectField(TEXT("result"), ResultObj) && ResultObj->IsValid())
	{
		Out = (*ResultObj).ToSharedRef();
		bool bSuccess = true;
		if (Out->TryGetBoolField(TEXT("success"), bSuccess) && !bSuccess)
		{
			return MakeError(ToJson(Out));
		}
	}
	else if (const TSharedPtr<FJsonValue> Raw = Parsed->TryGetField(TEXT("result")))
	{
		Out->SetField(TEXT("result"), Raw);
	}
	Out->SetBoolField(TEXT("success"), true);
	if (LogErrors.Num() > 0)
	{
		Out->SetArrayField(TEXT("log_errors"), LogErrors);
	}
	return MakeValue(ToJson(Out));
}

TFuture<TValueOrError<FString, FString>> FUnrealMCPToolset::RunFrameStats(const FToolSpec& Spec, const TSharedRef<FJsonObject>& Params) const
{
	UEpicUnrealMCPBridge* Bridge = GetBridge();
	if (!Bridge)
	{
		return MakeFulfilledPromise<FResult>(MakeError(TEXT("UnrealMCP bridge subsystem is not available"))).GetFuture();
	}
	int32 Frames = 120;
	Params->TryGetNumberField(TEXT("frames"), Frames);
	double TimeoutS = Spec.TimeoutS;
	Params->TryGetNumberField(TEXT("timeout_s"), TimeoutS);

	TSharedRef<FJsonObject> Start = MakeShared<FJsonObject>();
	Start->SetStringField(TEXT("action"), TEXT("start"));
	Start->SetNumberField(TEXT("frames"), Frames);
	FResult Started = FinishCommandReply(Bridge->ExecuteCommandJson(TEXT("frame_stats"), Start), false);
	if (Started.HasError())
	{
		return MakeFulfilledPromise<FResult>(MoveTemp(Started)).GetFuture();
	}

	// An async tool: the HTTP request stays open while the editor keeps ticking (the frames being measured).
	TSharedRef<TPromise<FResult>> Promise = MakeShared<TPromise<FResult>>();
	TFuture<FResult> Future = Promise->GetFuture();
	const double Deadline = FPlatformTime::Seconds() + TimeoutS;
	TWeakObjectPtr<UEpicUnrealMCPBridge> WeakBridge(Bridge);
	FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([Promise, Deadline, WeakBridge, Frames](float)
	{
		UEpicUnrealMCPBridge* B = WeakBridge.Get();
		if (!B)
		{
			Promise->SetValue(MakeError(TEXT("editor shutting down")));
			return false;
		}
		TSharedRef<FJsonObject> Read = MakeShared<FJsonObject>();
		Read->SetStringField(TEXT("action"), TEXT("read"));
		const TSharedRef<FJsonObject> Reply = B->ExecuteCommandJson(TEXT("frame_stats"), Read);
		FString Status;
		Reply->TryGetStringField(TEXT("status"), Status);
		const TSharedPtr<FJsonObject>* Result = nullptr;
		bool bDone = false;
		if (Status == TEXT("success") && Reply->TryGetObjectField(TEXT("result"), Result))
		{
			(*Result)->TryGetBoolField(TEXT("done"), bDone);
		}
		if (Status != TEXT("success") || bDone)
		{
			Promise->SetValue(FinishCommandReply(Reply, false));
			return false;
		}
		if (FPlatformTime::Seconds() >= Deadline)
		{
			TSharedRef<FJsonObject> Stop = MakeShared<FJsonObject>();
			Stop->SetStringField(TEXT("action"), TEXT("stop"));
			B->ExecuteCommandJson(TEXT("frame_stats"), Stop);
			Promise->SetValue(MakeError(FString::Printf(TEXT("only %d of %d frames before the timeout"),
				int32((*Result)->GetNumberField(TEXT("frames"))), Frames)));
			return false;
		}
		return true;
	}), 0.25f);
	return Future;
}
