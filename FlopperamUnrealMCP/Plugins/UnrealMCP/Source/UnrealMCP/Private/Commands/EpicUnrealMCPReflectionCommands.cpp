#include "Commands/EpicUnrealMCPReflectionCommands.h"
#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "Components/ActorComponent.h"
#include "JsonObjectConverter.h"
#include "UObject/UObjectIterator.h"
#include "UObject/UnrealType.h"
#include "Misc/ScopeExit.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

static constexpr int32 MCP_MAX_DESCRIBE_ITEMS = 600;

// ---------------------------------------------------------------------------------------------
// Resolution helpers
// ---------------------------------------------------------------------------------------------

UWorld* FEpicUnrealMCPReflectionCommands::GetEditorWorld()
{
	return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
}

AActor* FEpicUnrealMCPReflectionCommands::FindActor(const FString& LabelOrName)
{
	UWorld* World = GetEditorWorld();
	if (!World)
	{
		return nullptr;
	}
	AActor* ByName = nullptr;
	for (TActorIterator<AActor> It(World); It; ++It)
	{
		AActor* Actor = *It;
		if (Actor->GetActorLabel().Equals(LabelOrName, ESearchCase::IgnoreCase))
		{
			return Actor;
		}
		if (!ByName && Actor->GetName().Equals(LabelOrName, ESearchCase::IgnoreCase))
		{
			ByName = Actor;
		}
	}
	return ByName;
}

UObject* FEpicUnrealMCPReflectionCommands::ResolveObject(const FString& PathOrLabel, FString& OutError)
{
	if (PathOrLabel.IsEmpty())
	{
		OutError = TEXT("empty object path");
		return nullptr;
	}
	if (PathOrLabel.StartsWith(TEXT("/")))
	{
		if (UObject* Found = StaticFindObject(UObject::StaticClass(), nullptr, *PathOrLabel))
		{
			return Found;
		}
		if (UObject* Loaded = LoadObject<UObject>(nullptr, *PathOrLabel, nullptr, LOAD_NoWarn | LOAD_Quiet))
		{
			return Loaded;
		}
		OutError = FString::Printf(TEXT("no object at path '%s'"), *PathOrLabel);
		return nullptr;
	}
	if (AActor* Actor = FindActor(PathOrLabel))
	{
		return Actor;
	}
	OutError = FString::Printf(TEXT("no actor labelled or named '%s' in the editor level, and not an object path"), *PathOrLabel);
	return nullptr;
}

UClass* FEpicUnrealMCPReflectionCommands::ResolveClass(const FString& NameOrPath)
{
	if (NameOrPath.IsEmpty())
	{
		return nullptr;
	}
	if (NameOrPath.StartsWith(TEXT("/")))
	{
		if (UClass* C = LoadObject<UClass>(nullptr, *NameOrPath, nullptr, LOAD_NoWarn | LOAD_Quiet))
		{
			return C;
		}
		return nullptr;
	}
	FString Short = NameOrPath;
	for (TObjectIterator<UClass> It; It; ++It)
	{
		// Accept "Actor" for AActor, "StaticMeshComponent" for UStaticMeshComponent, etc.
		if (It->GetName().Equals(Short, ESearchCase::IgnoreCase))
		{
			return *It;
		}
	}
	return nullptr;
}

static FProperty* FindPropertyCI(const UStruct* Struct, const FString& Name)
{
	if (FProperty* Exact = Struct->FindPropertyByName(FName(*Name)))
	{
		return Exact;
	}
	for (TFieldIterator<FProperty> It(Struct); It; ++It)
	{
		if (It->GetName().Equals(Name, ESearchCase::IgnoreCase)
#if WITH_EDITORONLY_DATA
			|| It->GetDisplayNameText().ToString().Replace(TEXT(" "), TEXT("")).Equals(Name.Replace(TEXT(" "), TEXT("")), ESearchCase::IgnoreCase)
#endif
			)
		{
			return *It;
		}
	}
	return nullptr;
}

/** Resolved location of a (possibly nested) property. */
struct FMCPPropertyTarget
{
	UObject* Owner = nullptr;            // innermost UObject that contains the leaf (for Modify/PostEditChange)
	FProperty* MemberProperty = nullptr; // first property under Owner on the path (what owners switch on)
	FProperty* Property = nullptr;       // leaf property
	void* ValuePtr = nullptr;      // address of the leaf value
};

/**
 * Walk "A.B.C": struct members, object references, and (on actors) component names.
 * e.g. "RootComponent.RelativeLocation.X", "LightComponent.Intensity", "PointLightComponent0.Intensity".
 */
static bool ResolvePropertyPath(UObject* Root, const FString& Path, FMCPPropertyTarget& Out, FString& OutError)
{
	TArray<FString> Parts;
	Path.ParseIntoArray(Parts, TEXT("."), true);
	if (Parts.Num() == 0)
	{
		OutError = TEXT("empty property path");
		return false;
	}

	UObject* Owner = Root;
	const UStruct* Struct = Root->GetClass();
	void* Container = Root;
	FProperty* Member = nullptr;

	for (int32 i = 0; i < Parts.Num(); ++i)
	{
		const bool bLast = i == Parts.Num() - 1;
		FProperty* Prop = FindPropertyCI(Struct, Parts[i]);

		if (!Prop && Container == Owner)
		{
			// Not a property: on an actor, allow addressing a component by its name.
			if (AActor* Actor = Cast<AActor>(Owner))
			{
				UActorComponent* Found = nullptr;
				for (UActorComponent* Comp : Actor->GetComponents())
				{
					if (Comp && Comp->GetName().Equals(Parts[i], ESearchCase::IgnoreCase))
					{
						Found = Comp;
						break;
					}
				}
				if (Found && !bLast)
				{
					Owner = Found;
					Struct = Found->GetClass();
					Container = Found;
					Member = nullptr;
					continue;
				}
			}
		}
		if (!Prop)
		{
			OutError = FString::Printf(TEXT("'%s' has no property '%s' (use object_describe to list properties)"),
				*Struct->GetName(), *Parts[i]);
			return false;
		}

		void* ValuePtr = Prop->ContainerPtrToValuePtr<void>(Container);
		if (!Member)
		{
			Member = Prop;
		}
		if (bLast)
		{
			Out.Owner = Owner;
			Out.MemberProperty = Member;
			Out.Property = Prop;
			Out.ValuePtr = ValuePtr;
			return true;
		}
		if (FStructProperty* SP = CastField<FStructProperty>(Prop))
		{
			Struct = SP->Struct;
			Container = ValuePtr;
		}
		else if (FObjectPropertyBase* OP = CastField<FObjectPropertyBase>(Prop))
		{
			UObject* Next = OP->GetObjectPropertyValue(ValuePtr);
			if (!Next)
			{
				OutError = FString::Printf(TEXT("'%s' is null"), *Parts[i]);
				return false;
			}
			Owner = Next;
			Struct = Next->GetClass();
			Container = Next;
			Member = nullptr; // a new owner starts a new member chain
		}
		else
		{
			OutError = FString::Printf(TEXT("cannot descend into '%s' (type %s)"), *Parts[i], *Prop->GetCPPType());
			return false;
		}
	}
	return false;
}

static TSharedPtr<FJsonValue> PropertyToJson(FProperty* Prop, const void* ValuePtr)
{
	TSharedPtr<FJsonValue> V = FJsonObjectConverter::UPropertyToJsonValue(Prop, ValuePtr);
	if (V.IsValid())
	{
		return V;
	}
	FString Text;
	Prop->ExportTextItem_Direct(Text, ValuePtr, nullptr, nullptr, PPF_None);
	return MakeShared<FJsonValueString>(Text);
}

/** [1,2,3] for vectors/rotators/colors/points: map array items onto the struct's fields in order. */
static TSharedPtr<FJsonValue> ArrayToStructJson(const TSharedPtr<FJsonValue>& Value, FProperty* Prop)
{
	const FStructProperty* SP = CastField<FStructProperty>(Prop);
	if (!SP || !Value.IsValid() || Value->Type != EJson::Array)
	{
		return nullptr;
	}
	static const TMap<FName, TArray<const TCHAR*>> Fields = {
		{ NAME_Vector, { TEXT("X"), TEXT("Y"), TEXT("Z") } },
		{ NAME_Vector2D, { TEXT("X"), TEXT("Y") } },
		{ NAME_Vector4, { TEXT("X"), TEXT("Y"), TEXT("Z"), TEXT("W") } },
		{ NAME_Rotator, { TEXT("Pitch"), TEXT("Yaw"), TEXT("Roll") } },
		{ NAME_LinearColor, { TEXT("R"), TEXT("G"), TEXT("B"), TEXT("A") } },
		{ NAME_Color, { TEXT("R"), TEXT("G"), TEXT("B"), TEXT("A") } },
		{ NAME_IntPoint, { TEXT("X"), TEXT("Y") } },
		{ FName(TEXT("Margin")), { TEXT("Left"), TEXT("Top"), TEXT("Right"), TEXT("Bottom") } },
	};
	const TArray<const TCHAR*>* Names = Fields.Find(SP->Struct->GetFName());
	const TArray<TSharedPtr<FJsonValue>>& Items = Value->AsArray();
	if (!Names || Items.Num() == 0 || Items.Num() > Names->Num())
	{
		return nullptr;
	}
	TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
	if (SP->Struct->GetFName() == FName(TEXT("Margin")) && Items.Num() == 1)
	{
		for (const TCHAR* N : *Names) Obj->SetField(N, Items[0]); // uniform padding
	}
	else
	{
		for (int32 I = 0; I < Items.Num(); ++I) Obj->SetField((*Names)[I], Items[I]);
		if (SP->Struct->GetFName() == NAME_LinearColor && Items.Num() == 3) Obj->SetNumberField(TEXT("A"), 1.0);
		if (SP->Struct->GetFName() == NAME_Color && Items.Num() == 3) Obj->SetNumberField(TEXT("A"), 255);
	}
	return MakeShared<FJsonValueObject>(Obj);
}

static bool JsonToProperty(const TSharedPtr<FJsonValue>& InValue, FProperty* Prop, void* ValuePtr, UObject* Owner, FString& OutError)
{
	TSharedPtr<FJsonValue> AsStruct = ArrayToStructJson(InValue, Prop);
	// UE 5.4+ "value or blackboard key" structs (BT node params like MoveTo.AcceptableRadius):
	// a plain value sets DefaultValue, {"key": "Name"} binds a blackboard key.
	if (const FStructProperty* SP = CastField<FStructProperty>(Prop); !AsStruct.IsValid() && SP && InValue.IsValid()
		&& SP->Struct->GetName().StartsWith(TEXT("ValueOrBBKey_")))
	{
		TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
		const TSharedPtr<FJsonObject>* In = nullptr;
		FString KeyName;
		if (InValue->TryGetObject(In) && ((*In)->TryGetStringField(TEXT("key"), KeyName) || (*In)->TryGetStringField(TEXT("Key"), KeyName)))
		{
			Obj->SetStringField(TEXT("Key"), KeyName);
		}
		else if (InValue->Type != EJson::Object)
		{
			Obj->SetField(TEXT("DefaultValue"), InValue);
		}
		if (Obj->Values.Num()) AsStruct = MakeShared<FJsonValueObject>(Obj);
	}
	const TSharedPtr<FJsonValue>& Value = AsStruct.IsValid() ? AsStruct : InValue;
	// An object/class path that does not resolve must be an error, not a silent None.
	FString Path;
	if (const FObjectPropertyBase* OP = CastField<FObjectPropertyBase>(Prop); OP && Value.IsValid() && Value->TryGetString(Path)
		&& !Path.IsEmpty() && !Path.Equals(TEXT("None"), ESearchCase::IgnoreCase))
	{
		UObject* Found = nullptr;
		if (const FClassProperty* CP = CastField<FClassProperty>(Prop))
		{
			UClass* C = FEpicUnrealMCPReflectionCommands::ResolveClass(Path);
			Found = (C && C->IsChildOf(CP->MetaClass)) ? C : nullptr;
		}
		else
		{
			Found = StaticLoadObject(OP->PropertyClass, nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		}
		if (!Found)
		{
			OutError = FString::Printf(TEXT("'%s' does not resolve to a %s"), *Path,
				CastField<FClassProperty>(Prop) ? *CastField<FClassProperty>(Prop)->MetaClass->GetName() : *OP->PropertyClass->GetName());
			return false;
		}
		OP->SetObjectPropertyValue(ValuePtr, Found);
		return true;
	}
	if (FJsonObjectConverter::JsonValueToUProperty(Value, Prop, ValuePtr))
	{
		return true;
	}
	// Fallback: Unreal text import ("(X=1,Y=2,Z=3)", "/Game/M_Red.M_Red", enum names, ...).
	FString Text;
	if (Value.IsValid() && Value->TryGetString(Text))
	{
		if (Prop->ImportText_Direct(*Text, ValuePtr, Owner, PPF_None) != nullptr)
		{
			return true;
		}
	}
	OutError = FString::Printf(TEXT("cannot convert value to %s"), *Prop->GetCPPType());
	return false;
}

static TSharedPtr<FJsonObject> DescribeProperty(FProperty* Prop)
{
	TSharedPtr<FJsonObject> P = MakeShared<FJsonObject>();
	P->SetStringField(TEXT("name"), Prop->GetName());
	P->SetStringField(TEXT("type"), Prop->GetCPPType());
#if WITH_EDITORONLY_DATA
	const FString Category = Prop->GetMetaData(TEXT("Category"));
	if (!Category.IsEmpty())
	{
		P->SetStringField(TEXT("category"), Category);
	}
	const FString Tooltip = Prop->GetMetaData(TEXT("ToolTip"));
	if (!Tooltip.IsEmpty())
	{
		P->SetStringField(TEXT("tooltip"), Tooltip.Left(200));
	}
#endif
	P->SetBoolField(TEXT("editable"), Prop->HasAnyPropertyFlags(CPF_Edit) && !Prop->HasAnyPropertyFlags(CPF_EditConst));
	P->SetBoolField(TEXT("blueprint_visible"), Prop->HasAnyPropertyFlags(CPF_BlueprintVisible));
	return P;
}

// ---------------------------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------------------------

static TSharedPtr<FJsonObject> HandleObjectDescribe(const TSharedPtr<FJsonObject>& Params)
{
	FString Path, ClassName, Filter;
	Params->TryGetStringField(TEXT("path"), Path);
	Params->TryGetStringField(TEXT("class"), ClassName);
	Params->TryGetStringField(TEXT("filter"), Filter);
	bool bValues = false, bFunctions = true;
	Params->TryGetBoolField(TEXT("include_values"), bValues);
	Params->TryGetBoolField(TEXT("include_functions"), bFunctions);

	UObject* Object = nullptr;
	UClass* Class = nullptr;
	if (!Path.IsEmpty())
	{
		FString Error;
		Object = FEpicUnrealMCPReflectionCommands::ResolveObject(Path, Error);
		if (!Object)
		{
			return MCPError(Error);
		}
		Class = Cast<UClass>(Object);
		if (Class)
		{
			Object = nullptr; // describing a class asset, not an instance
		}
		else
		{
			Class = Object->GetClass();
		}
	}
	else
	{
		Class = FEpicUnrealMCPReflectionCommands::ResolveClass(ClassName);
		if (!Class)
		{
			return MCPError(FString::Printf(TEXT("class '%s' not found"), *ClassName));
		}
	}

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("class"), Class->GetName());
	Out->SetStringField(TEXT("class_path"), Class->GetPathName());
	if (Object)
	{
		Out->SetStringField(TEXT("path"), Object->GetPathName());
		if (AActor* Actor = Cast<AActor>(Object))
		{
			Out->SetStringField(TEXT("label"), Actor->GetActorLabel());
			TArray<TSharedPtr<FJsonValue>> Comps;
			for (UActorComponent* Comp : Actor->GetComponents())
			{
				if (Comp)
				{
					Comps.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("%s (%s)"), *Comp->GetName(), *Comp->GetClass()->GetName())));
				}
			}
			Out->SetArrayField(TEXT("components"), Comps);
		}
	}
	TArray<TSharedPtr<FJsonValue>> Supers;
	for (UClass* S = Class->GetSuperClass(); S; S = S->GetSuperClass())
	{
		Supers.Add(MakeShared<FJsonValueString>(S->GetName()));
	}
	Out->SetArrayField(TEXT("super_classes"), Supers);

	TArray<TSharedPtr<FJsonValue>> Props;
	for (TFieldIterator<FProperty> It(Class); It && Props.Num() < MCP_MAX_DESCRIBE_ITEMS; ++It)
	{
		FProperty* Prop = *It;
		if (!Filter.IsEmpty() && !Prop->GetName().Contains(Filter))
		{
			continue;
		}
		TSharedPtr<FJsonObject> P = DescribeProperty(Prop);
		if (bValues && Object)
		{
			P->SetField(TEXT("value"), PropertyToJson(Prop, Prop->ContainerPtrToValuePtr<void>(Object)));
		}
		Props.Add(MakeShared<FJsonValueObject>(P));
	}
	Out->SetArrayField(TEXT("properties"), Props);

	if (bFunctions)
	{
		TArray<TSharedPtr<FJsonValue>> Funcs;
		for (TFieldIterator<UFunction> It(Class); It && Funcs.Num() < MCP_MAX_DESCRIBE_ITEMS; ++It)
		{
			UFunction* Fn = *It;
			if (!Filter.IsEmpty() && !Fn->GetName().Contains(Filter))
			{
				continue;
			}
			TSharedPtr<FJsonObject> F = MakeShared<FJsonObject>();
			F->SetStringField(TEXT("name"), Fn->GetName());
			F->SetBoolField(TEXT("static"), Fn->HasAnyFunctionFlags(FUNC_Static));
			F->SetBoolField(TEXT("blueprint_callable"), Fn->HasAnyFunctionFlags(FUNC_BlueprintCallable));
#if WITH_EDITORONLY_DATA
			F->SetBoolField(TEXT("call_in_editor"), Fn->HasMetaData(TEXT("CallInEditor")));
#endif
			TArray<TSharedPtr<FJsonValue>> FnParams;
			for (TFieldIterator<FProperty> PIt(Fn); PIt && PIt->HasAnyPropertyFlags(CPF_Parm); ++PIt)
			{
				TSharedPtr<FJsonObject> P = MakeShared<FJsonObject>();
				P->SetStringField(TEXT("name"), PIt->GetName());
				P->SetStringField(TEXT("type"), PIt->GetCPPType());
				if (PIt->HasAnyPropertyFlags(CPF_ReturnParm))
				{
					P->SetBoolField(TEXT("return"), true);
				}
				else if (PIt->HasAnyPropertyFlags(CPF_OutParm) && !PIt->HasAnyPropertyFlags(CPF_ReferenceParm))
				{
					P->SetBoolField(TEXT("out"), true);
				}
				FnParams.Add(MakeShared<FJsonValueObject>(P));
			}
			F->SetArrayField(TEXT("params"), FnParams);
			Funcs.Add(MakeShared<FJsonValueObject>(F));
		}
		Out->SetArrayField(TEXT("functions"), Funcs);
	}
	return Out;
}

static TSharedPtr<FJsonObject> HandlePropertyGet(const TSharedPtr<FJsonObject>& Params)
{
	FString Path, PropPath, Error;
	Params->TryGetStringField(TEXT("path"), Path);
	Params->TryGetStringField(TEXT("property"), PropPath);
	UObject* Object = FEpicUnrealMCPReflectionCommands::ResolveObject(Path, Error);
	if (!Object)
	{
		return MCPError(Error);
	}
	FMCPPropertyTarget T;
	if (!ResolvePropertyPath(Object, PropPath, T, Error))
	{
		return MCPError(Error);
	}
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("type"), T.Property->GetCPPType());
	Out->SetField(TEXT("value"), PropertyToJson(T.Property, T.ValuePtr));
	return Out;
}

static TSharedPtr<FJsonObject> HandlePropertySet(const TSharedPtr<FJsonObject>& Params)
{
	FString Path, PropPath, Error;
	Params->TryGetStringField(TEXT("path"), Path);
	Params->TryGetStringField(TEXT("property"), PropPath);
	const TSharedPtr<FJsonValue> Value = Params->TryGetField(TEXT("value"));
	if (!Value.IsValid())
	{
		return MCPError(TEXT("missing 'value'"));
	}
	UObject* Object = FEpicUnrealMCPReflectionCommands::ResolveObject(Path, Error);
	if (!Object)
	{
		return MCPError(Error);
	}
	TSharedPtr<FJsonValue> Old, New;
	if (!FEpicUnrealMCPReflectionCommands::SetPropertyByPath(Object, PropPath, Value, Error, &Old, &New))
	{
		return MCPError(Error);
	}
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetField(TEXT("old_value"), Old);
	Out->SetField(TEXT("new_value"), New);
	return Out;
}

bool FEpicUnrealMCPReflectionCommands::SetPropertyByPath(UObject* Root, const FString& PropPath, const TSharedPtr<FJsonValue>& Value,
	FString& Error, TSharedPtr<FJsonValue>* OutOld, TSharedPtr<FJsonValue>* OutNew)
{
	FMCPPropertyTarget T;
	if (!Root || !ResolvePropertyPath(Root, PropPath, T, Error))
	{
		if (!Root) Error = TEXT("null object");
		return false;
	}
	if (T.Property->HasAnyPropertyFlags(CPF_EditConst))
	{
		UE_LOG(LogUnrealMCP, Warning, TEXT("set property: %s is EditConst; writing anyway"), *PropPath);
	}
	if (OutOld)
	{
		*OutOld = PropertyToJson(T.Property, T.ValuePtr);
	}
	// Convert into a scratch value first, so a bad value never leaves a partial write or an
	// unbalanced PreEditChange (components unregister in PreEditChange, re-register in Post).
	void* Scratch = FMemory::Malloc(FMath::Max(T.Property->GetSize(), 1), T.Property->GetMinAlignment());
	T.Property->InitializeValue(Scratch);
	ON_SCOPE_EXIT { T.Property->DestroyValue(Scratch); FMemory::Free(Scratch); };
	T.Property->CopyCompleteValue(Scratch, T.ValuePtr); // start from current (partial struct JSON keeps other fields)
	if (!JsonToProperty(Value, T.Property, Scratch, T.Owner, Error))
	{
		return false;
	}
	T.Owner->Modify();
#if WITH_EDITOR
	T.Owner->PreEditChange(T.MemberProperty);
#endif
	T.Property->CopyCompleteValue(T.ValuePtr, Scratch);
#if WITH_EDITOR
	FPropertyChangedEvent Changed(T.Property, EPropertyChangeType::ValueSet);
	Changed.SetActiveMemberProperty(T.MemberProperty);
	T.Owner->PostEditChangeProperty(Changed);
#endif
	if (AActor* Actor = Cast<AActor>(Root))
	{
		Actor->MarkComponentsRenderStateDirty();
	}
	if (OutNew)
	{
		*OutNew = PropertyToJson(T.Property, T.ValuePtr);
	}
	return true;
}

int32 FEpicUnrealMCPReflectionCommands::SetPropertiesByPath(UObject* Root, const TSharedPtr<FJsonObject>& Props, TArray<FString>& OutErrors)
{
	int32 Set = 0;
	if (!Props.IsValid())
	{
		return 0;
	}
	for (const auto& Pair : Props->Values)
	{
		const FString Key(*Pair.Key);
		FString Err;
		if (SetPropertyByPath(Root, Key, Pair.Value, Err))
		{
			++Set;
		}
		else
		{
			OutErrors.Add(FString::Printf(TEXT("%s: %s"), *Key, *Err));
		}
	}
	return Set;
}

TSharedPtr<FJsonValue> FEpicUnrealMCPReflectionCommands::GetPropertyByPath(UObject* Root, const FString& PropPath, FString& Error)
{
	FMCPPropertyTarget T;
	if (!Root || !ResolvePropertyPath(Root, PropPath, T, Error))
	{
		return nullptr;
	}
	return PropertyToJson(T.Property, T.ValuePtr);
}

static TSharedPtr<FJsonObject> HandleFunctionCall(const TSharedPtr<FJsonObject>& Params)
{
	FString Path, FnName, Error;
	Params->TryGetStringField(TEXT("path"), Path);
	Params->TryGetStringField(TEXT("function"), FnName);
	const TSharedPtr<FJsonObject>* ArgsPtr = nullptr;
	const TSharedPtr<FJsonObject> Args = Params->TryGetObjectField(TEXT("args"), ArgsPtr) && ArgsPtr ? *ArgsPtr : MakeShared<FJsonObject>();

	UObject* Object = FEpicUnrealMCPReflectionCommands::ResolveObject(Path, Error);
	if (!Object)
	{
		return MCPError(Error);
	}
	if (UClass* AsClass = Cast<UClass>(Object))
	{
		Object = AsClass->GetDefaultObject(); // allow passing a class for static functions
	}
	UFunction* Fn = Object->FindFunction(FName(*FnName));
	if (!Fn)
	{
		for (TFieldIterator<UFunction> It(Object->GetClass()); It; ++It)
		{
			if (It->GetName().Equals(FnName, ESearchCase::IgnoreCase))
			{
				Fn = *It;
				break;
			}
		}
	}
	if (!Fn)
	{
		return MCPError(FString::Printf(TEXT("%s has no function '%s' (use object_describe)"), *Object->GetClass()->GetName(), *FnName));
	}

	// InitializeStruct/DestroyStruct touch GetStructureSize() bytes (params + locals + padding),
	// which can exceed ParmsSize; size the buffer for the whole struct.
	TArray<uint8> Buffer;
	Buffer.SetNumZeroed(FMath::Max<int32>(Fn->GetStructureSize(), 1));
	Fn->InitializeStruct(Buffer.GetData());
	ON_SCOPE_EXIT { Fn->DestroyStruct(Buffer.GetData()); };

	for (TFieldIterator<FProperty> It(Fn); It && It->HasAnyPropertyFlags(CPF_Parm); ++It)
	{
		FProperty* P = *It;
		if (P->HasAnyPropertyFlags(CPF_ReturnParm))
		{
			continue;
		}
		TSharedPtr<FJsonValue> ArgValue;
		for (const auto& Pair : Args->Values)
		{
			// *Key is const TCHAR* for both FString (<=5.7) and UE::TSharedString (5.8+) keys
			if (FCString::Stricmp(*Pair.Key, *P->GetName()) == 0)
			{
				ArgValue = Pair.Value;
				break;
			}
		}
		if (!ArgValue.IsValid())
		{
			// World-context parameters are filled automatically.
			if (FObjectProperty* OP = CastField<FObjectProperty>(P))
			{
#if WITH_EDITORONLY_DATA
				if (Fn->GetMetaData(TEXT("WorldContext")) == P->GetName() || OP->PropertyClass == UWorld::StaticClass())
#else
				if (OP->PropertyClass == UWorld::StaticClass())
#endif
				{
					OP->SetObjectPropertyValue(P->ContainerPtrToValuePtr<void>(Buffer.GetData()), FEpicUnrealMCPReflectionCommands::GetEditorWorld());
				}
			}
			continue; // leave default
		}
		if (!JsonToProperty(ArgValue, P, P->ContainerPtrToValuePtr<void>(Buffer.GetData()), Object, Error))
		{
			return MCPError(FString::Printf(TEXT("arg '%s': %s"), *P->GetName(), *Error));
		}
	}

	Object->Modify();
	Object->ProcessEvent(Fn, Buffer.GetData());

	TSharedPtr<FJsonObject> Outs = MakeShared<FJsonObject>();
	TSharedPtr<FJsonValue> Return;
	for (TFieldIterator<FProperty> It(Fn); It && It->HasAnyPropertyFlags(CPF_Parm); ++It)
	{
		FProperty* P = *It;
		const void* V = P->ContainerPtrToValuePtr<void>(Buffer.GetData());
		if (P->HasAnyPropertyFlags(CPF_ReturnParm))
		{
			Return = PropertyToJson(P, V);
		}
		else if (P->HasAnyPropertyFlags(CPF_OutParm) && !P->HasAnyPropertyFlags(CPF_ConstParm))
		{
			Outs->SetField(P->GetName(), PropertyToJson(P, V));
		}
	}
	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	if (Return.IsValid())
	{
		Out->SetField(TEXT("return_value"), Return);
	}
	Out->SetObjectField(TEXT("out_params"), Outs);
	return Out;
}

void FEpicUnrealMCPReflectionCommands::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("object_describe"), &HandleObjectDescribe, false);
	Registry.Register(TEXT("property_get"), &HandlePropertyGet, false);
	Registry.Register(TEXT("property_set"), &HandlePropertySet, true);
	Registry.Register(TEXT("function_call"), &HandleFunctionCall, true);
}
