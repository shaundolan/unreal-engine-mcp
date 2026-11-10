#include "Agent/SMCPAgentPanel.h"

#include "Agent/MCPAgentClient.h"
#include "Agent/MCPAgentSettings.h"
#include "Agent/MCPAgentTurns.h"
#include "Framework/Docking/TabManager.h"
#include "Misc/Paths.h"
#include "Styling/AppStyle.h"
#include "Styling/CoreStyle.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SComboBox.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Input/SMultiLineEditableTextBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SExpandableArea.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"
#include "WorkspaceMenuStructure.h"
#include "WorkspaceMenuStructureModule.h"

#define LOCTEXT_NAMESPACE "UnrealMCPAgent"

namespace
{
const FLinearColor AgentPanelUIColUser(0.30f, 0.55f, 0.95f);
const FLinearColor AgentPanelUIColOk(0.35f, 0.80f, 0.40f);
const FLinearColor AgentPanelUIColErr(0.95f, 0.35f, 0.30f);
const FLinearColor AgentPanelUIColWarn(0.95f, 0.75f, 0.25f);
const FLinearColor AgentPanelUIColDim(0.60f, 0.60f, 0.60f);

FSlateFontInfo AgentPanelUIFont(const TCHAR* Style, int32 Size = 9)
{
	return FCoreStyle::GetDefaultFontStyle(Style, Size);
}

TSharedRef<SWidget> AgentPanelUIMono(const FString& Text)
{
	return SNew(SBorder)
		.BorderImage(FAppStyle::GetBrush("ToolPanel.DarkGroupBorder"))
		.Padding(4)
		[
			SNew(STextBlock).Text(FText::FromString(Text)).Font(AgentPanelUIFont(TEXT("Mono"), 8)).AutoWrapText(true)
		];
}
}

void SMCPAgentPanel::Construct(const FArguments&)
{
	const UMCPAgentSettings* S = GetDefault<UMCPAgentSettings>();
	FMCPAgentClient& Client = FMCPAgentClient::Get();
	ChangedHandle = Client.OnItemsChanged.AddSP(this, &SMCPAgentPanel::Rebuild);
	RefreshOptions();

	ChildSlot
	[
		SNew(SVerticalBox)
		// toolbar
		+ SVerticalBox::Slot().AutoHeight().Padding(4)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 4, 0)
			[ SNew(STextBlock).Text(LOCTEXT("Provider", "Provider")) ]
			+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 8, 0)
			[ MakeCombo(ProviderOptions, SelectedProvider, ProviderCombo) ]
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 4, 0)
			[ SNew(STextBlock).Text(LOCTEXT("Model", "Model")) ]
			+ SHorizontalBox::Slot().FillWidth(1).Padding(0, 0, 8, 0)
			[
				SAssignNew(ModelBox, SEditableTextBox)
				.Text(FText::FromString(S->DefaultModel))
				.HintText(LOCTEXT("ModelHint", "provider default"))
				.ToolTipText(LOCTEXT("ModelTip", "Model id. Empty uses the provider's default (Anthropic: claude-opus-5-5; OpenAI-compatible: UNREAL_MCP_OPENAI_MODEL)."))
			]
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 4, 0)
			[ SNew(STextBlock).Text(LOCTEXT("Agent", "Agent")) ]
			+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 8, 0)
			[ MakeCombo(SpecialistOptions, SelectedSpecialist, SpecialistCombo) ]
			+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 4, 0)
			[ SNew(SButton).Text(this, &SMCPAgentPanel::RunnerButtonText).OnClicked(this, &SMCPAgentPanel::OnRunnerButton)
				.ToolTipText(LOCTEXT("RunnerTip", "Start or stop the local agent runner process (Python/unreal_mcp/agent_runner.py)")) ]
			+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 4, 0)
			[ SNew(SButton).Text(LOCTEXT("Connect", "Connect")).OnClicked(this, &SMCPAgentPanel::OnConnect)
				.IsEnabled_Lambda([]() { return !FMCPAgentClient::Get().IsConnected(); })
				.ToolTipText(LOCTEXT("ConnectTip", "Connect to a runner you started yourself")) ]
			+ SHorizontalBox::Slot().AutoWidth()
			[ SNew(SButton).Text(LOCTEXT("NewChat", "New chat")).OnClicked(this, &SMCPAgentPanel::OnNewChat) ]
		]
		// status line
		+ SVerticalBox::Slot().AutoHeight().Padding(6, 0, 6, 2)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth()
			[ SNew(STextBlock).Text(this, &SMCPAgentPanel::StatusText).ColorAndOpacity(this, &SMCPAgentPanel::StatusColor).Font(AgentPanelUIFont(TEXT("Bold"))) ]
			+ SHorizontalBox::Slot().FillWidth(1).Padding(12, 0, 0, 0)
			[ SNew(STextBlock).Text(this, &SMCPAgentPanel::HintText).ColorAndOpacity(AgentPanelUIColDim).AutoWrapText(true) ]
		]
		+ SVerticalBox::Slot().AutoHeight()[ SNew(SSeparator) ]
		// transcript
		+ SVerticalBox::Slot().FillHeight(1).Padding(4)
		[
			SAssignNew(Scroll, SScrollBox)
			+ SScrollBox::Slot()[ SAssignNew(Rows, SVerticalBox) ]
		]
		+ SVerticalBox::Slot().AutoHeight()[ SNew(SSeparator) ]
		// input
		+ SVerticalBox::Slot().AutoHeight().Padding(4)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().FillWidth(1).Padding(0, 0, 4, 0)
			[
				SNew(SBox).MinDesiredHeight(54).MaxDesiredHeight(160)
				[
					SAssignNew(Input, SMultiLineEditableTextBox)
					.AutoWrapText(true)
					.HintText(LOCTEXT("InputHint", "Ask the agent (Enter sends, Shift+Enter for a new line). The open level and your selection are sent along."))
					.OnKeyDownHandler_Lambda([this](const FGeometry&, const FKeyEvent& Key)
					{
						if (Key.GetKey() == EKeys::Enter && !Key.IsShiftDown())
						{
							OnSend();
							return FReply::Handled();
						}
						return FReply::Unhandled();
					})
				]
			]
			+ SHorizontalBox::Slot().AutoWidth()
			[
				SNew(SVerticalBox)
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 2)
				[ SNew(SButton).Text(LOCTEXT("Send", "Send")).OnClicked(this, &SMCPAgentPanel::OnSend)
					.IsEnabled_Lambda([]() { return FMCPAgentClient::Get().IsConnected() && !FMCPAgentClient::Get().IsTurnRunning(); }) ]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 2)
				[ SNew(SButton).Text(LOCTEXT("Stop", "Stop")).OnClicked(this, &SMCPAgentPanel::OnStop)
					.IsEnabled_Lambda([]() { return FMCPAgentClient::Get().IsTurnRunning(); }) ]
				+ SVerticalBox::Slot().AutoHeight()
				[ SNew(SButton).Text(LOCTEXT("Undo", "Undo last agent turn")).OnClicked(this, &SMCPAgentPanel::OnUndo)
					.IsEnabled(this, &SMCPAgentPanel::CanUndo).ToolTipText(this, &SMCPAgentPanel::UndoTooltip) ]
			]
		]
	];
	Rebuild();

	if (S->bAutoStartRunner && !Client.IsConnected())
	{
		FString Err;
		if (FPaths::FileExists(FMCPAgentClient::TokenFilePath(S->RunnerPort)) ? !Client.Connect(Err) : !Client.StartRunner(FString(), Err))
		{
			UE_LOG(LogTemp, Display, TEXT("UnrealMCP Agent: %s"), *Err);
		}
	}
}

SMCPAgentPanel::~SMCPAgentPanel()
{
	FMCPAgentClient::Get().OnItemsChanged.Remove(ChangedHandle);
}

TSharedRef<SWidget> SMCPAgentPanel::MakeCombo(TArray<TSharedPtr<FString>>& Options, TSharedPtr<FString>& Selected,
	TSharedPtr<SComboBox<TSharedPtr<FString>>>& Out)
{
	TSharedPtr<FString>* SelectedPtr = &Selected;
	return SAssignNew(Out, SComboBox<TSharedPtr<FString>>)
		.OptionsSource(&Options)
		.InitiallySelectedItem(Selected)
		.OnGenerateWidget_Lambda([](TSharedPtr<FString> O) { return SNew(STextBlock).Text(FText::FromString(O.IsValid() ? *O : FString())); })
		.OnSelectionChanged_Lambda([SelectedPtr](TSharedPtr<FString> O, ESelectInfo::Type) { if (O.IsValid()) { *SelectedPtr = O; } })
		[
			SNew(STextBlock).Text_Lambda([SelectedPtr]() { return FText::FromString(SelectedPtr->IsValid() ? **SelectedPtr : FString()); })
		];
}

FString SMCPAgentPanel::CurrentOption(const TSharedPtr<FString>& Sel) const
{
	return Sel.IsValid() ? *Sel : FString();
}

void SMCPAgentPanel::RefreshOptions()
{
	const UMCPAgentSettings* S = GetDefault<UMCPAgentSettings>();
	const FMCPAgentClient& Client = FMCPAgentClient::Get();
	TArray<FString> Provs;
	for (const TSharedPtr<FJsonObject>& P : Client.GetProviders())
	{
		Provs.Add(P->GetStringField(TEXT("id")));
	}
	if (Provs.Num() == 0)
	{
		Provs = { TEXT("anthropic"), TEXT("openai"), TEXT("claude_code"), TEXT("mock") };
	}
	TArray<FString> Specs = Client.GetSpecialists();
	if (Specs.Num() == 0)
	{
		Specs = { TEXT("general") };
	}
	auto Sync = [](TArray<TSharedPtr<FString>>& Options, const TArray<FString>& Ids, TSharedPtr<FString>& Selected, const FString& Default)
	{
		const FString Keep = Selected.IsValid() ? *Selected : Default;
		Options.Reset();
		Selected.Reset();
		for (const FString& Id : Ids)
		{
			Options.Add(MakeShared<FString>(Id));
			if (Id == Keep)
			{
				Selected = Options.Last();
			}
		}
		if (!Selected.IsValid() && Options.Num())
		{
			Selected = Options[0];
		}
	};
	Sync(ProviderOptions, Provs, SelectedProvider, S->DefaultProvider);
	Sync(SpecialistOptions, Specs, SelectedSpecialist, S->DefaultSpecialist);
	if (ProviderCombo.IsValid())
	{
		ProviderCombo->RefreshOptions();
		ProviderCombo->SetSelectedItem(SelectedProvider);
	}
	if (SpecialistCombo.IsValid())
	{
		SpecialistCombo->RefreshOptions();
		SpecialistCombo->SetSelectedItem(SelectedSpecialist);
	}
}

void SMCPAgentPanel::Rebuild()
{
	if (!Rows.IsValid())
	{
		return;
	}
	const TArray<TSharedPtr<FMCPChatItem>>& Items = FMCPAgentClient::Get().GetItems();
	if ((int32)ProviderOptions.Num() != FMath::Max(4, FMCPAgentClient::Get().GetProviders().Num()) ||
		SpecialistOptions.Num() != FMath::Max(1, FMCPAgentClient::Get().GetSpecialists().Num()))
	{
		RefreshOptions();
	}
	Rows->ClearChildren();
	if (Items.Num() == 0)
	{
		Rows->AddSlot().AutoHeight().Padding(8)
		[
			SNew(STextBlock).ColorAndOpacity(AgentPanelUIColDim).AutoWrapText(true)
			.Text(LOCTEXT("Empty", "Start the runner, pick a provider and ask for something, e.g. \"add a warm point light above the selected actor\". "
				"Each tool call shows up here with its arguments and result. Destructive calls ask you first."))
		];
	}
	for (const TSharedPtr<FMCPChatItem>& Item : Items)
	{
		Rows->AddSlot().AutoHeight().Padding(2, 3)[ MakeRow(Item) ];
	}
	BuiltItems = Items.Num();
	if (Scroll.IsValid())
	{
		Scroll->ScrollToEnd();
	}
}

TSharedRef<SWidget> SMCPAgentPanel::MakeRow(const TSharedPtr<FMCPChatItem>& Item)
{
	using EKind = FMCPChatItem::EKind;
	switch (Item->Kind)
	{
	case EKind::User:
		return SNew(SBorder).BorderImage(FAppStyle::GetBrush("ToolPanel.GroupBorder")).Padding(6)
			[
				SNew(SVerticalBox)
				+ SVerticalBox::Slot().AutoHeight()[ SNew(STextBlock).Text(LOCTEXT("You", "You")).Font(AgentPanelUIFont(TEXT("Bold"))).ColorAndOpacity(AgentPanelUIColUser) ]
				+ SVerticalBox::Slot().AutoHeight()[ SNew(STextBlock).Text(FText::FromString(Item->Text)).AutoWrapText(true) ]
			];
	case EKind::Assistant:
		return SNew(SBox).Padding(FMargin(6, 2))
			[
				SNew(STextBlock).AutoWrapText(true)
				.Text_Lambda([Item]() { return FText::FromString(Item->Text); })
			];
	case EKind::Tool:
	{
		TSharedRef<SVerticalBox> Body = SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight()[ SNew(STextBlock).Text(LOCTEXT("Args", "Arguments")).Font(AgentPanelUIFont(TEXT("Bold"), 8)) ]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 2, 0, 4)[ AgentPanelUIMono(Item->ArgsJson) ]
			+ SVerticalBox::Slot().AutoHeight()[ SNew(STextBlock).Text(LOCTEXT("Result", "Result")).Font(AgentPanelUIFont(TEXT("Bold"), 8)) ]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 2, 0, 0)
			[
				SNew(SBorder).BorderImage(FAppStyle::GetBrush("ToolPanel.DarkGroupBorder")).Padding(4)
				[
					SNew(STextBlock).Font(AgentPanelUIFont(TEXT("Mono"), 8)).AutoWrapText(true)
					.Text_Lambda([Item]()
					{
						if (!Item->bDone)
						{
							return LOCTEXT("Running", "(running)");
						}
						FString R = Item->Result.Left(6000);
						if (Item->bImage)
						{
							R += TEXT("\n[image returned to the model]");
						}
						return FText::FromString(R);
					})
				]
			];
		return SNew(SExpandableArea)
			.InitiallyCollapsed(true)
			.Padding(FMargin(4, 2))
			.HeaderContent()
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 6, 0)
				[
					SNew(STextBlock).Font(AgentPanelUIFont(TEXT("Bold")))
					.Text_Lambda([Item]() { return FText::FromString(!Item->bDone ? TEXT("...") : Item->bDenied ? TEXT("declined") : Item->bOk ? TEXT("ok") : TEXT("error")); })
					.ColorAndOpacity_Lambda([Item]() { return FSlateColor(!Item->bDone ? AgentPanelUIColWarn : Item->bOk ? AgentPanelUIColOk : AgentPanelUIColErr); })
				]
				+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 6, 0)
				[ SNew(STextBlock).Text(FText::FromString(Item->ToolName)).Font(AgentPanelUIFont(TEXT("Mono"), 9)) ]
				+ SHorizontalBox::Slot().FillWidth(1)
				[
					SNew(STextBlock).ColorAndOpacity(AgentPanelUIColDim).Clipping(EWidgetClipping::ClipToBounds)
					.Text_Lambda([Item]()
					{
						if (!Item->bDone)
						{
							return FText::GetEmpty();
						}
						FString First = Item->Result.Left(160).Replace(TEXT("\n"), TEXT(" "));
						return FText::FromString(Item->Ms > 0 ? FString::Printf(TEXT("%d ms  %s"), Item->Ms, *First) : First);
					})
				]
			]
			.BodyContent()[ Body ];
	}
	case EKind::Confirm:
		return SNew(SBorder).BorderImage(FAppStyle::GetBrush("ToolPanel.GroupBorder")).BorderBackgroundColor(AgentPanelUIColWarn).Padding(6)
			[
				SNew(SVerticalBox)
				+ SVerticalBox::Slot().AutoHeight()
				[ SNew(STextBlock).Font(AgentPanelUIFont(TEXT("Bold"))).ColorAndOpacity(AgentPanelUIColWarn).AutoWrapText(true)
					.Text(FText::Format(LOCTEXT("ConfirmTitle", "Confirm {0}: {1}"), FText::FromString(Item->ToolName), FText::FromString(Item->Text))) ]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 4)
				[
					SNew(SExpandableArea).InitiallyCollapsed(true)
					.HeaderContent()[ SNew(STextBlock).Text(LOCTEXT("ConfirmArgs", "Arguments")) ]
					.BodyContent()[ AgentPanelUIMono(Item->ArgsJson) ]
				]
				+ SVerticalBox::Slot().AutoHeight()
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 4, 0)
					[ SNew(SButton).Text(LOCTEXT("Allow", "Allow")).IsEnabled_Lambda([Item]() { return !Item->bDone; })
						.OnClicked_Lambda([Item]() { FMCPAgentClient::Get().AnswerConfirm(Item->ConfirmId, true); return FReply::Handled(); }) ]
					+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 8, 0)
					[ SNew(SButton).Text(LOCTEXT("Deny", "Deny")).IsEnabled_Lambda([Item]() { return !Item->bDone; })
						.OnClicked_Lambda([Item]() { FMCPAgentClient::Get().AnswerConfirm(Item->ConfirmId, false); return FReply::Handled(); }) ]
					+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
					[ SNew(STextBlock).ColorAndOpacity(AgentPanelUIColDim)
						.Text_Lambda([Item]() { return !Item->bDone ? FText::GetEmpty() : Item->bOk ? LOCTEXT("Allowed", "allowed") : LOCTEXT("Denied", "denied"); }) ]
				]
			];
	case EKind::Error:
		return SNew(SBox).Padding(FMargin(6, 1))[ SNew(STextBlock).Text(FText::FromString(Item->Text)).ColorAndOpacity(AgentPanelUIColErr).AutoWrapText(true) ];
	default:
		return SNew(SBox).Padding(FMargin(6, 1))[ SNew(STextBlock).Text(FText::FromString(Item->Text)).ColorAndOpacity(AgentPanelUIColDim).Font(AgentPanelUIFont(TEXT("Italic"))).AutoWrapText(true) ];
	}
}

FReply SMCPAgentPanel::OnSend()
{
	const FString Text = Input->GetText().ToString();
	FString Err;
	if (FMCPAgentClient::Get().SendUserMessage(Text, CurrentOption(SelectedProvider), ModelBox->GetText().ToString(),
		CurrentOption(SelectedSpecialist), Err))
	{
		Input->SetText(FText::GetEmpty());
	}
	else if (!Err.IsEmpty() && Err != TEXT("empty message"))
	{
		UE_LOG(LogTemp, Display, TEXT("UnrealMCP Agent: %s"), *Err);
	}
	return FReply::Handled();
}

FReply SMCPAgentPanel::OnStop()
{
	FMCPAgentClient::Get().StopTurn();
	return FReply::Handled();
}

FReply SMCPAgentPanel::OnUndo()
{
	FMCPAgentClient::Get().UndoLastTurn();
	return FReply::Handled();
}

FReply SMCPAgentPanel::OnNewChat()
{
	FMCPAgentClient::Get().ResetConversation();
	return FReply::Handled();
}

FReply SMCPAgentPanel::OnRunnerButton()
{
	FMCPAgentClient& C = FMCPAgentClient::Get();
	if (C.IsRunnerProcessRunning())
	{
		C.StopRunner();
	}
	else
	{
		FString Err;
		C.StartRunner(FString(), Err);
	}
	return FReply::Handled();
}

FReply SMCPAgentPanel::OnConnect()
{
	FString Err;
	if (!FMCPAgentClient::Get().Connect(Err))
	{
		FMCPAgentClient::Get().OnItemsChanged.Broadcast();
		UE_LOG(LogTemp, Display, TEXT("UnrealMCP Agent: %s"), *Err);
	}
	return FReply::Handled();
}

FText SMCPAgentPanel::RunnerButtonText() const
{
	return FMCPAgentClient::Get().IsRunnerProcessRunning() ? LOCTEXT("StopRunner", "Stop runner") : LOCTEXT("StartRunner", "Start runner");
}

FText SMCPAgentPanel::StatusText() const
{
	return FText::FromString(FMCPAgentClient::Get().GetStatusText());
}

FSlateColor SMCPAgentPanel::StatusColor() const
{
	const FMCPAgentClient& C = FMCPAgentClient::Get();
	if (!C.IsConnected())
	{
		return C.GetStatusState() == TEXT("error") ? AgentPanelUIColErr : AgentPanelUIColDim;
	}
	if (C.IsTurnRunning())
	{
		return AgentPanelUIColWarn;
	}
	return C.GetStatusState() == TEXT("error") ? AgentPanelUIColErr : AgentPanelUIColOk;
}

FText SMCPAgentPanel::HintText() const
{
	FMCPAgentClient& C = FMCPAgentClient::Get();
	if (!C.IsConnected())
	{
		FString Why;
		if (FMCPAgentClient::ResolvePythonDir(&Why).IsEmpty())
		{
			return FText::FromString(Why);
		}
		return C.GetState() == FMCPAgentClient::EState::Disconnected
			? LOCTEXT("HintStart", "Press 'Start runner' (or 'Connect' if you started agent_runner yourself).")
			: FText::GetEmpty();
	}
	const FString Provider = CurrentOption(SelectedProvider);
	for (const TSharedPtr<FJsonObject>& P : C.GetProviders())
	{
		if (P->GetStringField(TEXT("id")) == Provider && !P->GetBoolField(TEXT("configured")))
		{
			return FText::FromString(FString::Printf(TEXT("%s is not configured: %s"), *Provider, *P->GetStringField(TEXT("detail"))));
		}
	}
	return FText::GetEmpty();
}

bool SMCPAgentPanel::CanUndo() const
{
	return !FMCPAgentClient::Get().IsTurnRunning() && FMCPAgentTurns::Get().UndoableSteps() > 0;
}

FText SMCPAgentPanel::UndoTooltip() const
{
	FString Why;
	const int32 N = FMCPAgentTurns::Get().UndoableSteps(&Why);
	return N > 0 ? FText::FromString(FString::Printf(TEXT("Undo the %d editor transaction(s) the last agent turn made"), N)) : FText::FromString(Why);
}

// --------------------------------------------------------------------------------------------------------- tab
namespace MCPAgentPanel
{
const FName TabId(TEXT("UnrealMCPAgent"));

void RegisterTab()
{
	FGlobalTabmanager::Get()->RegisterNomadTabSpawner(TabId, FOnSpawnTab::CreateLambda([](const FSpawnTabArgs&)
		{
			return SNew(SDockTab).TabRole(ETabRole::NomadTab)[ SNew(SMCPAgentPanel) ];
		}))
		.SetDisplayName(LOCTEXT("TabTitle", "UnrealMCP Agent"))
		.SetTooltipText(LOCTEXT("TabTip", "Chat with a local, model-agnostic agent that drives this editor through the UnrealMCP tools"))
		.SetGroup(WorkspaceMenu::GetMenuStructure().GetToolsCategory())
		.SetIcon(FSlateIcon(FAppStyle::GetAppStyleSetName(), "Icons.Help"));
}

void UnregisterTab()
{
	if (FSlateApplication::IsInitialized())
	{
		FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(TabId);
	}
}

bool IsTabRegistered()
{
	return FGlobalTabmanager::Get()->HasTabSpawner(TabId);
}

bool IsTabOpen()
{
	return FGlobalTabmanager::Get()->FindExistingLiveTab(FTabId(TabId)).IsValid();
}

bool OpenTab()
{
	FGlobalTabmanager::Get()->TryInvokeTab(FTabId(TabId));
	return IsTabOpen();
}

void CloseTab()
{
	if (TSharedPtr<SDockTab> Tab = FGlobalTabmanager::Get()->FindExistingLiveTab(FTabId(TabId)))
	{
		Tab->RequestCloseTab();
	}
}
}

#undef LOCTEXT_NAMESPACE
