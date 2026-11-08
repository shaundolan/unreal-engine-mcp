#include "MCPServerRunnable.h"
#include "EpicUnrealMCPBridge.h"
#include "MCPCommon.h"
#include "Sockets.h"
#include "SocketSubsystem.h"
#include "HAL/RunnableThread.h"
#include "HAL/PlatformProcess.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

// Must match MAX_FRAME_BYTES in Python/unreal_mcp/protocol.py.
static constexpr uint32 MCP_MAX_FRAME_BYTES = 256u * 1024u * 1024u;
static constexpr int32 MCP_MAX_CLIENTS = 8;
static const FTimespan MCP_POLL = FTimespan::FromMilliseconds(250);

static FString Utf8ToString(const uint8* Data, int32 Len)
{
	FUTF8ToTCHAR Conv(reinterpret_cast<const ANSICHAR*>(Data), Len);
	return FString(Conv.Length(), Conv.Get());
}

static FString SerializeJson(const TSharedRef<FJsonObject>& Obj)
{
	FString Out;
	TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
		TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out);
	FJsonSerializer::Serialize(Obj, Writer);
	return Out;
}

// ---------------------------------------------------------------------------------------------
// Accept loop
// ---------------------------------------------------------------------------------------------

FMCPServerRunnable::FMCPServerRunnable(UEpicUnrealMCPBridge* InBridge, TSharedPtr<FSocket> InListenerSocket)
	: Bridge(InBridge), ListenerSocket(InListenerSocket), bRunning(true)
{
}

FMCPServerRunnable::~FMCPServerRunnable()
{
	for (FClient& C : Clients)
	{
		C.Runnable->Stop();
		C.Thread->WaitForCompletion();
		delete C.Thread;
		delete C.Runnable;
	}
	Clients.Empty();
}

void FMCPServerRunnable::Stop()
{
	bRunning = false;
	for (FClient& C : Clients)
	{
		C.Runnable->Stop();
	}
}

void FMCPServerRunnable::ReapFinishedClients()
{
	for (int32 i = Clients.Num() - 1; i >= 0; --i)
	{
		if (Clients[i].Runnable->IsFinished())
		{
			Clients[i].Thread->WaitForCompletion();
			delete Clients[i].Thread;
			delete Clients[i].Runnable;
			Clients.RemoveAtSwap(i);
		}
	}
}

uint32 FMCPServerRunnable::Run()
{
	UE_LOG(LogUnrealMCP, Display, TEXT("UnrealMCP: accept loop running"));
	while (bRunning)
	{
		ReapFinishedClients();

		bool bPending = false;
		if (ListenerSocket.IsValid() && ListenerSocket->WaitForPendingConnection(bPending, MCP_POLL) && bPending)
		{
			FSocket* Accepted = ListenerSocket->Accept(TEXT("UnrealMCPClient"));
			if (!Accepted)
			{
				continue;
			}
			if (Clients.Num() >= MCP_MAX_CLIENTS)
			{
				UE_LOG(LogUnrealMCP, Warning, TEXT("UnrealMCP: refusing client, %d already connected"), Clients.Num());
				Accepted->Close();
				ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Accepted);
				continue;
			}
			Accepted->SetNonBlocking(false);
			Accepted->SetNoDelay(true);
			int32 Actual = 0;
			Accepted->SetSendBufferSize(1 << 20, Actual);
			Accepted->SetReceiveBufferSize(1 << 20, Actual);

			FClient C;
			C.Runnable = new FMCPClientRunnable(Bridge, Accepted);
			C.Thread = FRunnableThread::Create(C.Runnable, TEXT("UnrealMCPClient"), 0, TPri_Normal);
			if (!C.Thread)
			{
				delete C.Runnable;
				continue;
			}
			Clients.Add(C);
		}
	}
	return 0;
}

// ---------------------------------------------------------------------------------------------
// Client connection
// ---------------------------------------------------------------------------------------------

FMCPClientRunnable::FMCPClientRunnable(UEpicUnrealMCPBridge* InBridge, FSocket* InSocket)
	: Bridge(InBridge), Socket(InSocket), bRunning(true), bFinished(false)
{
}

FMCPClientRunnable::~FMCPClientRunnable()
{
	if (Socket)
	{
		Socket->Close();
		ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Socket);
		Socket = nullptr;
	}
}

bool FMCPClientRunnable::RecvExact(uint8* Out, int32 Num)
{
	int32 Got = 0;
	while (Got < Num)
	{
		if (!bRunning)
		{
			return false;
		}
		if (!Socket->Wait(ESocketWaitConditions::WaitForRead, MCP_POLL))
		{
			if (Socket->GetConnectionState() != SCS_Connected)
			{
				return false;
			}
			continue; // idle; re-check bRunning
		}
		int32 Read = 0;
		if (!Socket->Recv(Out + Got, Num - Got, Read) || Read <= 0)
		{
			return false; // closed or error
		}
		Got += Read;
	}
	return true;
}

bool FMCPClientRunnable::SendAll(const uint8* Data, int32 Num)
{
	int32 Sent = 0;
	while (Sent < Num)
	{
		int32 N = 0;
		if (!Socket->Send(Data + Sent, Num - Sent, N) || N <= 0)
		{
			return false;
		}
		Sent += N;
	}
	return true;
}

FString FMCPClientRunnable::Dispatch(const FString& RequestText, bool bIncludeId)
{
	TSharedRef<FJsonObject> Reply = MakeShared<FJsonObject>();
	TSharedPtr<FJsonObject> Request;
	TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(RequestText);
	if (!FJsonSerializer::Deserialize(Reader, Request) || !Request.IsValid())
	{
		Reply->SetStringField(TEXT("status"), TEXT("error"));
		Reply->SetStringField(TEXT("error"), TEXT("invalid JSON request"));
		return SerializeJson(Reply);
	}

	const TSharedPtr<FJsonValue> Id = Request->TryGetField(TEXT("id"));
	FString Token;
	Request->TryGetStringField(TEXT("token"), Token);
	FString CommandType;

	if (!Bridge->CheckToken(Token))
	{
		Reply->SetStringField(TEXT("status"), TEXT("error"));
		Reply->SetStringField(TEXT("error"), TEXT("unauthorized: missing or bad token (set UNREAL_MCP_TOKEN on both sides)"));
	}
	else if (!Request->TryGetStringField(TEXT("type"), CommandType))
	{
		Reply->SetStringField(TEXT("status"), TEXT("error"));
		Reply->SetStringField(TEXT("error"), TEXT("request has no 'type'"));
	}
	else
	{
		const TSharedPtr<FJsonObject>* ParamsPtr = nullptr;
		TSharedPtr<FJsonObject> Params = Request->TryGetObjectField(TEXT("params"), ParamsPtr) && ParamsPtr
			? *ParamsPtr : MakeShared<FJsonObject>();
		Reply = Bridge->ExecuteCommandJson(CommandType, Params);
	}

	if (bIncludeId && Id.IsValid())
	{
		Reply->SetField(TEXT("id"), Id);
	}
	return SerializeJson(Reply);
}

bool FMCPClientRunnable::HandleFramed()
{
	uint8 Header[4];
	if (!RecvExact(Header, 4))
	{
		return false;
	}
	const uint32 Len = (uint32(Header[0]) << 24) | (uint32(Header[1]) << 16) | (uint32(Header[2]) << 8) | uint32(Header[3]);
	if (Len > MCP_MAX_FRAME_BYTES)
	{
		UE_LOG(LogUnrealMCP, Warning, TEXT("UnrealMCP: frame of %u bytes exceeds limit; closing client"), Len);
		return false;
	}
	TArray<uint8> Payload;
	Payload.SetNumUninitialized(int32(Len));
	if (Len > 0 && !RecvExact(Payload.GetData(), int32(Len)))
	{
		return false;
	}
	const FString RequestText = Utf8ToString(Payload.GetData(), int32(Len));

	const FString ReplyText = Dispatch(RequestText, true);
	FTCHARToUTF8 Utf8(*ReplyText);
	const uint32 OutLen = uint32(Utf8.Length());
	const uint8 OutHeader[4] = { uint8(OutLen >> 24), uint8(OutLen >> 16), uint8(OutLen >> 8), uint8(OutLen) };
	return SendAll(OutHeader, 4) && SendAll(reinterpret_cast<const uint8*>(Utf8.Get()), int32(OutLen));
}

void FMCPClientRunnable::HandleLegacy()
{
	// Original protocol: read until the bytes form a complete JSON object, reply unframed, close.
	TArray<uint8> Data;
	uint8 Buffer[65536];
	while (bRunning && Data.Num() < int32(MCP_MAX_FRAME_BYTES))
	{
		if (!Socket->Wait(ESocketWaitConditions::WaitForRead, FTimespan::FromSeconds(10)))
		{
			break;
		}
		int32 Read = 0;
		if (!Socket->Recv(Buffer, sizeof(Buffer), Read) || Read <= 0)
		{
			break;
		}
		Data.Append(Buffer, Read);
		const FString Text = Utf8ToString(Data.GetData(), Data.Num());
		TSharedPtr<FJsonObject> Probe;
		TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (FJsonSerializer::Deserialize(Reader, Probe) && Probe.IsValid())
		{
			const FString ReplyText = Dispatch(Text, false);
			FTCHARToUTF8 Utf8(*ReplyText);
			SendAll(reinterpret_cast<const uint8*>(Utf8.Get()), Utf8.Length());
			return;
		}
	}
}

uint32 FMCPClientRunnable::Run()
{
	// Peek the first byte to pick the protocol.
	uint8 First = 0;
	int32 Read = 0;
	while (bRunning && !Socket->Wait(ESocketWaitConditions::WaitForRead, MCP_POLL))
	{
		if (Socket->GetConnectionState() != SCS_Connected)
		{
			bFinished = true;
			return 0;
		}
	}
	if (bRunning && Socket->Recv(&First, 1, Read, ESocketReceiveFlags::Peek) && Read == 1)
	{
		if (First == '{')
		{
			HandleLegacy();
		}
		else
		{
			while (bRunning && HandleFramed())
			{
			}
		}
	}
	bFinished = true;
	return 0;
}
