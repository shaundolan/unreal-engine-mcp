#include "Agent/MCPAgentClient.h"

#include "Agent/MCPAgentSettings.h"
#include "Agent/MCPAgentTurns.h"
#include "AssetRegistry/AssetData.h"
#include "ContentBrowserModule.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Selection.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "IContentBrowserSingleton.h"
#include "IWebSocket.h"
#include "Interfaces/IPluginManager.h"
#include "MCPCommon.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Policies/CondensedJsonPrintPolicy.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "WebSocketsModule.h"

namespace
{
TSharedPtr<FMCPAgentClient> AgentClientGInstance;

FString AgentClientToJson(const TSharedPtr<FJsonObject>& Obj, bool bPretty)
{
	FString Out;
	if (bPretty)
	{
		TSharedRef<TJsonWriter<>> W = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Obj.ToSharedRef(), W);
	}
	else
	{
		TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> W =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out);
		FJsonSerializer::Serialize(Obj.ToSharedRef(), W);
	}
	return Out;
}

FString AgentClientValueToPrettyJson(const TSharedPtr<FJsonValue>& Value)
{
	if (!Value.IsValid())
	{
		return TEXT("{}");
	}
	if (Value->Type == EJson::Object)
	{
		return AgentClientToJson(Value->AsObject(), true);
	}
	FString Out;
	TSharedRef<TJsonWriter<>> W = TJsonWriterFactory<>::Create(&Out);
	FJsonSerializer::Serialize(Value, FString(), W);
	return Out;
}

int32 AgentClientEditorPort()
{
	const FString Env = FPlatformMisc::GetEnvironmentVariable(TEXT("UNREAL_MCP_PORT"));
	const int32 P = Env.IsEmpty() ? 55557 : FCString::Atoi(*Env);
	return (P > 0 && P < 65536) ? P : 55557;
}

FString AgentClientFindExecutable(const FString& Name)
{
	if (Name.IsEmpty())
	{
		return FString();
	}
	if (Name.Contains(TEXT("/")) || Name.Contains(TEXT("\\")))
	{
		return FPaths::FileExists(Name) ? Name : FString();
	}
#if PLATFORM_WINDOWS
	const TCHAR* Delim = TEXT(";");
	const TArray<FString> Suffixes = { TEXT(".exe"), TEXT("") };
#else
	const TCHAR* Delim = TEXT(":");
	const TArray<FString> Suffixes = { TEXT("") };
#endif
	TArray<FString> Dirs;
	FPlatformMisc::GetEnvironmentVariable(TEXT("PATH")).ParseIntoArray(Dirs, Delim, true);
	// GUI launches often miss user tool dirs; uv's installer and Homebrew put it here.
	Dirs.Add(FPaths::Combine(FPlatformProcess::UserHomeDir(), TEXT(".local/bin")));
	Dirs.Add(FPaths::Combine(FPlatformProcess::UserHomeDir(), TEXT(".cargo/bin")));
	Dirs.Add(TEXT("/home/linuxbrew/.linuxbrew/bin"));
	Dirs.Add(TEXT("/opt/homebrew/bin"));
	Dirs.Add(TEXT("/usr/local/bin"));
	for (const FString& Dir : Dirs)
	{
		for (const FString& Suffix : Suffixes)
		{
			const FString Candidate = FPaths::Combine(Dir, Name + Suffix);
			if (FPaths::FileExists(Candidate))
			{
				return Candidate;
			}
		}
	}
	return FString();
}

bool AgentClientIsBusyState(const FString& S)
{
	return S == TEXT("thinking") || S == TEXT("running_tool") || S == TEXT("waiting_confirm");
}
}

FMCPAgentClient& FMCPAgentClient::Get()
{
	if (!AgentClientGInstance.IsValid())
	{
		AgentClientGInstance = MakeShared<FMCPAgentClient>();
		AgentClientGInstance->TickHandle = FTSTicker::GetCoreTicker().AddTicker(
			FTickerDelegate::CreateRaw(AgentClientGInstance.Get(), &FMCPAgentClient::Tick), 0.25f);
	}
	return *AgentClientGInstance;
}

void FMCPAgentClient::Shutdown()
{
	if (AgentClientGInstance.IsValid())
	{
		AgentClientGInstance->StopRunner();
		AgentClientGInstance->Disconnect();
		FTSTicker::GetCoreTicker().RemoveTicker(AgentClientGInstance->TickHandle);
		AgentClientGInstance.Reset();
	}
}

FMCPAgentClient::~FMCPAgentClient()
{
	if (PipeRead || PipeWrite)
	{
		FPlatformProcess::ClosePipe(PipeRead, PipeWrite);
	}
}

// ------------------------------------------------------------------------------------------------- runner process
FString FMCPAgentClient::ResolvePythonDir(FString* OutWhy)
{
	auto Valid = [](const FString& Dir) { return !Dir.IsEmpty() && FPaths::FileExists(FPaths::Combine(Dir, TEXT("unreal_mcp/agent_runner.py"))); };
	const UMCPAgentSettings* S = GetDefault<UMCPAgentSettings>();
	if (!S->PythonDir.IsEmpty())
	{
		if (Valid(S->PythonDir))
		{
			return S->PythonDir;
		}
		if (OutWhy)
		{
			*OutWhy = FString::Printf(TEXT("Python dir '%s' (Editor Preferences > Plugins > UnrealMCP Agent) has no unreal_mcp/agent_runner.py"), *S->PythonDir);
		}
		return FString();
	}
	const FString Env = FPlatformMisc::GetEnvironmentVariable(TEXT("UNREAL_MCP_PYTHON_DIR"));
	if (Valid(Env))
	{
		return Env;
	}
	TArray<FString> Roots;
	if (TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UnrealMCP")))
	{
		Roots.Add(FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir()));
	}
	Roots.Add(FPaths::ConvertRelativePathToFull(FPaths::ProjectDir()));
	for (FString Dir : Roots)
	{
		for (int32 Up = 0; Up < 5 && !Dir.IsEmpty(); ++Up)
		{
			const FString Candidate = FPaths::Combine(Dir, TEXT("Python"));
			if (Valid(Candidate))
			{
				return Candidate;
			}
			Dir = FPaths::GetPath(Dir);
		}
	}
	if (OutWhy)
	{
		*OutWhy = TEXT("Agent runner not found. Set 'Python Dir' in Editor Preferences > Plugins > UnrealMCP Agent to the "
			"unreal-engine-mcp repo's Python/ folder (or set UNREAL_MCP_PYTHON_DIR), or start it yourself: "
			"cd Python && uv run python -m unreal_mcp.agent_runner");
	}
	return FString();
}

FString FMCPAgentClient::TokenFilePath(int32 Port)
{
	return FPaths::Combine(FPlatformProcess::UserHomeDir(), TEXT(".unreal_mcp"), FString::Printf(TEXT("agent_runner_%d.json"), Port));
}

bool FMCPAgentClient::IsRunnerProcessRunning()
{
	return RunnerProc.IsValid() && FPlatformProcess::IsProcRunning(RunnerProc);
}

bool FMCPAgentClient::StartRunner(const FString& ExtraArgs, FString& OutError)
{
	if (IsRunnerProcessRunning())
	{
		return true;
	}
	const UMCPAgentSettings* S = GetDefault<UMCPAgentSettings>();
	const FString PythonDir = ResolvePythonDir(&OutError);
	if (PythonDir.IsEmpty())
	{
		AddNotice(OutError, true);
		return false;
	}
	const FString Uv = AgentClientFindExecutable(S->UvExecutable);
	if (Uv.IsEmpty())
	{
		OutError = FString::Printf(TEXT("'%s' not found on PATH. Install uv (https://docs.astral.sh/uv/) or set 'Uv Executable' in Editor Preferences > Plugins > UnrealMCP Agent."), *S->UvExecutable);
		AddNotice(OutError, true);
		return false;
	}
	IFileManager::Get().Delete(*TokenFilePath(S->RunnerPort), false, true, true);
	const FString Provider = S->DefaultProvider.IsEmpty() ? TEXT("anthropic") : S->DefaultProvider;
	const FString Args = FString::Printf(
		TEXT("--directory \"%s\" run --quiet python -m unreal_mcp.agent_runner --port %d --unreal-port %d --provider %s --confirm %s %s %s"),
		*PythonDir, S->RunnerPort, AgentClientEditorPort(), *Provider, *UMCPAgentSettings::ConfirmModeName(S->ConfirmMode),
		*S->ExtraRunnerArgs, *ExtraArgs);
	if (PipeRead || PipeWrite)
	{
		FPlatformProcess::ClosePipe(PipeRead, PipeWrite);
		PipeRead = PipeWrite = nullptr;
	}
	FPlatformProcess::CreatePipe(PipeRead, PipeWrite);
	RunnerOutput.Reset();
	LaunchTime = FDateTime::UtcNow() - FTimespan::FromSeconds(2);
	RunnerProc = FPlatformProcess::CreateProc(*Uv, *Args, false, true, true, nullptr, 0, *PythonDir, PipeWrite, nullptr);
	if (!RunnerProc.IsValid())
	{
		OutError = FString::Printf(TEXT("could not start %s %s"), *Uv, *Args);
		AddNotice(OutError, true);
		return false;
	}
	State = EState::Starting;
	StartDeadline = FPlatformTime::Seconds() + 180.0;  // the first run may install the Python environment
	StatusState = TEXT("starting");
	StatusDetail = FString::Printf(TEXT("starting the agent runner on port %d"), S->RunnerPort);
	AddNotice(FString::Printf(TEXT("Starting the agent runner: uv %s"), *Args));
	return true;
}

void FMCPAgentClient::StopRunner()
{
	if (RunnerProc.IsValid())
	{
		if (FPlatformProcess::IsProcRunning(RunnerProc))
		{
			FPlatformProcess::TerminateProc(RunnerProc, true);
		}
		FPlatformProcess::CloseProc(RunnerProc);
		RunnerProc = FProcHandle();
		Disconnect();
		AddNotice(TEXT("Agent runner stopped."));
	}
}

void FMCPAgentClient::DrainRunnerOutput()
{
	if (!PipeRead)
	{
		return;
	}
	const FString Chunk = FPlatformProcess::ReadPipe(PipeRead);
	if (Chunk.IsEmpty())
	{
		return;
	}
	TArray<FString> Lines;
	Chunk.ParseIntoArrayLines(Lines, true);
	RunnerOutput.Append(Lines);
	if (RunnerOutput.Num() > 200)
	{
		RunnerOutput.RemoveAt(0, RunnerOutput.Num() - 200);
	}
	for (const FString& L : Lines)
	{
		UE_LOG(LogUnrealMCP, Verbose, TEXT("agent runner: %s"), *L);
	}
}

FString FMCPAgentClient::GetLastRunnerOutput() const
{
	const int32 First = FMath::Max(0, RunnerOutput.Num() - 20);
	return FString::Join(TArray<FString>(RunnerOutput.GetData() + First, RunnerOutput.Num() - First), TEXT("\n"));
}

bool FMCPAgentClient::Tick(float)
{
	DrainRunnerOutput();
	if (State == EState::Starting)
	{
		const UMCPAgentSettings* S = GetDefault<UMCPAgentSettings>();
		const FString TokenFile = TokenFilePath(S->RunnerPort);
		if (!IsRunnerProcessRunning())
		{
			State = EState::Disconnected;
			StatusState = TEXT("error");
			AddNotice(FString::Printf(TEXT("The agent runner exited during startup:\n%s"), *GetLastRunnerOutput()), true);
		}
		else if (IFileManager::Get().FileExists(*TokenFile) && IFileManager::Get().GetTimeStamp(*TokenFile) >= LaunchTime)
		{
			FString Err;
			if (!Connect(Err))
			{
				AddNotice(Err, true);
			}
		}
		else if (FPlatformTime::Seconds() > StartDeadline)
		{
			State = EState::Disconnected;
			StatusState = TEXT("error");
			AddNotice(FString::Printf(TEXT("The agent runner did not start listening in time:\n%s"), *GetLastRunnerOutput()), true);
		}
	}
	return true;
}

// ------------------------------------------------------------------------------------------------------ connection
bool FMCPAgentClient::Connect(FString& OutError)
{
	if (State == EState::Connected || State == EState::Connecting)
	{
		return true;
	}
	const int32 Port = GetDefault<UMCPAgentSettings>()->RunnerPort;
	FString Json;
	if (!FFileHelper::LoadFileToString(Json, *TokenFilePath(Port)))
	{
		OutError = FString::Printf(TEXT("No agent runner on port %d (%s is missing). Press 'Start runner', or run: "
			"cd Python && uv run python -m unreal_mcp.agent_runner --port %d"), Port, *TokenFilePath(Port), Port);
		State = EState::Disconnected;
		return false;
	}
	TSharedPtr<FJsonObject> Info;
	FString Token;
	if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Info) || !Info.IsValid() || !Info->TryGetStringField(TEXT("token"), Token))
	{
		OutError = TEXT("the agent runner's token file is unreadable");
		State = EState::Disconnected;
		return false;
	}
	FModuleManager::LoadModuleChecked<FWebSocketsModule>(TEXT("WebSockets"));
	const FString Url = FString::Printf(TEXT("ws://127.0.0.1:%d/?token=%s"), Port, *Token);
	Socket = FWebSocketsModule::Get().CreateWebSocket(Url, FString());
	Socket->SetTextMessageMemoryLimit(64ull * 1024 * 1024);
	TWeakPtr<FMCPAgentClient> Weak = AsShared();
	Socket->OnConnected().AddLambda([Weak]()
	{
		if (TSharedPtr<FMCPAgentClient> C = Weak.Pin())
		{
			C->State = EState::Connected;
			C->StatusState = TEXT("idle");
			C->StatusDetail.Reset();
			C->AddNotice(TEXT("Connected to the agent runner."));
		}
	});
	Socket->OnConnectionError().AddLambda([Weak](const FString& Error)
	{
		if (TSharedPtr<FMCPAgentClient> C = Weak.Pin())
		{
			C->State = EState::Disconnected;
			C->StatusState = TEXT("error");
			C->bTurnRunning = false;
			C->AddNotice(FString::Printf(TEXT("Could not connect to the agent runner: %s"), *Error), true);
		}
	});
	Socket->OnClosed().AddLambda([Weak](int32 Code, const FString& Reason, bool)
	{
		if (TSharedPtr<FMCPAgentClient> C = Weak.Pin())
		{
			if (C->State != EState::Disconnected)
			{
				C->State = EState::Disconnected;
				C->StatusState = TEXT("disconnected");
				C->bTurnRunning = false;
				C->AddNotice(FString::Printf(TEXT("Disconnected from the agent runner (%d %s)."), Code, *Reason));
			}
		}
	});
	Socket->OnMessage().AddLambda([Weak](const FString& Message)
	{
		if (TSharedPtr<FMCPAgentClient> C = Weak.Pin())
		{
			C->HandleMessage(Message);
		}
	});
	State = EState::Connecting;
	StatusState = TEXT("connecting");
	Socket->Connect();
	OnItemsChanged.Broadcast();
	return true;
}

void FMCPAgentClient::Disconnect()
{
	if (Socket.IsValid())
	{
		Socket->OnConnected().Clear();
		Socket->OnConnectionError().Clear();
		Socket->OnClosed().Clear();
		Socket->OnMessage().Clear();
		if (Socket->IsConnected())
		{
			Socket->Close();
		}
		Socket.Reset();
	}
	if (State != EState::Disconnected)
	{
		State = EState::Disconnected;
		StatusState = TEXT("disconnected");
		bTurnRunning = false;
		OnItemsChanged.Broadcast();
	}
}

bool FMCPAgentClient::SendJson(const TSharedPtr<FJsonObject>& Object)
{
	if (!Socket.IsValid() || State != EState::Connected)
	{
		return false;
	}
	Socket->Send(AgentClientToJson(Object, false));
	return true;
}

// ---------------------------------------------------------------------------------------------------- conversation
TSharedPtr<FJsonObject> FMCPAgentClient::GatherEditorContext()
{
	TSharedPtr<FJsonObject> Ctx = MakeShared<FJsonObject>();
	if (!GEditor)
	{
		return Ctx;
	}
	if (UWorld* World = GEditor->GetEditorWorldContext().World())
	{
		Ctx->SetStringField(TEXT("level"), World->GetOutermost()->GetName());
	}
	Ctx->SetBoolField(TEXT("pie"), GEditor->PlayWorld != nullptr);
	TArray<TSharedPtr<FJsonValue>> Actors;
	int32 Total = 0;
	for (FSelectionIterator It(GEditor->GetSelectedActorIterator()); It; ++It)
	{
		if (const AActor* A = Cast<AActor>(*It))
		{
			if (++Total > 100)
			{
				continue;
			}
			TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("label"), A->GetActorLabel());
			O->SetStringField(TEXT("class"), A->GetClass()->GetName());
			Actors.Add(MakeShared<FJsonValueObject>(O));
		}
	}
	Ctx->SetArrayField(TEXT("selected_actors"), Actors);
	Ctx->SetNumberField(TEXT("selected_actor_count"), Total);
	if (FModuleManager::Get().IsModuleLoaded(TEXT("ContentBrowser")))
	{
		IContentBrowserSingleton& CB = FModuleManager::GetModuleChecked<FContentBrowserModule>(TEXT("ContentBrowser")).Get();
		TArray<FAssetData> Assets;
		CB.GetSelectedAssets(Assets);
		TArray<TSharedPtr<FJsonValue>> AssetJson;
		for (int32 i = 0; i < Assets.Num() && i < 100; ++i)
		{
			TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
			O->SetStringField(TEXT("path"), Assets[i].PackageName.ToString());
			O->SetStringField(TEXT("class"), Assets[i].AssetClassPath.GetAssetName().ToString());
			AssetJson.Add(MakeShared<FJsonValueObject>(O));
		}
		Ctx->SetArrayField(TEXT("selected_assets"), AssetJson);
		Ctx->SetStringField(TEXT("content_browser_path"), CB.GetCurrentPath().GetVirtualPathString());
	}
	return Ctx;
}

bool FMCPAgentClient::SendUserMessage(const FString& Text, const FString& Provider, const FString& Model,
	const FString& Specialist, FString& OutError, const TSharedPtr<FJsonValue>& MockScript)
{
	if (State != EState::Connected)
	{
		OutError = TEXT("not connected to the agent runner");
		return false;
	}
	if (bTurnRunning)
	{
		OutError = TEXT("the agent is still working; press Stop first");
		return false;
	}
	if (Text.TrimStartAndEnd().IsEmpty())
	{
		OutError = TEXT("empty message");
		return false;
	}
	TSharedPtr<FJsonObject> Msg = MakeShared<FJsonObject>();
	Msg->SetStringField(TEXT("type"), TEXT("user_message"));
	Msg->SetStringField(TEXT("text"), Text);
	Msg->SetStringField(TEXT("provider"), Provider);
	Msg->SetStringField(TEXT("model"), Model);
	Msg->SetStringField(TEXT("specialist"), Specialist.IsEmpty() ? TEXT("general") : Specialist);
	if (GetDefault<UMCPAgentSettings>()->bSendEditorContext)
	{
		Msg->SetObjectField(TEXT("context"), GatherEditorContext());
	}
	if (MockScript.IsValid())
	{
		Msg->SetField(TEXT("mock_script"), MockScript);
	}
	TSharedPtr<FMCPChatItem> Item = MakeShared<FMCPChatItem>();
	Item->Kind = FMCPChatItem::EKind::User;
	Item->Text = Text;
	Item->Turn = CurrentTurn + 1;
	AddItem(Item);
	bTurnRunning = true;
	StatusState = TEXT("thinking");
	return SendJson(Msg);
}

void FMCPAgentClient::StopTurn()
{
	TSharedPtr<FJsonObject> Msg = MakeShared<FJsonObject>();
	Msg->SetStringField(TEXT("type"), TEXT("stop"));
	SendJson(Msg);
}

void FMCPAgentClient::AnswerConfirm(const FString& ConfirmId, bool bAllow)
{
	for (const TSharedPtr<FMCPChatItem>& I : Items)
	{
		if (I->Kind == FMCPChatItem::EKind::Confirm && I->ConfirmId == ConfirmId && !I->bDone)
		{
			I->bDone = true;
			I->bOk = bAllow;
		}
	}
	TSharedPtr<FJsonObject> Msg = MakeShared<FJsonObject>();
	Msg->SetStringField(TEXT("type"), TEXT("confirm"));
	Msg->SetStringField(TEXT("id"), ConfirmId);
	Msg->SetBoolField(TEXT("allow"), bAllow);
	SendJson(Msg);
	OnItemsChanged.Broadcast();
}

void FMCPAgentClient::ResetConversation()
{
	TSharedPtr<FJsonObject> Msg = MakeShared<FJsonObject>();
	Msg->SetStringField(TEXT("type"), TEXT("reset"));
	SendJson(Msg);
	Items.Reset();
	OpenAssistant.Reset();
	LastUndoSteps = -1;
	OnItemsChanged.Broadcast();
}

void FMCPAgentClient::SetConfirmMode(const FString& Mode)
{
	TSharedPtr<FJsonObject> Msg = MakeShared<FJsonObject>();
	Msg->SetStringField(TEXT("type"), TEXT("config"));
	Msg->SetStringField(TEXT("confirm_mode"), Mode);
	SendJson(Msg);
}

TSharedPtr<FJsonObject> FMCPAgentClient::UndoLastTurn()
{
	if (bTurnRunning)
	{
		return MCPError(TEXT("the agent is still working; press Stop first"));
	}
	TSharedPtr<FJsonObject> R = FMCPAgentTurns::Get().UndoLast();
	bool bOk = false;
	R->TryGetBoolField(TEXT("success"), bOk);
	FString Err;
	R->TryGetStringField(TEXT("error"), Err);
	AddNotice(bOk ? FString::Printf(TEXT("Undid agent turn %d (%d steps)."), FMCPAgentTurns::Get().LastTurn(), (int32)R->GetNumberField(TEXT("undone")))
		: FString::Printf(TEXT("Can't undo the last agent turn: %s"), *Err), !bOk);
	if (bOk)
	{
		LastUndoSteps = 0;
	}
	return R;
}

// ---------------------------------------------------------------------------------------------------------- events
void FMCPAgentClient::AddItem(const TSharedPtr<FMCPChatItem>& Item)
{
	Items.Add(Item);
	OnItemsChanged.Broadcast();
}

void FMCPAgentClient::AddNotice(const FString& Text, bool bError)
{
	TSharedPtr<FMCPChatItem> Item = MakeShared<FMCPChatItem>();
	Item->Kind = bError ? FMCPChatItem::EKind::Error : FMCPChatItem::EKind::Notice;
	Item->Text = Text;
	Item->Turn = CurrentTurn;
	OpenAssistant.Reset();
	AddItem(Item);
	if (bError)
	{
		UE_LOG(LogUnrealMCP, Warning, TEXT("UnrealMCP Agent: %s"), *Text);
	}
}

TSharedPtr<FMCPChatItem> FMCPAgentClient::FindTool(const FString& Id) const
{
	for (int32 i = Items.Num() - 1; i >= 0; --i)
	{
		if (Items[i]->Kind == FMCPChatItem::EKind::Tool && Items[i]->ToolId == Id && !Items[i]->bDone)
		{
			return Items[i];
		}
	}
	return nullptr;
}

void FMCPAgentClient::HandleMessage(const FString& Message)
{
	TSharedPtr<FJsonObject> Event;
	if (FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Message), Event) && Event.IsValid())
	{
		HandleEvent(Event);
	}
}

void FMCPAgentClient::HandleEvent(const TSharedPtr<FJsonObject>& E)
{
	const FString Type = E->GetStringField(TEXT("type"));
	int32 Turn = CurrentTurn;
	E->TryGetNumberField(TEXT("turn"), Turn);

	if (Type == TEXT("hello"))
	{
		Providers.Reset();
		const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr;
		if (E->TryGetArrayField(TEXT("providers"), Arr))
		{
			for (const TSharedPtr<FJsonValue>& V : *Arr)
			{
				Providers.Add(V->AsObject());
			}
		}
		Specialists.Reset();
		if (E->TryGetArrayField(TEXT("specialists"), Arr))
		{
			for (const TSharedPtr<FJsonValue>& V : *Arr)
			{
				Specialists.Add(V->AsObject()->GetStringField(TEXT("id")));
			}
		}
		E->TryGetStringField(TEXT("default_provider"), DefaultProvider);
		E->TryGetStringField(TEXT("version"), RunnerVersion);
		SetConfirmMode(UMCPAgentSettings::ConfirmModeName(GetDefault<UMCPAgentSettings>()->ConfirmMode));
		OnItemsChanged.Broadcast();
	}
	else if (Type == TEXT("status"))
	{
		StatusState = E->GetStringField(TEXT("state"));
		E->TryGetStringField(TEXT("detail"), StatusDetail);
		if (StatusState == TEXT("done") || StatusState == TEXT("error") || StatusState == TEXT("idle"))
		{
			bTurnRunning = false;
		}
		else if (AgentClientIsBusyState(StatusState))
		{
			bTurnRunning = true;
		}
	}
	else if (Type == TEXT("turn_start"))
	{
		CurrentTurn = Turn;
		bTurnRunning = true;
		OpenAssistant.Reset();
	}
	else if (Type == TEXT("text_delta"))
	{
		if (!OpenAssistant.IsValid())
		{
			OpenAssistant = MakeShared<FMCPChatItem>();
			OpenAssistant->Kind = FMCPChatItem::EKind::Assistant;
			OpenAssistant->Turn = Turn;
			AddItem(OpenAssistant);
		}
		OpenAssistant->Text += E->GetStringField(TEXT("text"));
	}
	else if (Type == TEXT("assistant_text"))
	{
		OpenAssistant.Reset();
	}
	else if (Type == TEXT("tool_call"))
	{
		OpenAssistant.Reset();
		TSharedPtr<FMCPChatItem> Item = MakeShared<FMCPChatItem>();
		Item->Kind = FMCPChatItem::EKind::Tool;
		Item->Turn = Turn;
		E->TryGetStringField(TEXT("id"), Item->ToolId);
		Item->ToolName = E->GetStringField(TEXT("name"));
		Item->ArgsJson = AgentClientValueToPrettyJson(E->TryGetField(TEXT("args")));
		AddItem(Item);
	}
	else if (Type == TEXT("tool_result"))
	{
		FString Id;
		E->TryGetStringField(TEXT("id"), Id);
		TSharedPtr<FMCPChatItem> Item = FindTool(Id);
		if (!Item.IsValid())
		{
			Item = MakeShared<FMCPChatItem>();
			Item->Kind = FMCPChatItem::EKind::Tool;
			Item->Turn = Turn;
			Item->ToolId = Id;
			E->TryGetStringField(TEXT("name"), Item->ToolName);
			Items.Add(Item);
		}
		Item->bDone = true;
		E->TryGetBoolField(TEXT("ok"), Item->bOk);
		E->TryGetBoolField(TEXT("denied"), Item->bDenied);
		E->TryGetBoolField(TEXT("image"), Item->bImage);
		E->TryGetStringField(TEXT("result"), Item->Result);
		E->TryGetNumberField(TEXT("ms"), Item->Ms);
		OnItemsChanged.Broadcast();
	}
	else if (Type == TEXT("confirm_request"))
	{
		OpenAssistant.Reset();
		TSharedPtr<FMCPChatItem> Item = MakeShared<FMCPChatItem>();
		Item->Kind = FMCPChatItem::EKind::Confirm;
		Item->Turn = Turn;
		Item->ConfirmId = E->GetStringField(TEXT("id"));
		Item->ToolName = E->GetStringField(TEXT("tool"));
		Item->Text = E->GetStringField(TEXT("reason"));
		Item->ArgsJson = AgentClientValueToPrettyJson(E->TryGetField(TEXT("args")));
		AddItem(Item);
	}
	else if (Type == TEXT("notice"))
	{
		AddNotice(E->GetStringField(TEXT("text")));
	}
	else if (Type == TEXT("error"))
	{
		AddNotice(E->GetStringField(TEXT("message")), true);
	}
	else if (Type == TEXT("turn_end"))
	{
		bTurnRunning = false;
		OpenAssistant.Reset();
		double Steps = -1;
		LastUndoSteps = E->TryGetNumberField(TEXT("undo_steps"), Steps) ? (int32)Steps : -1;
		for (const TSharedPtr<FMCPChatItem>& I : Items)
		{
			if (I->Kind == FMCPChatItem::EKind::Confirm && !I->bDone)
			{
				I->bDone = true;  // the runner gave up waiting (stop or timeout)
			}
		}
		if (LastUndoSteps > 0)
		{
			AddNotice(FString::Printf(TEXT("Turn %d: %d undoable change(s). 'Undo last agent turn' reverts them."), Turn, LastUndoSteps));
		}
		OnItemsChanged.Broadcast();
	}
}

// ---------------------------------------------------------------------------------------------------------- status
FString FMCPAgentClient::GetStatusText() const
{
	switch (State)
	{
	case EState::Disconnected:
		return StatusState == TEXT("error") ? TEXT("Not connected (see the last error below)") : TEXT("Not connected");
	case EState::Starting:
		return TEXT("Starting the agent runner...");
	case EState::Connecting:
		return TEXT("Connecting...");
	default:
		break;
	}
	if (StatusState == TEXT("thinking"))
	{
		return TEXT("Thinking...");
	}
	if (StatusState == TEXT("running_tool"))
	{
		return FString::Printf(TEXT("Running tool: %s"), *StatusDetail);
	}
	if (StatusState == TEXT("waiting_confirm"))
	{
		return TEXT("Waiting for your confirmation");
	}
	if (StatusState == TEXT("done"))
	{
		return TEXT("Done");
	}
	if (StatusState == TEXT("error"))
	{
		return TEXT("Done with errors");
	}
	return FString::Printf(TEXT("Connected (runner %s)"), *RunnerVersion);
}

TSharedPtr<FJsonObject> FMCPAgentClient::StatusJson()
{
	static const TCHAR* StateNames[] = { TEXT("disconnected"), TEXT("starting"), TEXT("connecting"), TEXT("connected") };
	const UMCPAgentSettings* S = GetDefault<UMCPAgentSettings>();
	TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
	O->SetBoolField(TEXT("success"), true);
	O->SetStringField(TEXT("connection"), StateNames[(int32)State]);
	O->SetStringField(TEXT("status"), StatusState);
	O->SetStringField(TEXT("status_text"), GetStatusText());
	O->SetBoolField(TEXT("turn_running"), bTurnRunning);
	O->SetBoolField(TEXT("runner_process_running"), IsRunnerProcessRunning());
	O->SetNumberField(TEXT("runner_port"), S->RunnerPort);
	O->SetBoolField(TEXT("token_file_exists"), FPaths::FileExists(TokenFilePath(S->RunnerPort)));
	FString Why;
	O->SetStringField(TEXT("python_dir"), ResolvePythonDir(&Why));
	O->SetStringField(TEXT("python_dir_problem"), Why);
	O->SetNumberField(TEXT("items"), Items.Num());
	O->SetNumberField(TEXT("turn"), CurrentTurn);
	O->SetNumberField(TEXT("last_undo_steps"), LastUndoSteps);
	TArray<TSharedPtr<FJsonValue>> Provs;
	for (const TSharedPtr<FJsonObject>& P : Providers)
	{
		Provs.Add(MakeShared<FJsonValueObject>(P));
	}
	O->SetArrayField(TEXT("providers"), Provs);
	TArray<TSharedPtr<FJsonValue>> Specs;
	for (const FString& Sp : Specialists)
	{
		Specs.Add(MakeShared<FJsonValueString>(Sp));
	}
	O->SetArrayField(TEXT("specialists"), Specs);
	O->SetStringField(TEXT("runner_output_tail"), GetLastRunnerOutput());
	O->SetObjectField(TEXT("undo"), FMCPAgentTurns::Get().Status());
	return O;
}

TSharedPtr<FJsonObject> FMCPAgentClient::TranscriptJson(int32 Since) const
{
	static const TCHAR* Kinds[] = { TEXT("user"), TEXT("assistant"), TEXT("tool"), TEXT("confirm"), TEXT("notice"), TEXT("error") };
	TArray<TSharedPtr<FJsonValue>> Arr;
	for (int32 i = FMath::Max(0, Since); i < Items.Num(); ++i)
	{
		const FMCPChatItem& I = *Items[i];
		TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
		O->SetNumberField(TEXT("index"), i);
		O->SetStringField(TEXT("kind"), Kinds[(int32)I.Kind]);
		O->SetNumberField(TEXT("turn"), I.Turn);
		if (!I.Text.IsEmpty())
		{
			O->SetStringField(TEXT("text"), I.Text);
		}
		if (I.Kind == FMCPChatItem::EKind::Tool || I.Kind == FMCPChatItem::EKind::Confirm)
		{
			O->SetStringField(TEXT("tool"), I.ToolName);
			O->SetStringField(TEXT("args"), I.ArgsJson);
			O->SetBoolField(TEXT("done"), I.bDone);
			O->SetBoolField(TEXT("ok"), I.bOk);
		}
		if (I.Kind == FMCPChatItem::EKind::Tool)
		{
			O->SetStringField(TEXT("result"), I.Result.Left(2000));
			O->SetBoolField(TEXT("denied"), I.bDenied);
			O->SetNumberField(TEXT("ms"), I.Ms);
		}
		if (I.Kind == FMCPChatItem::EKind::Confirm)
		{
			O->SetStringField(TEXT("confirm_id"), I.ConfirmId);
		}
		Arr.Add(MakeShared<FJsonValueObject>(O));
	}
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("success"), true);
	Out->SetArrayField(TEXT("items"), Arr);
	Out->SetNumberField(TEXT("next"), Items.Num());
	Out->SetBoolField(TEXT("turn_running"), bTurnRunning);
	return Out;
}
