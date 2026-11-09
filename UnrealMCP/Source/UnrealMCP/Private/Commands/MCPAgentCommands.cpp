#include "Commands/MCPAgentCommands.h"

#include "Agent/MCPAgentClient.h"
#include "Agent/MCPAgentTurns.h"
#include "Agent/SMCPAgentPanel.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
FString Action(const TSharedPtr<FJsonObject>& P)
{
	FString A;
	P->TryGetStringField(TEXT("action"), A);
	return A;
}

TSharedPtr<FJsonObject> WithSuccess(bool bOk, const FString& Error)
{
	return bOk ? FMCPAgentClient::Get().StatusJson() : MCPError(Error);
}

TSharedPtr<FJsonObject> AgentTurn(const TSharedPtr<FJsonObject>& P)
{
	const FString A = Action(P);
	int32 Turn = 0;
	P->TryGetNumberField(TEXT("turn"), Turn);
	FMCPAgentTurns& T = FMCPAgentTurns::Get();
	if (A == TEXT("begin"))
	{
		return T.Begin(Turn);
	}
	if (A == TEXT("end"))
	{
		return T.End(Turn);
	}
	if (A == TEXT("undo"))
	{
		return T.UndoLast();
	}
	if (A == TEXT("status") || A.IsEmpty())
	{
		return T.Status();
	}
	return MCPError(FString::Printf(TEXT("unknown agent_turn action '%s' (begin, end, undo, status)"), *A));
}

TSharedPtr<FJsonObject> AgentPanel(const TSharedPtr<FJsonObject>& P)
{
	const FString A = Action(P);
	FMCPAgentClient& C = FMCPAgentClient::Get();
	FString Err;
	if (A == TEXT("status") || A.IsEmpty())
	{
		TSharedPtr<FJsonObject> O = C.StatusJson();
		O->SetStringField(TEXT("tab_id"), MCPAgentPanel::TabId.ToString());
		O->SetBoolField(TEXT("tab_registered"), MCPAgentPanel::IsTabRegistered());
		O->SetBoolField(TEXT("tab_open"), MCPAgentPanel::IsTabOpen());
		return O;
	}
	if (A == TEXT("open"))
	{
		const bool bOpen = MCPAgentPanel::OpenTab();
		TSharedPtr<FJsonObject> O = bOpen ? C.StatusJson() : MCPError(TEXT("the tab did not open"));
		O->SetBoolField(TEXT("tab_open"), bOpen);
		return O;
	}
	if (A == TEXT("close"))
	{
		MCPAgentPanel::CloseTab();
		return C.StatusJson();
	}
	if (A == TEXT("start_runner"))
	{
		FString Extra;
		P->TryGetStringField(TEXT("extra_args"), Extra);
		return WithSuccess(C.StartRunner(Extra, Err), Err);
	}
	if (A == TEXT("stop_runner"))
	{
		C.StopRunner();
		return C.StatusJson();
	}
	if (A == TEXT("connect"))
	{
		return WithSuccess(C.Connect(Err), Err);
	}
	if (A == TEXT("disconnect"))
	{
		C.Disconnect();
		return C.StatusJson();
	}
	if (A == TEXT("send"))
	{
		FString Text, Provider, Model, Specialist;
		P->TryGetStringField(TEXT("text"), Text);
		P->TryGetStringField(TEXT("provider"), Provider);
		P->TryGetStringField(TEXT("model"), Model);
		P->TryGetStringField(TEXT("specialist"), Specialist);
		return WithSuccess(C.SendUserMessage(Text, Provider, Model, Specialist, Err, P->TryGetField(TEXT("mock_script"))), Err);
	}
	if (A == TEXT("stop"))
	{
		C.StopTurn();
		return C.StatusJson();
	}
	if (A == TEXT("confirm"))
	{
		FString Id;
		bool bAllow = false;
		P->TryGetStringField(TEXT("id"), Id);
		P->TryGetBoolField(TEXT("allow"), bAllow);
		C.AnswerConfirm(Id, bAllow);
		return C.StatusJson();
	}
	if (A == TEXT("reset"))
	{
		C.ResetConversation();
		return C.StatusJson();
	}
	if (A == TEXT("transcript"))
	{
		int32 Since = 0;
		P->TryGetNumberField(TEXT("since"), Since);
		return C.TranscriptJson(Since);
	}
	if (A == TEXT("undo_last_turn"))
	{
		return C.UndoLastTurn();
	}
	if (A == TEXT("context"))
	{
		TSharedPtr<FJsonObject> O = FMCPAgentClient::GatherEditorContext();
		O->SetBoolField(TEXT("success"), true);
		return O;
	}
	return MCPError(FString::Printf(TEXT("unknown agent_panel action '%s'"), *A));
}
}

void FMCPAgentCommands::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("agent_turn"), &AgentTurn, false);
	Registry.Register(TEXT("agent_panel"), &AgentPanel, false);
}
