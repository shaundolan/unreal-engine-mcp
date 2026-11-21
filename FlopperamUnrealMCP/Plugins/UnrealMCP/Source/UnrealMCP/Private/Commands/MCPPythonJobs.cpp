#include "Commands/MCPPythonJobs.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "HAL/CriticalSection.h"
#include "HAL/PlatformTime.h"
#include "Misc/ScopeLock.h"

namespace
{
struct FJobState
{
	double Created = 0.0;
	double Started = 0.0;  // 0 until execute_python begins it
	double Ended = 0.0;    // 0 while running
	double LastReport = 0.0;
	double Progress = 0.0;
	double Total = 0.0;
	int32 Reports = 0;
	FString Message;
	bool bCancel = false;
	FString CancelReason;
};

FCriticalSection GJobsLock;
TMap<FString, FJobState> GJobs; // guarded by GJobsLock

constexpr int32 MaxJobs = 64;
constexpr double KeepFinishedS = 1800.0;

/** Drop finished jobs older than KeepFinishedS, then the oldest finished ones beyond MaxJobs. Caller holds the lock. */
void PruneLocked(double Now)
{
	for (auto It = GJobs.CreateIterator(); It; ++It)
	{
		const FJobState& J = It.Value();
		const bool bStale = J.Ended > 0.0 ? Now - J.Ended > KeepFinishedS
		                                  : (J.Started == 0.0 && Now - J.Created > KeepFinishedS); // cancel for a job that never came
		if (bStale)
		{
			It.RemoveCurrent();
		}
	}
	while (GJobs.Num() > MaxJobs)
	{
		FString Oldest;
		double OldestEnd = TNumericLimits<double>::Max();
		for (const TPair<FString, FJobState>& P : GJobs)
		{
			if (P.Value.Ended > 0.0 && P.Value.Ended < OldestEnd)
			{
				OldestEnd = P.Value.Ended;
				Oldest = P.Key;
			}
		}
		if (Oldest.IsEmpty())
		{
			break; // all running: keep them
		}
		GJobs.Remove(Oldest);
	}
}

double Round1(double X)
{
	return FMath::RoundToDouble(X * 10.0) / 10.0;
}

TSharedRef<FJsonObject> StateJsonLocked(const FString& JobId, const FJobState& J, double Now)
{
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("job_id"), JobId);
	Out->SetBoolField(TEXT("known"), true);
	Out->SetBoolField(TEXT("started"), J.Started > 0.0);
	Out->SetBoolField(TEXT("running"), J.Started > 0.0 && J.Ended == 0.0);
	if (J.Started > 0.0)
	{
		Out->SetNumberField(TEXT("elapsed_s"), Round1((J.Ended > 0.0 ? J.Ended : Now) - J.Started));
	}
	if (J.Ended > 0.0)
	{
		Out->SetNumberField(TEXT("ended_age_s"), Round1(Now - J.Ended));
	}
	Out->SetNumberField(TEXT("reports"), J.Reports);
	if (J.Reports > 0)
	{
		Out->SetNumberField(TEXT("progress"), J.Progress);
		if (J.Total > 0.0)
		{
			Out->SetNumberField(TEXT("total"), J.Total);
		}
		Out->SetStringField(TEXT("message"), J.Message);
		Out->SetNumberField(TEXT("last_report_age_s"), Round1(Now - J.LastReport));
	}
	Out->SetBoolField(TEXT("cancel_requested"), J.bCancel);
	if (J.bCancel)
	{
		Out->SetStringField(TEXT("cancel_reason"), J.CancelReason);
	}
	return Out;
}
} // namespace

void FMCPPythonJobs::Begin(const FString& JobId)
{
	if (JobId.IsEmpty())
	{
		return;
	}
	const double Now = FPlatformTime::Seconds();
	FScopeLock Lock(&GJobsLock);
	PruneLocked(Now);
	FJobState& J = GJobs.FindOrAdd(JobId);
	if (J.Created == 0.0)
	{
		J.Created = Now;
	}
	J.Started = Now;
	J.Ended = 0.0;
}

void FMCPPythonJobs::End(const FString& JobId)
{
	if (JobId.IsEmpty())
	{
		return;
	}
	FScopeLock Lock(&GJobsLock);
	if (FJobState* J = GJobs.Find(JobId))
	{
		J->Ended = FPlatformTime::Seconds();
	}
}

bool FMCPPythonJobs::Report(const FString& JobId, double Progress, double Total, const FString& Message)
{
	if (JobId.IsEmpty())
	{
		return false;
	}
	const double Now = FPlatformTime::Seconds();
	FScopeLock Lock(&GJobsLock);
	FJobState& J = GJobs.FindOrAdd(JobId);
	if (J.Created == 0.0)
	{
		J.Created = J.Started = Now; // a script reporting under an id execute_python did not begin (direct use)
	}
	J.Progress = Progress;
	J.Total = Total;
	J.Message = Message.Left(1024);
	J.LastReport = Now;
	++J.Reports;
	return J.bCancel;
}

bool FMCPPythonJobs::IsCancelRequested(const FString& JobId)
{
	FScopeLock Lock(&GJobsLock);
	const FJobState* J = GJobs.Find(JobId);
	return J && J->bCancel;
}

void FMCPPythonJobs::RequestCancel(const FString& JobId, const FString& Reason)
{
	if (JobId.IsEmpty())
	{
		return;
	}
	const double Now = FPlatformTime::Seconds();
	FScopeLock Lock(&GJobsLock);
	FJobState& J = GJobs.FindOrAdd(JobId);
	if (J.Created == 0.0)
	{
		J.Created = Now;
	}
	if (!J.bCancel)
	{
		J.bCancel = true;
		J.CancelReason = Reason.IsEmpty() ? TEXT("cancelled") : Reason;
	}
}

TSharedRef<FJsonObject> FMCPPythonJobs::StatusJson(const FString& JobId)
{
	const double Now = FPlatformTime::Seconds();
	FScopeLock Lock(&GJobsLock);
	if (const FJobState* J = GJobs.Find(JobId))
	{
		return StateJsonLocked(JobId, *J, Now);
	}
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("job_id"), JobId);
	Out->SetBoolField(TEXT("known"), false);
	return Out;
}

TArray<TSharedPtr<FJsonValue>> FMCPPythonJobs::RunningJson()
{
	const double Now = FPlatformTime::Seconds();
	TArray<TSharedPtr<FJsonValue>> Out;
	FScopeLock Lock(&GJobsLock);
	for (const TPair<FString, FJobState>& P : GJobs)
	{
		if (P.Value.Started > 0.0 && P.Value.Ended == 0.0)
		{
			Out.Add(MakeShared<FJsonValueObject>(StateJsonLocked(P.Key, P.Value, Now)));
		}
	}
	return Out;
}

// ---------------------------------------------------------------------------------------------

bool UMCPPythonJobLibrary::ReportProgress(const FString& JobId, double Progress, double Total, const FString& Message)
{
	return FMCPPythonJobs::Report(JobId, Progress, Total, Message);
}

bool UMCPPythonJobLibrary::IsCancelRequested(const FString& JobId)
{
	return FMCPPythonJobs::IsCancelRequested(JobId);
}

int32 UMCPPythonJobLibrary::GetUndoableTransactionCount()
{
	const UTransactor* T = GEditor ? GEditor->Trans.Get() : nullptr;
	return T ? T->GetQueueLength() - T->GetUndoCount() : 0;
}

bool UMCPPythonJobLibrary::IsTransactionActive()
{
	return GEditor && GEditor->IsTransactionActive();
}

int32 UMCPPythonJobLibrary::BeginTransaction(const FString& Description)
{
	return GEditor ? GEditor->BeginTransaction(TEXT("UnrealMCP"), FText::FromString(Description), nullptr) : -1;
}

int32 UMCPPythonJobLibrary::EndTransaction()
{
	return GEditor ? GEditor->EndTransaction() : -1;
}

bool UMCPPythonJobLibrary::UndoLastTransaction()
{
	return GEditor && !GEditor->IsTransactionActive() && GEditor->UndoTransaction(/*bCanRedo*/ true);
}
