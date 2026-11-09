#pragma once

#include "CoreMinimal.h"
#include "Misc/OutputDevice.h"
#include "HAL/CriticalSection.h"
#include "MCPCommon.h"

/** Ring buffer of recent Output Log lines, attached to GLog for the editor's lifetime. */
class UNREALMCP_API FMCPLogCapture : public FOutputDevice
{
public:
	struct FEntry
	{
		uint64 Seq;
		FDateTime Time;
		FName Category;
		ELogVerbosity::Type Verbosity;
		FString Message;
	};

	static FMCPLogCapture& Get();

	virtual void Serialize(const TCHAR* V, ELogVerbosity::Type Verbosity, const FName& Category) override;
	virtual bool CanBeUsedOnAnyThread() const override { return true; }
	virtual bool CanBeUsedOnMultipleThreads() const override { return true; }

	/** Entries with Seq > SinceSeq, most recent MaxLines, filtered. */
	TArray<FEntry> Query(uint64 SinceSeq, int32 MaxLines, ELogVerbosity::Type MinVerbosity,
		const FString& Filter, const FString& Category) const;
	uint64 LastSeq() const;

private:
	static constexpr int32 Capacity = 10000;
	mutable FCriticalSection Lock;
	TArray<FEntry> Ring;
	int32 Head = 0;
	uint64 NextSeq = 1;
};

/** editor_log, viewport_capture, project_context, console_command, editor_action. */
class UNREALMCP_API FEpicUnrealMCPObservationCommands
{
public:
	static void Register(IMCPCommandRegistry& Registry);
};
