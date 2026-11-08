// widget_export / widget_apply: UMG widget trees as JSON. Graph logic goes through bp_apply.
#include "Commands/MCPAuthoringCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Blueprint/UserWidget.h"
#include "Blueprint/WidgetTree.h"
#include "Components/Border.h"
#include "Components/CanvasPanelSlot.h"
#include "Components/Image.h"
#include "Components/PanelWidget.h"
#include "Components/ProgressBar.h"
#include "Components/TextBlock.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "EditorAssetLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"
#include "WidgetBlueprint.h"

using namespace MCPAuthoring;

static TSharedPtr<FJsonObject> WObj(const TSharedPtr<FJsonObject>& O, const TCHAR* Name)
{
	const TSharedPtr<FJsonObject>* V = nullptr;
	return O->TryGetObjectField(Name, V) ? *V : TSharedPtr<FJsonObject>();
}

static bool ReadVec2(const TSharedPtr<FJsonObject>& O, const TCHAR* Name, FVector2D& Out)
{
	const TArray<TSharedPtr<FJsonValue>>* A = nullptr;
	if (!O->TryGetArrayField(Name, A) || A->Num() < 2) return false;
	Out = FVector2D((*A)[0]->AsNumber(), (*A)[1]->AsNumber());
	return true;
}

static bool ReadColor(const TSharedPtr<FJsonObject>& O, const TCHAR* Name, FLinearColor& Out)
{
	const TArray<TSharedPtr<FJsonValue>>* A = nullptr;
	if (!O->TryGetArrayField(Name, A) || A->Num() < 3) return false;
	Out = FLinearColor((*A)[0]->AsNumber(), (*A)[1]->AsNumber(), (*A)[2]->AsNumber(), A->Num() > 3 ? (*A)[3]->AsNumber() : 1.0);
	return true;
}

static bool AnchorPreset(const FString& In, FAnchors& Out)
{
	const FString S = In.ToLower().Replace(TEXT("-"), TEXT("_"));
	struct FP { const TCHAR* Name; float MinX, MinY, MaxX, MaxY; };
	static const FP Presets[] = {
		{ TEXT("top_left"), 0, 0, 0, 0 }, { TEXT("top_center"), .5f, 0, .5f, 0 }, { TEXT("top_right"), 1, 0, 1, 0 },
		{ TEXT("center_left"), 0, .5f, 0, .5f }, { TEXT("center"), .5f, .5f, .5f, .5f }, { TEXT("center_right"), 1, .5f, 1, .5f },
		{ TEXT("bottom_left"), 0, 1, 0, 1 }, { TEXT("bottom_center"), .5f, 1, .5f, 1 }, { TEXT("bottom_right"), 1, 1, 1, 1 },
		{ TEXT("fill"), 0, 0, 1, 1 }, { TEXT("top_fill"), 0, 0, 1, 0 }, { TEXT("bottom_fill"), 0, 1, 1, 1 },
		{ TEXT("left_fill"), 0, 0, 0, 1 }, { TEXT("right_fill"), 1, 0, 1, 1 },
	};
	for (const FP& P : Presets)
	{
		if (S == P.Name)
		{
			Out = FAnchors(P.MinX, P.MinY, P.MaxX, P.MaxY);
			return true;
		}
	}
	return false;
}

/** Canvas-slot conveniences, then generic reflection for everything else on the slot. */
static void ApplySlot(UPanelSlot* Slot, const TSharedPtr<FJsonObject>& S, TArray<FString>& Errors)
{
	if (!Slot || !S.IsValid()) return;
	Slot->Modify();
	TSharedPtr<FJsonObject> Rest = MakeShared<FJsonObject>(*S);
	if (UCanvasPanelSlot* C = Cast<UCanvasPanelSlot>(Slot))
	{
		FString Preset;
		const TArray<TSharedPtr<FJsonValue>>* A = nullptr;
		if (S->TryGetStringField(TEXT("anchors"), Preset))
		{
			FAnchors An;
			if (AnchorPreset(Preset, An)) C->SetAnchors(An);
			else Errors.Add(FString::Printf(TEXT("anchors: unknown preset '%s' (top_left, top_center, top_right, center, bottom_left, fill, ...)"), *Preset));
		}
		else if (S->TryGetArrayField(TEXT("anchors"), A) && A->Num() == 4)
		{
			C->SetAnchors(FAnchors((*A)[0]->AsNumber(), (*A)[1]->AsNumber(), (*A)[2]->AsNumber(), (*A)[3]->AsNumber()));
		}
		FVector2D V;
		if (ReadVec2(S, TEXT("position"), V)) C->SetPosition(V);
		if (ReadVec2(S, TEXT("size"), V)) C->SetSize(V);
		if (ReadVec2(S, TEXT("alignment"), V)) C->SetAlignment(V);
		bool bAuto = false;
		if (S->TryGetBoolField(TEXT("auto_size"), bAuto)) C->SetAutoSize(bAuto);
		double Z = 0;
		if (S->TryGetNumberField(TEXT("z"), Z)) C->SetZOrder(int32(Z));
		for (const TCHAR* K : { TEXT("anchors"), TEXT("position"), TEXT("size"), TEXT("alignment"), TEXT("auto_size"), TEXT("z") }) Rest->RemoveField(K);
	}
	FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(Slot, Rest, Errors);
}

/** Widget conveniences (text, font_size, color, percent, fill_color), then generic "properties". */
static void ApplyWidgetProps(UWidget* W, const TSharedPtr<FJsonObject>& Op, TArray<FString>& Errors)
{
	W->Modify();
	FString Text;
	FLinearColor Color;
	double Num = 0;
	bool bVar = false;
	if (Op->TryGetBoolField(TEXT("is_variable"), bVar)) W->bIsVariable = bVar;
	if (UTextBlock* T = Cast<UTextBlock>(W))
	{
		if (Op->TryGetStringField(TEXT("text"), Text)) T->SetText(FText::FromString(Text));
		if (Op->TryGetNumberField(TEXT("font_size"), Num))
		{
			FSlateFontInfo Font = T->GetFont();
			Font.Size = int32(Num);
			T->SetFont(Font);
		}
		if (ReadColor(Op, TEXT("color"), Color)) T->SetColorAndOpacity(FSlateColor(Color));
		FString Just;
		if (Op->TryGetStringField(TEXT("justification"), Just))
		{
			T->SetJustification(Just.Equals(TEXT("center"), ESearchCase::IgnoreCase) ? ETextJustify::Center
				: Just.Equals(TEXT("right"), ESearchCase::IgnoreCase) ? ETextJustify::Right : ETextJustify::Left);
		}
	}
	else if (UProgressBar* P = Cast<UProgressBar>(W))
	{
		if (Op->TryGetNumberField(TEXT("percent"), Num)) P->SetPercent(float(Num));
		if (ReadColor(Op, TEXT("fill_color"), Color) || ReadColor(Op, TEXT("color"), Color)) P->SetFillColorAndOpacity(Color);
	}
	else if (UImage* I = Cast<UImage>(W))
	{
		if (ReadColor(Op, TEXT("color"), Color)) I->SetColorAndOpacity(Color);
	}
	else if (UBorder* B = Cast<UBorder>(W))
	{
		if (ReadColor(Op, TEXT("color"), Color)) B->SetBrushColor(Color);
	}
	FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(W, WObj(Op, TEXT("properties")), Errors);
}

static TSharedPtr<FJsonObject> ExportWidget(UWidget* W)
{
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("name"), W->GetName());
	J->SetStringField(TEXT("class"), W->GetClass()->GetName());
	if (W->bIsVariable) J->SetBoolField(TEXT("is_variable"), true);
	if (UTextBlock* T = Cast<UTextBlock>(W))
	{
		J->SetStringField(TEXT("text"), T->GetText().ToString());
		J->SetNumberField(TEXT("font_size"), T->GetFont().Size);
	}
	if (UProgressBar* P = Cast<UProgressBar>(W)) J->SetNumberField(TEXT("percent"), P->GetPercent());
	if (UPanelSlot* Slot = W->Slot)
	{
		TSharedPtr<FJsonObject> SJ = MakeShared<FJsonObject>();
		SJ->SetStringField(TEXT("class"), Slot->GetClass()->GetName());
		if (UCanvasPanelSlot* C = Cast<UCanvasPanelSlot>(Slot))
		{
			const FAnchors An = C->GetAnchors();
			SJ->SetArrayField(TEXT("anchors"), { MakeShared<FJsonValueNumber>(An.Minimum.X), MakeShared<FJsonValueNumber>(An.Minimum.Y),
				MakeShared<FJsonValueNumber>(An.Maximum.X), MakeShared<FJsonValueNumber>(An.Maximum.Y) });
			const FVector2D Pos = C->GetPosition(), Size = C->GetSize(), Al = C->GetAlignment();
			SJ->SetArrayField(TEXT("position"), { MakeShared<FJsonValueNumber>(Pos.X), MakeShared<FJsonValueNumber>(Pos.Y) });
			SJ->SetArrayField(TEXT("size"), { MakeShared<FJsonValueNumber>(Size.X), MakeShared<FJsonValueNumber>(Size.Y) });
			SJ->SetArrayField(TEXT("alignment"), { MakeShared<FJsonValueNumber>(Al.X), MakeShared<FJsonValueNumber>(Al.Y) });
			SJ->SetBoolField(TEXT("auto_size"), C->GetAutoSize());
		}
		else
		{
			FString Err;
			for (const TCHAR* K : { TEXT("Padding"), TEXT("HorizontalAlignment"), TEXT("VerticalAlignment"), TEXT("Size") })
			{
				if (TSharedPtr<FJsonValue> V = FEpicUnrealMCPReflectionCommands::GetPropertyByPath(Slot, K, Err)) SJ->SetField(K, V);
			}
		}
		J->SetObjectField(TEXT("slot"), SJ);
	}
	if (UPanelWidget* Panel = Cast<UPanelWidget>(W))
	{
		TArray<TSharedPtr<FJsonValue>> Kids;
		for (int32 I = 0; I < Panel->GetChildrenCount(); ++I)
		{
			if (UWidget* C = Panel->GetChildAt(I)) Kids.Add(MakeShared<FJsonValueObject>(ExportWidget(C)));
		}
		J->SetArrayField(TEXT("children"), Kids);
	}
	return J;
}

static UWidgetBlueprint* LoadWidgetBP(const FString& Path)
{
	return Cast<UWidgetBlueprint>(LoadAssetOfClass(Path, UWidgetBlueprint::StaticClass()));
}

static TSharedPtr<FJsonObject> HandleWidgetExport(const TSharedPtr<FJsonObject>& Params)
{
	FString Path;
	Params->TryGetStringField(TEXT("widget"), Path);
	UWidgetBlueprint* WBP = LoadWidgetBP(Path);
	if (!WBP)
	{
		return MCPError(FString::Printf(TEXT("widget Blueprint '%s' not found"), *Path));
	}
	TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("path"), WBP->GetPathName());
	J->SetStringField(TEXT("status"), BlueprintStatusName(WBP->Status));
	if (WBP->GeneratedClass) J->SetStringField(TEXT("generated_class"), WBP->GeneratedClass->GetPathName());
	if (WBP->WidgetTree && WBP->WidgetTree->RootWidget)
	{
		J->SetObjectField(TEXT("root"), ExportWidget(WBP->WidgetTree->RootWidget));
	}
	TArray<FString> Vars;
	if (WBP->WidgetTree)
	{
		WBP->WidgetTree->ForEachWidget([&Vars](UWidget* W) { if (W && W->bIsVariable) Vars.Add(W->GetName()); });
	}
	J->SetField(TEXT("variables"), StringArray(Vars));
	return J;
}

static TSharedPtr<FJsonObject> HandleWidgetApply(const TSharedPtr<FJsonObject>& Params)
{
	FString Path;
	Params->TryGetStringField(TEXT("widget"), Path);
	bool bCreate = false, bCompile = true, bSave = true, bAtomic = true;
	Params->TryGetBoolField(TEXT("create"), bCreate);
	Params->TryGetBoolField(TEXT("compile"), bCompile);
	Params->TryGetBoolField(TEXT("save"), bSave);
	Params->TryGetBoolField(TEXT("atomic"), bAtomic);

	UWidgetBlueprint* WBP = LoadWidgetBP(Path);
	bool bCreated = false;
	if (!WBP)
	{
		if (!bCreate)
		{
			return MCPError(FString::Printf(TEXT("widget Blueprint '%s' not found (create=true to make it)"), *Path));
		}
		FString Pkg, Name, Err;
		if (!SplitAssetPath(Path, Pkg, Name, Err))
		{
			return MCPError(Err);
		}
		if (UEditorAssetLibrary::DoesAssetExist(Pkg))
		{
			return MCPError(FString::Printf(TEXT("%s exists but is not a widget Blueprint"), *Pkg));
		}
		UClass* GenClass = FindObject<UClass>(nullptr, TEXT("/Script/UMG.WidgetBlueprintGeneratedClass"));
		UPackage* Package = CreatePackage(*Pkg);
		WBP = Cast<UWidgetBlueprint>(FKismetEditorUtilities::CreateBlueprint(UUserWidget::StaticClass(), Package, FName(*Name),
			BPTYPE_Normal, UWidgetBlueprint::StaticClass(), GenClass, FName(TEXT("UnrealMCP"))));
		if (!WBP)
		{
			return MCPError(TEXT("widget Blueprint creation failed"));
		}
		FAssetRegistryModule::AssetCreated(WBP);
		WBP->MarkPackageDirty();
		bCreated = true;
	}
	UWidgetTree* Tree = WBP->WidgetTree;
	if (!Tree)
	{
		return MCPError(TEXT("widget Blueprint has no widget tree"));
	}

	TArray<TSharedPtr<FJsonValue>> Failed;
	int32 Applied = 0;
	const int32 QueueBefore = (GEditor && GEditor->Trans) ? GEditor->Trans->GetQueueLength() : 0;
	{
		FScopedTransaction Transaction(FText::FromString(FString::Printf(TEXT("MCP: widget_apply %s"), *WBP->GetName())));
		WBP->Modify();
		Tree->Modify();
		const TArray<TSharedPtr<FJsonValue>>* Ops = nullptr;
		if (Params->TryGetArrayField(TEXT("operations"), Ops))
		{
			for (int32 I = 0; I < Ops->Num(); ++I)
			{
				const TSharedPtr<FJsonObject> Op = (*Ops)[I]->AsObject();
				FString Kind, Name, Err;
				Op->TryGetStringField(TEXT("op"), Kind);
				Op->TryGetStringField(TEXT("name"), Name);
				TArray<FString> Errors;
				if (Kind == TEXT("add"))
				{
					FString ClassName, ParentName;
					Op->TryGetStringField(TEXT("class"), ClassName);
					Op->TryGetStringField(TEXT("parent"), ParentName);
					UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(ClassName);
					if (!C || !C->IsChildOf(UWidget::StaticClass()) || C->HasAnyClassFlags(CLASS_Abstract))
					{
						Err = FString::Printf(TEXT("'%s' is not a widget class (CanvasPanel, VerticalBox, HorizontalBox, Overlay, SizeBox, Border, "
							"TextBlock, Image, ProgressBar, Button, Spacer, ...)"), *ClassName);
					}
					else if (Name.IsEmpty() || Tree->FindWidget(FName(*Name)))
					{
						Err = Name.IsEmpty() ? FString(TEXT("add needs a name")) : FString::Printf(TEXT("widget '%s' already exists"), *Name);
					}
					else
					{
						UPanelWidget* Parent = nullptr;
						if (!ParentName.IsEmpty())
						{
							Parent = Cast<UPanelWidget>(Tree->FindWidget(FName(*ParentName)));
							if (!Parent) Err = FString::Printf(TEXT("parent '%s' is not a panel in this tree"), *ParentName);
						}
						else if (Tree->RootWidget)
						{
							Parent = Cast<UPanelWidget>(Tree->RootWidget);
							if (!Parent) Err = TEXT("the root widget is not a panel; pass parent");
						}
						if (Err.IsEmpty())
						{
							UWidget* W = Tree->ConstructWidget<UWidget>(C, FName(*Name));
							if (Parent)
							{
								Parent->Modify();
								UPanelSlot* Slot = Parent->AddChild(W);
								if (!Slot)
								{
									Err = FString::Printf(TEXT("'%s' cannot take another child"), *Parent->GetName());
								}
								else
								{
									ApplySlot(Slot, WObj(Op, TEXT("slot")), Errors);
								}
							}
							else
							{
								Tree->RootWidget = W;
							}
							if (Err.IsEmpty()) ApplyWidgetProps(W, Op, Errors);
						}
					}
				}
				else if (Kind == TEXT("set"))
				{
					if (UWidget* W = Tree->FindWidget(FName(*Name)))
					{
						ApplyWidgetProps(W, Op, Errors);
						if (W->Slot) ApplySlot(W->Slot, WObj(Op, TEXT("slot")), Errors);
					}
					else Err = FString::Printf(TEXT("widget '%s' not found"), *Name);
				}
				else if (Kind == TEXT("remove"))
				{
					if (UWidget* W = Tree->FindWidget(FName(*Name)))
					{
						if (UPanelWidget* P = W->GetParent()) { P->Modify(); P->RemoveChild(W); }
						else if (Tree->RootWidget == W) Tree->RootWidget = nullptr;
						Tree->RemoveWidget(W);
					}
					else Err = FString::Printf(TEXT("widget '%s' not found"), *Name);
				}
				else
				{
					Err = FString::Printf(TEXT("unknown op '%s' (add, set, remove)"), *Kind);
				}
				if (Err.IsEmpty() && Errors.Num()) Err = FString::Join(Errors, TEXT("; "));
				if (Err.IsEmpty())
				{
					++Applied;
				}
				else
				{
					TSharedPtr<FJsonObject> F = MakeShared<FJsonObject>();
					F->SetNumberField(TEXT("index"), I);
					F->SetStringField(TEXT("op"), Kind);
					F->SetStringField(TEXT("error"), Err);
					Failed.Add(MakeShared<FJsonValueObject>(F));
				}
			}
		}
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(WBP);
	}

	TSharedPtr<FJsonObject> Compile = bCompile ? CompileBlueprintWithResults(WBP) : nullptr;
	const bool bCompileOk = !Compile.IsValid() || Compile->GetBoolField(TEXT("ok"));
	const bool bRollback = bAtomic && (Failed.Num() > 0 || !bCompileOk);
	bool bRolledBack = false;
	const FString FinalPath = WBP->GetPathName();
	if (bRollback && bCreated)
	{
		UEditorAssetLibrary::DeleteLoadedAsset(WBP);
		bRolledBack = true;
	}
	else if (bRollback && GEditor && GEditor->Trans && GEditor->Trans->GetQueueLength() > QueueBefore)
	{
		GEditor->UndoTransaction(false);
		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(WBP);
		if (bCompile) CompileBlueprintWithResults(WBP);
		bRolledBack = true;
	}
	bool bSaved = false;
	if (!bRollback && bSave) bSaved = SaveAsset(WBP);

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("ok"), Failed.Num() == 0 && bCompileOk);
	Out->SetStringField(TEXT("widget"), FinalPath);
	Out->SetBoolField(TEXT("created"), bCreated && !bRolledBack);
	Out->SetNumberField(TEXT("applied"), Applied);
	Out->SetArrayField(TEXT("failed"), Failed);
	if (Compile.IsValid()) Out->SetObjectField(TEXT("compile"), Compile);
	Out->SetBoolField(TEXT("rolled_back"), bRolledBack);
	Out->SetBoolField(TEXT("saved"), bSaved);
	return Out;
}

void FMCPWidgetAuthoring::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("widget_export"), &HandleWidgetExport, false);
	Registry.Register(TEXT("widget_apply"), &HandleWidgetApply, false);
}
