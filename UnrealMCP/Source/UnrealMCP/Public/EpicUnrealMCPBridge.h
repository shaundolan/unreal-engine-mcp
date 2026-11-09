#pragma once

#include "CoreMinimal.h"
#include "EditorSubsystem.h"
#include "Sockets.h"
#include "Json.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "MCPCommon.h"
#include "HAL/ThreadSafeBool.h"
#include "Containers/Queue.h"
#include "Containers/Ticker.h"
#include "Async/Future.h"
#include "HAL/CriticalSection.h"
#include <atomic>
#include "Commands/EpicUnrealMCPEditorCommands.h"
#include "Commands/EpicUnrealMCPBlueprintCommands.h"
#include "Commands/EpicUnrealMCPBlueprintGraphCommands.h"
#include "EpicUnrealMCPBridge.generated.h"

class FMCPServerRunnable;
class FRunnableThread;

/**
 * Editor subsystem hosting the UnrealMCP TCP server.
 *
 * Environment (read at editor start):
 *   UNREAL_MCP_PORT   listen port (default 55557)
 *   UNREAL_MCP_BIND   bind address (default 127.0.0.1). Non-loopback requires UNREAL_MCP_TOKEN.
 *   UNREAL_MCP_TOKEN  shared secret every request must carry as "token"
 *   UNREAL_MCP_WATCHDOG_S  seconds the game thread may stop servicing the command queue (outside a running command)
 *                     before queued commands fail fast with "editor_unresponsive" (default 60)
 *
 * Commands live in a name -> handler registry; see RegisterAllCommands().
 */
UCLASS()
class UNREALMCP_API UEpicUnrealMCPBridge : public UEditorSubsystem
{
	GENERATED_BODY()

public:
	UEpicUnrealMCPBridge();
	virtual ~UEpicUnrealMCPBridge();

	virtual void Initialize(FSubsystemCollectionBase& Collection) override;
	virtual void Deinitialize() override;

	void StartServer();
	void StopServer();
	bool IsRunning() const { return bIsRunning; }

	/** Thread-safe. Runs the command on the game thread and waits (bounded by timeout_s). */
	TSharedRef<FJsonObject> ExecuteCommandJson(const FString& CommandType, const TSharedPtr<FJsonObject>& Params);

	/** Legacy string form, kept for source compatibility. */
	FString ExecuteCommand(const FString& CommandType, const TSharedPtr<FJsonObject>& Params);

	bool CheckToken(const FString& Provided) const;
	TArray<FString> GetCommandNames() const;

	/** editor_health: answered on the calling (socket) thread, so it works while the game thread is hung. */
	TSharedRef<FJsonObject> HealthJson() const;
	/** Empty while the game thread is servicing commands; otherwise why it is not (for editor_unresponsive). */
	FString UnresponsiveReason() const;

private:
	struct FCommandEntry
	{
		FMCPCommandHandler Handler;
		bool bTransactional = false;
	};

	class FRegistry : public IMCPCommandRegistry
	{
	public:
		TMap<FString, FCommandEntry> Commands;
		virtual void Register(const FString& Name, FMCPCommandHandler Handler, bool bTransactional) override
		{
			Commands.Add(Name, FCommandEntry{ MoveTemp(Handler), bTransactional });
		}
	};

	void RegisterAllCommands();
	TSharedRef<FJsonObject> RunOnGameThread(const FString& CommandType, const TSharedPtr<FJsonObject>& Params);

	/**
	 * Commands from client threads are queued and executed ONLY from the core ticker at a safe point:
	 * never re-entrantly from task-graph pumping inside a modal / slow task / package save (running
	 * editor code there crashed Slate: FindWindowByPlatformWindow during a validation slow task).
	 */
	using FPromisePtr = TSharedPtr<TPromise<TSharedPtr<FJsonObject>>, ESPMode::ThreadSafe>;
	struct FPendingCommand
	{
		FString Type;
		TSharedPtr<FJsonObject> Params;
		FPromisePtr Promise;
		/** Set by the waiting client thread when it gave up before the command started: it is then never run. */
		TSharedPtr<std::atomic<bool>, ESPMode::ThreadSafe> Cancelled;
		TSharedPtr<std::atomic<bool>, ESPMode::ThreadSafe> Started;
		double TimeoutS = 0;
	};
	TQueue<FPendingCommand, EQueueMode::Mpsc> Pending;
	FTSTicker::FDelegateHandle DrainHandle;
	bool bDraining = false;
	bool DrainPending(float DeltaTime);
	void FailPending(const TCHAR* Reason);
	static bool IsSafePointForCommands();
	/** nullptr at a safe point, else what blocks command dispatch (modal dialog, slow task, GC, package save). */
	static const TCHAR* BlockedBy();
	TSharedRef<FJsonObject> UnresponsiveError(const FString& CommandType, const FString& Reason) const;

	// Watchdog state: written on the game thread, read from socket threads. Times are FPlatformTime::Seconds().
	std::atomic<double> LastTickTime{ 0.0 };     // DrainPending ran (the game thread / a modal loop is ticking)
	std::atomic<double> LastServiceTime{ 0.0 };  // DrainPending was at a safe point and could run commands
	std::atomic<double> BusySince{ 0.0 };        // > 0 while a queued command runs on the game thread
	std::atomic<double> BusyTimeoutS{ 0.0 };     // that command's timeout_s
	std::atomic<int32> QueueDepth{ 0 };
	mutable FCriticalSection WatchdogLock;
	FString BusyCommand;                         // guarded by WatchdogLock
	FString LastBlockedBy;                       // guarded by WatchdogLock
	double WatchdogS = 60.0;

	bool bIsRunning = false;
	/** Set first thing in StopServer so waiting client threads bail out instead of blocking shutdown. */
	FThreadSafeBool bStopping = false;
	TSharedPtr<FSocket> ListenerSocket;
	FMCPServerRunnable* ServerRunnable = nullptr;
	FRunnableThread* ServerThread = nullptr;

	FIPv4Address ServerAddress;
	uint16 Port = 55557;
	FString Token;

	FRegistry Registry;

	TSharedPtr<FEpicUnrealMCPEditorCommands> EditorCommands;
	TSharedPtr<FEpicUnrealMCPBlueprintCommands> BlueprintCommands;
	TSharedPtr<FEpicUnrealMCPBlueprintGraphCommands> BlueprintGraphCommands;
};
