#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MCPPythonJobs.generated.h"

class FJsonObject;
class FJsonValue;

/**
 * Progress / cancel registry for long execute_python jobs, keyed by the job_id the client sends.
 *
 * The script fills it on the game thread (through UMCPPythonJobLibrary, i.e. unreal_mcp.progress() in the script);
 * socket threads read and cancel it (python_job_status / python_job_cancel, answered without queueing like
 * editor_health), because the game thread is busy for as long as the script runs. Every method is thread-safe.
 */
class UNREALMCP_API FMCPPythonJobs
{
public:
	/** execute_python is about to run the job (keeps a cancel requested while it was still queued). */
	static void Begin(const FString& JobId);
	/** The job finished; its last state stays readable for a while. */
	static void End(const FString& JobId);
	/** Record progress. Total <= 0 means "unknown". Returns true when the job should stop (cancel requested). */
	static bool Report(const FString& JobId, double Progress, double Total, const FString& Message);
	static bool IsCancelRequested(const FString& JobId);
	/** Flag the job for cooperative cancellation. Unknown ids are recorded too (the job may still be queued). */
	static void RequestCancel(const FString& JobId, const FString& Reason);
	/** {job_id, known, running, elapsed_s, reports, progress, total, message, last_report_age_s, cancel_requested, ...}. */
	static TSharedRef<FJsonObject> StatusJson(const FString& JobId);
	/** Status of every running job (for editor_health). */
	static TArray<TSharedPtr<FJsonValue>> RunningJson();
};

/**
 * Reachable from editor Python as unreal.MCPPythonJobLibrary. The execute_python wrapper uses it to relay progress,
 * poll for cancellation and run transactional scripts; scripts normally call the unreal_mcp helper instead.
 */
UCLASS()
class UNREALMCP_API UMCPPythonJobLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Record progress for a job (Total <= 0: unknown). @return true when the job has been asked to cancel. */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP|Python")
	static bool ReportProgress(const FString& JobId, double Progress, double Total, const FString& Message);

	UFUNCTION(BlueprintCallable, Category = "UnrealMCP|Python")
	static bool IsCancelRequested(const FString& JobId);

	/** Undo entries that an undo would pop (queue length minus already-undone entries). */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP|Python")
	static int32 GetUndoableTransactionCount();

	UFUNCTION(BlueprintCallable, Category = "UnrealMCP|Python")
	static bool IsTransactionActive();

	/** GEditor->BeginTransaction. @return the transaction index, or -1 without an editor. */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP|Python")
	static int32 BeginTransaction(const FString& Description);

	UFUNCTION(BlueprintCallable, Category = "UnrealMCP|Python")
	static int32 EndTransaction();

	/** Undo the newest undo entry. @return whether something was undone. */
	UFUNCTION(BlueprintCallable, Category = "UnrealMCP|Python")
	static bool UndoLastTransaction();
};
