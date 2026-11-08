#pragma once

#include "CoreMinimal.h"
#include "EditorSubsystem.h"
#include "Sockets.h"
#include "Json.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "MCPCommon.h"
#include "HAL/ThreadSafeBool.h"
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
