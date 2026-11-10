#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "Dom/JsonObject.h"
#include "HAL/PlatformProcess.h"

class IWebSocket;

/** One row of the panel transcript. */
struct FMCPChatItem
{
	enum class EKind : uint8 { User, Assistant, Tool, Confirm, Notice, Error };

	EKind Kind = EKind::Notice;
	int32 Turn = 0;
	FString Text;          // user / assistant / notice / error text; confirm reason
	FString ToolId;
	FString ToolName;
	FString ArgsJson;      // pretty-printed arguments
	FString Result;        // tool result text (truncated by the runner)
	bool bDone = false;    // tool finished / confirm answered
	bool bOk = false;      // tool succeeded / confirm allowed
	bool bDenied = false;  // tool not run because the user declined
	bool bImage = false;   // the tool returned an image (sent to the model, not shown here)
	int32 Ms = 0;
	FString ConfirmId;
};

/**
 * The panel's model and transport: owns the WebSocket to the agent runner, optionally the runner process itself,
 * and the transcript. Lives in the module (not the widget), so the conversation survives closing the tab and the
 * agent_panel MCP command can drive it with no UI at all (offscreen editors, tests).
 */
class FMCPAgentClient : public TSharedFromThis<FMCPAgentClient>
{
public:
	enum class EState : uint8 { Disconnected, Starting, Connecting, Connected };

	static FMCPAgentClient& Get();
	static void Shutdown();

	// Runner process
	bool StartRunner(const FString& ExtraArgs, FString& OutError);
	void StopRunner();
	bool IsRunnerProcessRunning();
	/** Resolved Python/ directory, or empty with OutWhy explaining how to configure it. */
	static FString ResolvePythonDir(FString* OutWhy = nullptr);
	static FString TokenFilePath(int32 Port);

	// Connection
	bool Connect(FString& OutError);
	void Disconnect();
	EState GetState() const { return State; }
	bool IsConnected() const { return State == EState::Connected; }

	// Conversation
	bool SendUserMessage(const FString& Text, const FString& Provider, const FString& Model, const FString& Specialist,
		FString& OutError, const TSharedPtr<FJsonValue>& MockScript = nullptr);
	void StopTurn();
	void AnswerConfirm(const FString& ConfirmId, bool bAllow);
	void ResetConversation();
	TSharedPtr<FJsonObject> UndoLastTurn();
	void SetConfirmMode(const FString& Mode);

	const TArray<TSharedPtr<FMCPChatItem>>& GetItems() const { return Items; }
	FString GetStatusText() const;
	FString GetStatusState() const { return StatusState; }
	bool IsTurnRunning() const { return bTurnRunning; }
	int32 GetLastUndoSteps() const { return LastUndoSteps; }
	const TArray<TSharedPtr<FJsonObject>>& GetProviders() const { return Providers; }
	const TArray<FString>& GetSpecialists() const { return Specialists; }
	FString GetDefaultProvider() const { return DefaultProvider; }
	FString GetLastRunnerOutput() const;

	TSharedPtr<FJsonObject> StatusJson();
	TSharedPtr<FJsonObject> TranscriptJson(int32 Since) const;

	/** Context sent with each message: open level, PIE, selected actors, Content Browser selection and folder. */
	static TSharedPtr<FJsonObject> GatherEditorContext();

	DECLARE_MULTICAST_DELEGATE(FOnChanged);
	/** Items added or the connection/status changed (rebuild rows). */
	FOnChanged OnItemsChanged;

	~FMCPAgentClient();

private:
	void AddItem(const TSharedPtr<FMCPChatItem>& Item);
	void AddNotice(const FString& Text, bool bError = false);
	void HandleMessage(const FString& Message);
	void HandleEvent(const TSharedPtr<FJsonObject>& Event);
	bool SendJson(const TSharedPtr<FJsonObject>& Object);
	bool Tick(float DeltaTime);
	void DrainRunnerOutput();
	TSharedPtr<FMCPChatItem> FindTool(const FString& Id) const;

	EState State = EState::Disconnected;
	TSharedPtr<IWebSocket> Socket;
	FProcHandle RunnerProc;
	void* PipeRead = nullptr;
	void* PipeWrite = nullptr;
	TArray<FString> RunnerOutput;
	double StartDeadline = 0;
	FDateTime LaunchTime;
	FTSTicker::FDelegateHandle TickHandle;

	TArray<TSharedPtr<FMCPChatItem>> Items;
	TSharedPtr<FMCPChatItem> OpenAssistant;
	FString StatusState = TEXT("disconnected");
	FString StatusDetail;
	bool bTurnRunning = false;
	int32 CurrentTurn = 0;
	int32 LastUndoSteps = -1;
	TArray<TSharedPtr<FJsonObject>> Providers;
	TArray<FString> Specialists;
	FString DefaultProvider;
	FString RunnerVersion;
};
