#include "Agent/MCPAgentTurns.h"

#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Editor/TransBuffer.h"
#include "MCPCommon.h"
#include "Misc/ITransaction.h"

namespace
{
UTransactor* Trans()
{
	return GEditor ? GEditor->Trans.Get() : nullptr;
}

int32 ActiveCount(const UTransactor* T)
{
	return T ? T->GetQueueLength() - T->GetUndoCount() : 0;
}

FGuid IdAt(const UTransactor* T, int32 Index)
{
	if (!T || Index < 0 || Index >= T->GetQueueLength())
	{
		return FGuid();
	}
	const FTransaction* Tr = T->GetTransaction(Index);
	return Tr ? Tr->GetContext().TransactionId : FGuid();
}

FString TitleAt(const UTransactor* T, int32 Index)
{
	const FTransaction* Tr = (T && Index >= 0 && Index < T->GetQueueLength()) ? T->GetTransaction(Index) : nullptr;
	return Tr ? Tr->GetContext().Title.ToString() : FString();
}

TSharedPtr<FJsonObject> Ok()
{
	TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
	O->SetBoolField(TEXT("success"), true);
	return O;
}

TArray<TSharedPtr<FJsonValue>> Strings(const TArray<FString>& In)
{
	TArray<TSharedPtr<FJsonValue>> Out;
	for (const FString& S : In)
	{
		Out.Add(MakeShared<FJsonValueString>(S));
	}
	return Out;
}
}

FMCPAgentTurns& FMCPAgentTurns::Get()
{
	static FMCPAgentTurns Instance;
	return Instance;
}

TSharedPtr<FJsonObject> FMCPAgentTurns::Begin(int32 Turn)
{
	UTransactor* T = Trans();
	if (!T)
	{
		return MCPError(TEXT("no editor transaction buffer"));
	}
	Record = FRecord();
	Record.Turn = Turn;
	Record.bOpen = true;
	Record.StartActive = ActiveCount(T);
	Record.StartTop = IdAt(T, Record.StartActive - 1);
	TSharedPtr<FJsonObject> O = Ok();
	O->SetNumberField(TEXT("turn"), Turn);
	O->SetNumberField(TEXT("active_transactions"), Record.StartActive);
	return O;
}

TSharedPtr<FJsonObject> FMCPAgentTurns::End(int32 Turn)
{
	UTransactor* T = Trans();
	if (!T)
	{
		return MCPError(TEXT("no editor transaction buffer"));
	}
	if (!Record.bOpen || Record.Turn != Turn)
	{
		return MCPError(FString::Printf(TEXT("turn %d was not begun"), Turn));
	}
	Record.bOpen = false;
	const int32 Active = ActiveCount(T);
	// The buffer was reset (map switch) or rewound below the starting point: the turn's steps can't be identified.
	if (Active < Record.StartActive || IdAt(T, Record.StartActive - 1) != Record.StartTop)
	{
		Record.bReset = true;
	}
	else
	{
		for (int32 i = Record.StartActive; i < Active; ++i)
		{
			Record.Ids.Add(IdAt(T, i));
			Record.Titles.Add(TitleAt(T, i));
		}
	}
	TSharedPtr<FJsonObject> O = Ok();
	O->SetNumberField(TEXT("turn"), Turn);
	O->SetNumberField(TEXT("steps"), Record.Ids.Num());
	O->SetBoolField(TEXT("undo_history_reset"), Record.bReset);
	O->SetArrayField(TEXT("titles"), Strings(Record.Titles));
	return O;
}

int32 FMCPAgentTurns::UndoableSteps(FString* Why) const
{
	auto Fail = [Why](const TCHAR* Reason) { if (Why) { *Why = Reason; } return 0; };
	const UTransactor* T = Trans();
	if (!T || Record.Turn == 0)
	{
		return Fail(TEXT("no agent turn yet"));
	}
	if (Record.bOpen)
	{
		return Fail(TEXT("the agent turn is still running"));
	}
	if (Record.bUndone)
	{
		return Fail(TEXT("the last agent turn was already undone"));
	}
	if (Record.bReset)
	{
		return Fail(TEXT("the undo history was reset during the turn (map switch), so it can't be undone as a whole"));
	}
	if (Record.Ids.Num() == 0)
	{
		return Fail(TEXT("the last agent turn made no undoable changes"));
	}
	if (GEditor && GEditor->PlayWorld)
	{
		return Fail(TEXT("stop Play In Editor first"));
	}
	const int32 Active = ActiveCount(T);
	const int32 N = Record.Ids.Num();
	if (Active < N)
	{
		return Fail(TEXT("the undo history changed since the turn"));
	}
	for (int32 i = 0; i < N; ++i)
	{
		if (IdAt(T, Active - N + i) != Record.Ids[i])
		{
			return Fail(TEXT("something else was done or undone after the agent turn; use Edit > Undo History instead"));
		}
	}
	return N;
}

TSharedPtr<FJsonObject> FMCPAgentTurns::UndoLast()
{
	FString Why;
	const int32 N = UndoableSteps(&Why);
	if (N == 0)
	{
		return MCPError(Why);
	}
	if (GEditor->IsTransactionActive())
	{
		return MCPError(TEXT("a transaction is open; try again in a moment"));
	}
	int32 Undone = 0;
	for (int32 i = 0; i < N; ++i)
	{
		if (!GEditor->UndoTransaction())
		{
			break;
		}
		++Undone;
	}
	Record.bUndone = true;
	TSharedPtr<FJsonObject> O = Undone == N ? Ok() : MCPError(FString::Printf(TEXT("undid %d of %d steps"), Undone, N));
	O->SetNumberField(TEXT("turn"), Record.Turn);
	O->SetNumberField(TEXT("undone"), Undone);
	O->SetArrayField(TEXT("titles"), Strings(Record.Titles));
	return O;
}

TSharedPtr<FJsonObject> FMCPAgentTurns::Status() const
{
	FString Why;
	const int32 N = UndoableSteps(&Why);
	TSharedPtr<FJsonObject> O = Ok();
	O->SetNumberField(TEXT("turn"), Record.Turn);
	O->SetBoolField(TEXT("open"), Record.bOpen);
	O->SetNumberField(TEXT("steps"), Record.Ids.Num());
	O->SetNumberField(TEXT("undoable_steps"), N);
	O->SetStringField(TEXT("not_undoable_because"), N ? FString() : Why);
	O->SetArrayField(TEXT("titles"), Strings(Record.Titles));
	return O;
}
