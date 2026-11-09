#pragma once

#include "CoreMinimal.h"
#include "HAL/Runnable.h"
#include "HAL/ThreadSafeBool.h"
#include "Sockets.h"

class UEpicUnrealMCPBridge;
class FRunnableThread;

/**
 * Accept loop. Each accepted client gets its own FMCPClientRunnable thread, so a slow or
 * idle client never blocks others. Commands still execute one at a time on the game thread.
 */
class FMCPServerRunnable : public FRunnable
{
public:
	FMCPServerRunnable(UEpicUnrealMCPBridge* InBridge, TSharedPtr<FSocket> InListenerSocket);
	virtual ~FMCPServerRunnable();

	virtual bool Init() override { return true; }
	virtual uint32 Run() override;
	virtual void Stop() override;
	virtual void Exit() override {}

private:
	void ReapFinishedClients();

	struct FClient
	{
		class FMCPClientRunnable* Runnable = nullptr;
		FRunnableThread* Thread = nullptr;
	};

	UEpicUnrealMCPBridge* Bridge;
	TSharedPtr<FSocket> ListenerSocket;
	FThreadSafeBool bRunning;
	TArray<FClient> Clients;
};

/**
 * One client connection. Wire format is auto-detected from the first byte:
 *   '{'  legacy: one bare JSON request, one bare JSON reply, then close
 *   else framed: [uint32 big-endian length][UTF-8 JSON], persistent, any number of requests
 */
class FMCPClientRunnable : public FRunnable
{
public:
	FMCPClientRunnable(UEpicUnrealMCPBridge* InBridge, FSocket* InSocket);
	virtual ~FMCPClientRunnable();

	virtual uint32 Run() override;
	virtual void Stop() override { bRunning = false; }
	bool IsFinished() const { return bFinished; }

private:
	bool RecvExact(uint8* Out, int32 Num);
	bool SendAll(const uint8* Data, int32 Num);
	bool HandleFramed();
	void HandleLegacy();
	FString Dispatch(const FString& RequestText, bool bIncludeId);

	UEpicUnrealMCPBridge* Bridge;
	FSocket* Socket;
	FThreadSafeBool bRunning;
	FThreadSafeBool bFinished;
};
