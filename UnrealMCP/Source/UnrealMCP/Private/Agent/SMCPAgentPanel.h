#pragma once

#include "CoreMinimal.h"
#include "Widgets/DeclarativeSyntaxSupport.h"
#include "Widgets/SCompoundWidget.h"

class SDockTab;
class SMultiLineEditableTextBox;
class SEditableTextBox;
class SScrollBox;
class SVerticalBox;
template <typename OptionType> class SComboBox;
struct FMCPChatItem;

/** The "UnrealMCP Agent" tab: chat transcript with collapsible tool rows, input, provider/model/specialist pickers. */
class SMCPAgentPanel : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SMCPAgentPanel) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);
	virtual ~SMCPAgentPanel() override;

private:
	void Rebuild();
	TSharedRef<SWidget> MakeRow(const TSharedPtr<FMCPChatItem>& Item);
	TSharedRef<SWidget> MakeCombo(TArray<TSharedPtr<FString>>& Options, TSharedPtr<FString>& Selected, TSharedPtr<SComboBox<TSharedPtr<FString>>>& Out);
	void RefreshOptions();
	FReply OnSend();
	FReply OnStop();
	FReply OnUndo();
	FReply OnNewChat();
	FReply OnRunnerButton();
	FReply OnConnect();
	FText RunnerButtonText() const;
	FText StatusText() const;
	FSlateColor StatusColor() const;
	FText HintText() const;
	FText UndoTooltip() const;
	bool CanUndo() const;
	FString CurrentOption(const TSharedPtr<FString>& Sel) const;

	TSharedPtr<SVerticalBox> Rows;
	TSharedPtr<SScrollBox> Scroll;
	TSharedPtr<SMultiLineEditableTextBox> Input;
	TSharedPtr<SEditableTextBox> ModelBox;
	TArray<TSharedPtr<FString>> ProviderOptions;
	TArray<TSharedPtr<FString>> SpecialistOptions;
	TSharedPtr<FString> SelectedProvider;
	TSharedPtr<FString> SelectedSpecialist;
	TSharedPtr<SComboBox<TSharedPtr<FString>>> ProviderCombo;
	TSharedPtr<SComboBox<TSharedPtr<FString>>> SpecialistCombo;
	FDelegateHandle ChangedHandle;
	int32 BuiltItems = -1;
};

namespace MCPAgentPanel
{
	extern const FName TabId;
	void RegisterTab();
	void UnregisterTab();
	bool IsTabRegistered();
	bool IsTabOpen();
	/** Open (or focus) the tab; returns whether a live tab exists afterwards. */
	bool OpenTab();
	void CloseTab();
}
