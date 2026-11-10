#include "EpicUnrealMCPBridge.h"
#include "MCPServerRunnable.h"
#include "Sockets.h"
#include "SocketSubsystem.h"
#include "HAL/RunnableThread.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformProcess.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "Async/Async.h"
#include "Async/Future.h"
#include "ScopedTransaction.h"
#include "Framework/Application/SlateApplication.h"
#include "UObject/GarbageCollection.h"
#include "UObject/UObjectGlobals.h"
#include "Misc/CoreMisc.h"
#include "Commands/EpicUnrealMCPEditorCommands.h"
#include "Commands/EpicUnrealMCPBlueprintCommands.h"
#include "Commands/EpicUnrealMCPBlueprintGraphCommands.h"
#include "Commands/EpicUnrealMCPPythonCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"
#include "Commands/MCPWorldCommands.h"
#include "Commands/EpicUnrealMCPObservationCommands.h"
#include "Commands/MCPAuthoringCommands.h"
#include "Commands/MCPPlayCommands.h"
#include "Commands/MCPLevelCommands.h"

DEFINE_LOG_CATEGORY(LogUnrealMCP);

static constexpr double MCP_DEFAULT_TIMEOUT_S = 120.0;
static constexpr double MCP_MAX_TIMEOUT_S = 3600.0;

UEpicUnrealMCPBridge::UEpicUnrealMCPBridge()
{
	EditorCommands = MakeShared<FEpicUnrealMCPEditorCommands>();
	BlueprintCommands = MakeShared<FEpicUnrealMCPBlueprintCommands>();
	BlueprintGraphCommands = MakeShared<FEpicUnrealMCPBlueprintGraphCommands>();
}

UEpicUnrealMCPBridge::~UEpicUnrealMCPBridge()
{
	EditorCommands.Reset();
	BlueprintCommands.Reset();
	BlueprintGraphCommands.Reset();
}

void UEpicUnrealMCPBridge::Initialize(FSubsystemCollectionBase& Collection)
{
	Super::Initialize(Collection);

	const FString PortEnv = FPlatformMisc::GetEnvironmentVariable(TEXT("UNREAL_MCP_PORT"));
	const FString BindEnv = FPlatformMisc::GetEnvironmentVariable(TEXT("UNREAL_MCP_BIND"));
	Token = FPlatformMisc::GetEnvironmentVariable(TEXT("UNREAL_MCP_TOKEN"));

	const int32 ParsedPort = PortEnv.IsEmpty() ? 55557 : FCString::Atoi(*PortEnv);
	if (ParsedPort <= 0 || ParsedPort > 65535)
	{
		UE_LOG(LogUnrealMCP, Error, TEXT("UnrealMCP: invalid UNREAL_MCP_PORT '%s'; using 55557"), *PortEnv);
		Port = 55557;
	}
	else
	{
		Port = uint16(ParsedPort);
	}
	// A failed parse would leave 0.0.0.0 (all interfaces): fall back to loopback instead.
	if (!FIPv4Address::Parse(BindEnv.IsEmpty() ? TEXT("127.0.0.1") : BindEnv, ServerAddress))
	{
		UE_LOG(LogUnrealMCP, Error, TEXT("UnrealMCP: UNREAL_MCP_BIND '%s' is not an IPv4 address; using 127.0.0.1"), *BindEnv);
		ServerAddress = FIPv4Address(127, 0, 0, 1);
	}

	if (!ServerAddress.IsLoopbackAddress() && Token.IsEmpty())
	{
		UE_LOG(LogUnrealMCP, Error,
			TEXT("UnrealMCP: refusing to bind %s without UNREAL_MCP_TOKEN (execute_python is remote code execution). Falling back to 127.0.0.1."),
			*ServerAddress.ToString());
		FIPv4Address::Parse(TEXT("127.0.0.1"), ServerAddress);
	}

	const FString WatchdogEnv = FPlatformMisc::GetEnvironmentVariable(TEXT("UNREAL_MCP_WATCHDOG_S"));
	if (!WatchdogEnv.IsEmpty() && FCString::Atod(*WatchdogEnv) > 0.0)
	{
		WatchdogS = FMath::Clamp(FCString::Atod(*WatchdogEnv), 2.0, 3600.0);
	}

	RegisterAllCommands();
	if (IsRunningCommandlet())
	{
		// Cook/package/automation commandlets load editor plugins too; they must not take the port
		// (an agent would end up talking to a cook process that exits mid-session).
		UE_LOG(LogUnrealMCP, Display, TEXT("UnrealMCP: commandlet run, server not started"));
		return;
	}
	StartServer();
}

void UEpicUnrealMCPBridge::Deinitialize()
{
	StopServer();
	FMCPPlayCommands::Shutdown();
	Super::Deinitialize();
}

void UEpicUnrealMCPBridge::RegisterAllCommands()
{
	FRegistry& R = Registry;

	R.Register(TEXT("ping"), [](const TSharedPtr<FJsonObject>&)
	{
		TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
		Out->SetStringField(TEXT("message"), TEXT("pong"));
		Out->SetStringField(TEXT("plugin_version"), UNREALMCP_PLUGIN_VERSION);
		return Out;
	}, false);

	R.Register(TEXT("list_commands"), [this](const TSharedPtr<FJsonObject>&)
	{
		TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
		TArray<TSharedPtr<FJsonValue>> Names;
		for (const FString& N : GetCommandNames())
		{
			Names.Add(MakeShared<FJsonValueString>(N));
		}
		Out->SetArrayField(TEXT("commands"), Names);
		Out->SetStringField(TEXT("plugin_version"), UNREALMCP_PLUGIN_VERSION);
		Out->SetBoolField(TEXT("python_available"), FEpicUnrealMCPPythonCommands::IsPythonAvailable());
		return Out;
	}, false);

	// Legacy command groups keep their own internal dispatch; route their names to them.
	auto Route = [&R](auto Group, std::initializer_list<const TCHAR*> Names, bool bTransactional)
	{
		for (const TCHAR* Name : Names)
		{
			const FString Cmd(Name);
			R.Register(Cmd, [Group, Cmd](const TSharedPtr<FJsonObject>& P) { return Group->HandleCommand(Cmd, P); }, bTransactional);
		}
	};
	Route(EditorCommands, { TEXT("spawn_actor"), TEXT("delete_actor"), TEXT("set_actor_transform"), TEXT("spawn_blueprint_actor") }, true);
	Route(EditorCommands, { TEXT("get_actors_in_level"), TEXT("find_actors_by_name") }, false);
	Route(BlueprintCommands, {
		TEXT("create_blueprint"), TEXT("add_component_to_blueprint"), TEXT("set_physics_properties"),
		TEXT("compile_blueprint"), TEXT("set_static_mesh_properties"), TEXT("set_mesh_material_color"),
		TEXT("get_available_materials"), TEXT("apply_material_to_actor"), TEXT("apply_material_to_blueprint"),
		TEXT("get_actor_material_info"), TEXT("get_blueprint_material_info"), TEXT("read_blueprint_content"),
		TEXT("analyze_blueprint_graph"), TEXT("get_blueprint_variable_details"), TEXT("get_blueprint_function_details") }, false);
	Route(BlueprintGraphCommands, {
		TEXT("add_blueprint_node"), TEXT("connect_nodes"), TEXT("create_variable"),
		TEXT("set_blueprint_variable_properties"), TEXT("add_event_node"), TEXT("delete_node"),
		TEXT("set_node_property"), TEXT("create_function"), TEXT("add_function_input"),
		TEXT("add_function_output"), TEXT("delete_function"), TEXT("rename_function") }, false);

	// Phase 1 groups.
	FEpicUnrealMCPPythonCommands::Register(R);
	FEpicUnrealMCPReflectionCommands::Register(R);
	FEpicUnrealMCPObservationCommands::Register(R);
	FMCPBlueprintAuthoring::Register(R);
	FMCPWidgetAuthoring::Register(R);
	FMCPBehaviorTreeAuthoring::Register(R);
	FMCPWorldCommands::Register(R);
	FMCPPlayCommands::Register(R);
	FMCPAnimAuthoring::Register(R);
	FMCPGameplayTagCommands::Register(R);
	FMCPDataTypeAuthoring::Register(R);
	FMCPAudioAuthoring::Register(R);
	FMCPNiagaraAuthoring::Register(R);
	FMCPLevelCommands::Register(R);

	UE_LOG(LogUnrealMCP, Display, TEXT("UnrealMCP: %d commands registered"), R.Commands.Num());
}

TArray<FString> UEpicUnrealMCPBridge::GetCommandNames() const
{
	TArray<FString> Names;
	Registry.Commands.GetKeys(Names);
	Names.Sort();
	return Names;
}

bool UEpicUnrealMCPBridge::CheckToken(const FString& Provided) const
{
	if (Token.IsEmpty())
	{
		return true;
	}
	// Constant-time compare.
	if (Provided.Len() != Token.Len())
	{
		return false;
	}
	uint32 Diff = 0;
	for (int32 i = 0; i < Token.Len(); ++i)
	{
		Diff |= uint32(Provided[i] ^ Token[i]);
	}
	return Diff == 0;
}

void UEpicUnrealMCPBridge::StartServer()
{
	if (bIsRunning)
	{
		return;
	}
	ISocketSubsystem* SocketSubsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
	if (!SocketSubsystem)
	{
		UE_LOG(LogUnrealMCP, Error, TEXT("UnrealMCP: no socket subsystem"));
		return;
	}
	FSocket* Raw = SocketSubsystem->CreateSocket(NAME_Stream, TEXT("UnrealMCPListener"), false);
	if (!Raw)
	{
		UE_LOG(LogUnrealMCP, Error, TEXT("UnrealMCP: failed to create listener socket"));
		return;
	}
	Raw->SetReuseAddr(true);
	Raw->SetNonBlocking(true);

	const FIPv4Endpoint Endpoint(ServerAddress, Port);
	if (!Raw->Bind(*Endpoint.ToInternetAddr()) || !Raw->Listen(8))
	{
		UE_LOG(LogUnrealMCP, Error, TEXT("UnrealMCP: cannot listen on %s (port in use?)"), *Endpoint.ToString());
		SocketSubsystem->DestroySocket(Raw);
		return;
	}

	// Sockets must be released through the subsystem, never plain delete.
	ListenerSocket = TSharedPtr<FSocket>(Raw, [](FSocket* S) { ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(S); });
	bStopping = false;
	LastTickTime = FPlatformTime::Seconds();
	LastServiceTime = LastTickTime.load();
	DrainHandle = FTSTicker::GetCoreTicker().AddTicker(
		FTickerDelegate::CreateUObject(this, &UEpicUnrealMCPBridge::DrainPending), 0.0f);
	ServerRunnable = new FMCPServerRunnable(this, ListenerSocket);
	ServerThread = FRunnableThread::Create(ServerRunnable, TEXT("UnrealMCPServerThread"), 0, TPri_Normal);
	if (!ServerThread)
	{
		UE_LOG(LogUnrealMCP, Error, TEXT("UnrealMCP: failed to create server thread"));
		delete ServerRunnable;
		ServerRunnable = nullptr;
		ListenerSocket.Reset();
		return;
	}
	bIsRunning = true;
	UE_LOG(LogUnrealMCP, Display, TEXT("UnrealMCP %s listening on %s (token %s)"),
		UNREALMCP_PLUGIN_VERSION, *Endpoint.ToString(), Token.IsEmpty() ? TEXT("off") : TEXT("on"));
}

void UEpicUnrealMCPBridge::StopServer()
{
	if (!bIsRunning)
	{
		return;
	}
	bIsRunning = false;
	bStopping = true; // before joining: client threads waiting on the (this) game thread must give up
	if (ServerThread)
	{
		ServerThread->Kill(true); // calls Stop() and waits
		delete ServerThread;
		ServerThread = nullptr;
	}
	delete ServerRunnable; // joins client threads
	ServerRunnable = nullptr;
	FTSTicker::GetCoreTicker().RemoveTicker(DrainHandle);
	DrainHandle.Reset();
	FailPending(TEXT("editor shutting down"));
	if (ListenerSocket.IsValid())
	{
		ListenerSocket->Close();
		ListenerSocket.Reset();
	}
	UE_LOG(LogUnrealMCP, Display, TEXT("UnrealMCP: server stopped"));
}

TSharedRef<FJsonObject> UEpicUnrealMCPBridge::RunOnGameThread(const FString& CommandType, const TSharedPtr<FJsonObject>& Params)
{
	TSharedRef<FJsonObject> Response = MakeShared<FJsonObject>();
	const FCommandEntry* Entry = Registry.Commands.Find(CommandType);
	if (!Entry)
	{
		Response->SetStringField(TEXT("status"), TEXT("error"));
		Response->SetStringField(TEXT("error"), FString::Printf(TEXT("Unknown command: %s"), *CommandType));
		return Response;
	}

	TSharedPtr<FJsonObject> Result;
	{
		TUniquePtr<FScopedTransaction> Transaction;
		if (Entry->bTransactional)
		{
			Transaction = MakeUnique<FScopedTransaction>(FText::FromString(FString::Printf(TEXT("MCP: %s"), *CommandType)));
		}
		// Always let the transaction commit, even on failure: Cancel() would drop the undo
		// record but keep any partial edits, making them impossible to undo.
		Result = Entry->Handler(Params);
	}

	if (!Result.IsValid())
	{
		Response->SetStringField(TEXT("status"), TEXT("error"));
		Response->SetStringField(TEXT("error"), FString::Printf(TEXT("%s returned no result"), *CommandType));
	}
	else if (Result->HasField(TEXT("success")) && !Result->GetBoolField(TEXT("success")))
	{
		FString Error = TEXT("Unknown error");
		if (!Result->TryGetStringField(TEXT("error"), Error))
		{
			Result->TryGetStringField(TEXT("message"), Error);
		}
		Response->SetStringField(TEXT("status"), TEXT("error"));
		Response->SetStringField(TEXT("error"), Error);
	}
	else
	{
		Response->SetStringField(TEXT("status"), TEXT("success"));
		Response->SetObjectField(TEXT("result"), Result);
	}
	return Response;
}

TSharedRef<FJsonObject> UEpicUnrealMCPBridge::ExecuteCommandJson(const FString& CommandType, const TSharedPtr<FJsonObject>& InParams)
{
	const TSharedPtr<FJsonObject> Params = InParams.IsValid() ? InParams : MakeShared<FJsonObject>();
	if (CommandType == TEXT("editor_health"))
	{
		// Never queued: it must answer while the game thread is stuck (that is what it is for).
		TSharedRef<FJsonObject> Reply = MakeShared<FJsonObject>();
		Reply->SetStringField(TEXT("status"), TEXT("success"));
		Reply->SetObjectField(TEXT("result"), HealthJson());
		return Reply;
	}
	if (IsInGameThread())
	{
		return RunOnGameThread(CommandType, Params);
	}

	double TimeoutS = MCP_DEFAULT_TIMEOUT_S;
	Params->TryGetNumberField(TEXT("timeout_s"), TimeoutS);
	TimeoutS = FMath::Clamp(TimeoutS + 5.0, 1.0, MCP_MAX_TIMEOUT_S);

	// TSharedPtr (not Ref) so the future's value type is default-constructible.
	TSharedRef<TPromise<TSharedPtr<FJsonObject>>, ESPMode::ThreadSafe> Promise =
		MakeShared<TPromise<TSharedPtr<FJsonObject>>, ESPMode::ThreadSafe>();
	TFuture<TSharedPtr<FJsonObject>> Future = Promise->GetFuture();

	FPendingCommand Cmd{ CommandType, Params, Promise };
	Cmd.Cancelled = MakeShared<std::atomic<bool>, ESPMode::ThreadSafe>(false);
	Cmd.Started = MakeShared<std::atomic<bool>, ESPMode::ThreadSafe>(false);
	Cmd.TimeoutS = TimeoutS;
	const TSharedPtr<std::atomic<bool>, ESPMode::ThreadSafe> Cancelled = Cmd.Cancelled;
	const TSharedPtr<std::atomic<bool>, ESPMode::ThreadSafe> Started = Cmd.Started;
	++QueueDepth;
	Pending.Enqueue(MoveTemp(Cmd));

	// Wait in slices so editor shutdown (which joins this thread from the game thread) never
	// blocks on a task that can only run once the game thread is free.
	const double Deadline = FPlatformTime::Seconds() + TimeoutS;
	bool bReady = false;
	FString Unresponsive;
	while (!(bReady = Future.WaitFor(FTimespan::FromMilliseconds(100))))
	{
		if (bStopping || FPlatformTime::Seconds() >= Deadline)
		{
			break;
		}
		// Watchdog: our command has not started and the game thread is not servicing the queue (hung engine,
		// modal dialog, or a command stuck past its own timeout). Fail fast instead of waiting out timeout_s.
		if (!Started->load())
		{
			Unresponsive = UnresponsiveReason();
			if (!Unresponsive.IsEmpty())
			{
				break;
			}
		}
	}
	if (!bReady && !Started->load())
	{
		Cancelled->store(true); // it never ran: make sure it never runs later either
	}
	if (!bReady && !bStopping && Unresponsive.IsEmpty())
	{
		Unresponsive = UnresponsiveReason();
	}
	if (!bReady && !bStopping && !Unresponsive.IsEmpty())
	{
		return UnresponsiveError(CommandType, Unresponsive);
	}
	if (!bReady && bStopping)
	{
		TSharedRef<FJsonObject> Stopping = MakeShared<FJsonObject>();
		Stopping->SetStringField(TEXT("status"), TEXT("error"));
		Stopping->SetStringField(TEXT("error"), TEXT("editor shutting down"));
		return Stopping;
	}
	if (!bReady)
	{
		// The task may still run later; its result is dropped. The client gets a clear error
		// instead of a hung socket (e.g. a modal dialog is blocking the game thread).
		TSharedRef<FJsonObject> TimedOut = MakeShared<FJsonObject>();
		TimedOut->SetStringField(TEXT("status"), TEXT("error"));
		TimedOut->SetStringField(TEXT("error"), FString::Printf(
			TEXT("%s timed out after %.0fs waiting for the game thread (a modal dialog, slow task or long compile is blocking the editor)"), *CommandType, TimeoutS));
		return TimedOut;
	}
	return Future.Get().ToSharedRef();
}

FString UEpicUnrealMCPBridge::ExecuteCommand(const FString& CommandType, const TSharedPtr<FJsonObject>& Params)
{
	FString Out;
	TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
	FJsonSerializer::Serialize(ExecuteCommandJson(CommandType, Params), Writer);
	return Out;
}


// ---------------------------------------------------------------------------------------------
// Safe-point dispatch
// ---------------------------------------------------------------------------------------------

const TCHAR* UEpicUnrealMCPBridge::BlockedBy()
{
	if (GIsSlowTask) return TEXT("a slow task (progress dialog)");
	if (IsGarbageCollecting()) return TEXT("garbage collection");
	if (UE::IsSavingPackage()) return TEXT("a package save");
	if (FSlateApplication::IsInitialized() && FSlateApplication::Get().GetActiveModalWindow().IsValid())
	{
		return TEXT("a modal dialog"); // a modal loop is pumping the ticker; editor state is mid-transition
	}
	return nullptr;
}

bool UEpicUnrealMCPBridge::IsSafePointForCommands()
{
	return BlockedBy() == nullptr;
}

bool UEpicUnrealMCPBridge::DrainPending(float /*DeltaTime*/)
{
	const double Now = FPlatformTime::Seconds();
	LastTickTime = Now;
	if (bDraining)
	{
		return true; // re-entered from a modal / slow task inside a running command
	}
	if (const TCHAR* Blocked = BlockedBy())
	{
		FScopeLock Lock(&WatchdogLock);
		LastBlockedBy = Blocked;
		return true; // keep ticking; try again next frame
	}
	LastServiceTime = Now;
	TGuardValue<bool> Guard(bDraining, true);
	// Commands run one at a time; a command may open its own modal/slow task, so re-check between them.
	FPendingCommand Cmd;
	while (IsSafePointForCommands() && Pending.Dequeue(Cmd))
	{
		--QueueDepth;
		if (Cmd.Cancelled.IsValid() && Cmd.Cancelled->load())
		{
			// The client already got an error (watchdog / timeout) before this started: never run it late.
			TSharedPtr<FJsonObject> Err = MakeShared<FJsonObject>();
			Err->SetStringField(TEXT("status"), TEXT("error"));
			Err->SetStringField(TEXT("error"), TEXT("cancelled: the client gave up before the command started"));
			Cmd.Promise->SetValue(Err);
			continue;
		}
		if (Cmd.Started.IsValid())
		{
			Cmd.Started->store(true);
		}
		{
			FScopeLock Lock(&WatchdogLock);
			BusyCommand = Cmd.Type;
		}
		BusyTimeoutS = Cmd.TimeoutS;
		BusySince = FPlatformTime::Seconds();
		TSharedPtr<FJsonObject> Result = RunOnGameThread(Cmd.Type, Cmd.Params);
		BusySince = 0.0;
		LastTickTime = FPlatformTime::Seconds();
		LastServiceTime = LastTickTime.load();
		{
			FScopeLock Lock(&WatchdogLock);
			BusyCommand.Reset();
		}
		Cmd.Promise->SetValue(Result);
	}
	return true;
}

FString UEpicUnrealMCPBridge::UnresponsiveReason() const
{
	const double Now = FPlatformTime::Seconds();
	const double TickAge = Now - LastTickTime.load();
	const double ServiceAge = Now - LastServiceTime.load();
	const double Busy = BusySince.load();
	FString Command, Blocked;
	{
		FScopeLock Lock(&WatchdogLock);
		Command = BusyCommand;
		Blocked = LastBlockedBy;
	}
	if (Busy > 0.0)
	{
		// A long command is normal (imports, compiles); only past its own timeout is the editor stuck in it.
		const double BusyFor = Now - Busy;
		if (BusyFor > BusyTimeoutS.load() + WatchdogS && TickAge > WatchdogS)
		{
			return FString::Printf(TEXT("the game thread has been stuck in '%s' for %.0fs, past its %.0fs timeout"),
				*Command, BusyFor, BusyTimeoutS.load());
		}
		return FString();
	}
	if (TickAge > WatchdogS)
	{
		return FString::Printf(TEXT("the game thread has not ticked for %.0fs (an engine hang, e.g. a render/RHI thread deadlock)"), TickAge);
	}
	if (ServiceAge > WatchdogS)
	{
		return FString::Printf(TEXT("the editor ticks but has been blocked by %s for %.0fs"),
			Blocked.IsEmpty() ? TEXT("a modal state") : *Blocked, ServiceAge);
	}
	return FString();
}

TSharedRef<FJsonObject> UEpicUnrealMCPBridge::HealthJson() const
{
	const double Now = FPlatformTime::Seconds();
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	const FString Reason = UnresponsiveReason();
	Out->SetBoolField(TEXT("responsive"), Reason.IsEmpty());
	if (!Reason.IsEmpty())
	{
		Out->SetStringField(TEXT("reason"), Reason);
	}
	auto Round = [](double X) { return FMath::RoundToDouble(X * 10.0) / 10.0; };
	Out->SetNumberField(TEXT("pid"), double(FPlatformProcess::GetCurrentProcessId()));
	Out->SetNumberField(TEXT("port"), double(Port));
	Out->SetStringField(TEXT("plugin_version"), UNREALMCP_PLUGIN_VERSION);
	Out->SetNumberField(TEXT("game_thread_tick_age_s"), Round(Now - LastTickTime.load()));
	Out->SetNumberField(TEXT("queue_service_age_s"), Round(Now - LastServiceTime.load()));
	Out->SetNumberField(TEXT("queued_commands"), double(FMath::Max(0, QueueDepth.load())));
	Out->SetNumberField(TEXT("watchdog_s"), WatchdogS);
	const double Busy = BusySince.load();
	FScopeLock Lock(&WatchdogLock);
	if (Busy > 0.0)
	{
		Out->SetStringField(TEXT("busy_command"), BusyCommand);
		Out->SetNumberField(TEXT("busy_for_s"), Round(Now - Busy));
		Out->SetNumberField(TEXT("busy_timeout_s"), BusyTimeoutS.load());
	}
	if (!LastBlockedBy.IsEmpty() && Now - LastServiceTime.load() > 1.0)
	{
		Out->SetStringField(TEXT("blocked_by"), LastBlockedBy);
	}
	return Out;
}

TSharedRef<FJsonObject> UEpicUnrealMCPBridge::UnresponsiveError(const FString& CommandType, const FString& Reason) const
{
	const uint32 Pid = FPlatformProcess::GetCurrentProcessId();
	TSharedRef<FJsonObject> Err = MakeShared<FJsonObject>();
	Err->SetStringField(TEXT("status"), TEXT("error"));
	Err->SetStringField(TEXT("error"), FString::Printf(TEXT("editor_unresponsive: %s was not run: %s"), *CommandType, *Reason));
	Err->SetBoolField(TEXT("editor_unresponsive"), true);
	Err->SetStringField(TEXT("hint"), FString::Printf(
		TEXT("The editor process (pid %u, port %d) is alive but not servicing MCP commands. Poll editor_health to see whether "
			 "it recovers. If it stays stuck (e.g. a Vulkan RHI-thread hang: 100%% CPU, frozen game thread), kill it with "
			 "UNREAL_MCP_PORT=%d scripts/ue_linux.sh kill (it only kills the editor that script started on this port) or kill -9 %u, "
			 "then relaunch. Changes since the last save are lost."), Pid, int32(Port), int32(Port), Pid));
	Err->SetObjectField(TEXT("health"), HealthJson());
	return Err;
}

void UEpicUnrealMCPBridge::FailPending(const TCHAR* Reason)
{
	FPendingCommand Cmd;
	while (Pending.Dequeue(Cmd))
	{
		--QueueDepth;
		TSharedPtr<FJsonObject> Err = MakeShared<FJsonObject>();
		Err->SetStringField(TEXT("status"), TEXT("error"));
		Err->SetStringField(TEXT("error"), Reason);
		Cmd.Promise->SetValue(Err);
	}
}
