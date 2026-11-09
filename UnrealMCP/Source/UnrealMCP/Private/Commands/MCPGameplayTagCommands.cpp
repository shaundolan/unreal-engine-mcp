// gameplay_tags: list the project's gameplay tags and add new ones to Config/DefaultGameplayTags.ini,
// refreshing the tag tree so the new tags are usable right away (Python cannot reach either step).
#include "Commands/MCPAuthoringCommands.h"

#include "GameplayTagContainer.h"
#include "GameplayTagsManager.h"
#include "GameplayTagsSettings.h"
#include "Misc/ConfigCacheIni.h"

namespace
{
TSharedPtr<FJsonObject> HandleGameplayTags(const TSharedPtr<FJsonObject>& Params)
{
	UGameplayTagsManager& Manager = UGameplayTagsManager::Get();
	FString Action = TEXT("list"), Prefix;
	Params->TryGetStringField(TEXT("action"), Action);
	Params->TryGetStringField(TEXT("prefix"), Prefix);

	TArray<FString> Added, Existing, Errors;
	if (Action == TEXT("add"))
	{
		const TArray<TSharedPtr<FJsonValue>>* Tags = nullptr;
		Params->TryGetArrayField(TEXT("tags"), Tags);
		FString Comment;
		Params->TryGetStringField(TEXT("comment"), Comment);
		if (!Manager.ShouldImportTagsFromINI())
		{
			return MCPError(TEXT("this project does not import gameplay tags from config (ImportTagsFromConfig is off)"));
		}
		UGameplayTagsList* List = nullptr;
		if (FGameplayTagSource* Source = Manager.FindTagSource(FGameplayTagSource::GetDefaultName()))
		{
			List = Source->SourceTagList;
		}
		if (!List)
		{
			List = GetMutableDefault<UGameplayTagsSettings>();
		}
		for (int32 I = 0; Tags && I < Tags->Num(); ++I)
		{
			FString Tag = (*Tags)[I]->AsString().TrimStartAndEnd(), Fixed;
			FText Why;
			if (!Manager.IsValidGameplayTagString(Tag, &Why, &Fixed))
			{
				Errors.Add(FString::Printf(TEXT("'%s': %s (try '%s')"), *Tag, *Why.ToString(), *Fixed));
				continue;
			}
			if (Manager.IsDictionaryTag(FName(*Tag)))
			{
				Existing.Add(Tag);
				continue;
			}
			List->GameplayTagList.AddUnique(FGameplayTagTableRow(FName(*Tag), Comment));
			Added.Add(Tag);
		}
		if (Added.Num())
		{
			List->SortTags();
			List->TryUpdateDefaultConfigFile(List->ConfigFileName);
			if (!List->ConfigFileName.IsEmpty())
			{
				GConfig->LoadFile(List->ConfigFileName);
			}
			Manager.EditorRefreshGameplayTagTree();
		}
	}
	else if (Action != TEXT("list"))
	{
		return MCPError(TEXT("action must be list or add"));
	}

	FGameplayTagContainer All;
	Manager.RequestAllGameplayTags(All, true);
	TArray<FString> Names;
	for (const FGameplayTag& T : All)
	{
		const FString N = T.ToString();
		if (Prefix.IsEmpty() || N.StartsWith(Prefix)) Names.Add(N);
	}
	Names.Sort();
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("ok"), Errors.Num() == 0);
	Out->SetField(TEXT("tags"), MCPAuthoring::StringArray(Names));
	Out->SetField(TEXT("added"), MCPAuthoring::StringArray(Added));
	Out->SetField(TEXT("already_existed"), MCPAuthoring::StringArray(Existing));
	Out->SetField(TEXT("errors"), MCPAuthoring::StringArray(Errors));
	return Out;
}
} // namespace

void FMCPGameplayTagCommands::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("gameplay_tags"), &HandleGameplayTags, false);
}
