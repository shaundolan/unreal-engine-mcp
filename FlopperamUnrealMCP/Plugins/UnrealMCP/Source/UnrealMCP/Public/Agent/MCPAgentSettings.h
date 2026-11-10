#pragma once

#include "CoreMinimal.h"
#include "Engine/DeveloperSettings.h"
#include "MCPAgentSettings.generated.h"

UENUM()
enum class EMCPAgentConfirmMode : uint8
{
	/** Never ask. */
	Off,
	/** Ask before calls that delete assets, overwrite files or discard unsaved work (default). */
	Destructive,
	/** Also ask before undoable actor deletes, map switches and saves. */
	Strict,
	/** Ask before every call that changes something. */
	All,
};

/**
 * Editor Preferences > Plugins > UnrealMCP Agent. Per user, per project (EditorPerProjectUserSettings).
 * The panel talks to a local Python agent runner (Python/unreal_mcp/agent_runner.py) over a localhost WebSocket.
 */
UCLASS(config = EditorPerProjectUserSettings, meta = (DisplayName = "UnrealMCP Agent"))
class UNREALMCP_API UMCPAgentSettings : public UDeveloperSettings
{
	GENERATED_BODY()

public:
	virtual FName GetContainerName() const override { return TEXT("Editor"); }
	virtual FName GetCategoryName() const override { return TEXT("Plugins"); }

	/** Port of the agent runner's WebSocket (127.0.0.1 only). */
	UPROPERTY(Config, EditAnywhere, Category = "Runner", meta = (ClampMin = 1024, ClampMax = 65535))
	int32 RunnerPort = 55600;

	/** The repo's Python/ directory (contains unreal_mcp/agent_runner.py). Empty: UNREAL_MCP_PYTHON_DIR, then a search upward from the plugin. */
	UPROPERTY(Config, EditAnywhere, Category = "Runner")
	FString PythonDir;

	/** uv executable used to start the runner (name on PATH or full path). */
	UPROPERTY(Config, EditAnywhere, Category = "Runner")
	FString UvExecutable = TEXT("uv");

	/** Extra command-line arguments for the runner, e.g. "--native-mcp" or "--mock-script /path/script.json". */
	UPROPERTY(Config, EditAnywhere, Category = "Runner")
	FString ExtraRunnerArgs;

	/** Start the runner (if needed) and connect when the panel opens. */
	UPROPERTY(Config, EditAnywhere, Category = "Runner")
	bool bAutoStartRunner = false;

	/** anthropic, openai (any OpenAI-compatible endpoint), claude_code or mock. */
	UPROPERTY(Config, EditAnywhere, Category = "Chat")
	FString DefaultProvider = TEXT("anthropic");

	/** Empty: the provider's default (Anthropic: claude-opus-5-5; OpenAI-compatible: UNREAL_MCP_OPENAI_MODEL). */
	UPROPERTY(Config, EditAnywhere, Category = "Chat")
	FString DefaultModel;

	/** general, or one of the specialists in the repo's agents folder (unreal-blueprint, unreal-level-designer, ...). */
	UPROPERTY(Config, EditAnywhere, Category = "Chat")
	FString DefaultSpecialist = TEXT("general");

	/** Send the open level, the selected actors and the Content Browser selection with each message. */
	UPROPERTY(Config, EditAnywhere, Category = "Chat")
	bool bSendEditorContext = true;

	/** Which tool calls need your confirmation in the panel before they run. */
	UPROPERTY(Config, EditAnywhere, Category = "Safety")
	EMCPAgentConfirmMode ConfirmMode = EMCPAgentConfirmMode::Destructive;

	static FString ConfirmModeName(EMCPAgentConfirmMode Mode);
};
