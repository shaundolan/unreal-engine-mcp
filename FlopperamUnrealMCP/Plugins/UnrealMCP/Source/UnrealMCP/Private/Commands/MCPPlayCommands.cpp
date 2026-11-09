// pie_input / frame_stats: drive and measure a running PIE session from the MCP server.
//
// Both commands only schedule work and return. A core ticker applies injections every frame and
// samples frame timings, so the game thread is never blocked while PIE runs (the server sleeps
// between short commands, as pie_test already does).
#include "Commands/MCPPlayCommands.h"
#include "Commands/EpicUnrealMCPReflectionCommands.h"

#include "Containers/Ticker.h"
#include "EngineUtils.h"
#include "Engine/Blueprint.h"
#include "Engine/GameViewportClient.h"
#include "IAssetViewport.h"
#include "LevelEditor.h"
#include "Modules/ModuleManager.h"
#include "PlayInEditorDataTypes.h"
#include "Settings/LevelEditorPlaySettings.h"
#include "Slate/SceneViewport.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "DynamicRHI.h"
#include "Editor.h"
#include "Editor/EditorEngine.h"
#include "EnhancedPlayerInput.h"
#include "Engine/LocalPlayer.h"
#include "Engine/World.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/PlayerInput.h"
#include "HAL/PlatformTime.h"
#include "InputAction.h"
#include "InputCoreTypes.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/App.h"
#include "Misc/PackageName.h"
#include "UObject/SoftObjectPath.h"
#include "RenderTimer.h"       // GGameThreadTime & co.
#include "InputKeyEventArgs.h"

namespace
{
	struct FInjection
	{
		int32 Id = 0;
		FString Label;
		TWeakObjectPtr<APlayerController> PC;
		FKey Key;                                   // raw key; invalid for actions
		TWeakObjectPtr<const UInputAction> Action;  // Enhanced Input action
		FVector Value = FVector(1, 0, 0);
		bool bAxis = false;      // raw key: an IE_Axis event every frame (mouse, sticks) instead of press ... release
		double EndTime = 0;
		int32 MinFrames = 2;     // a press must be seen by at least this many frames, however slow they are
		int32 Frames = 0;
		bool bPressed = false;
	};

	struct FRecording
	{
		int32 Wanted = 0;
		bool bActive = false;
		bool bSkipFirst = true;  // the frame in which start ran is partial
		double StartTime = 0;
		TArray<float> FrameMs, GameMs, RenderMs, RhiMs, GpuMs;
		bool bPie = false;
	};

	TArray<FInjection> GInjections;
	FRecording GRecording;
	int32 GNextId = 1;
	FTSTicker::FDelegateHandle GTickHandle;
	FDelegateHandle GThrottleHandle;

	bool IsBusy() { return GInjections.Num() > 0 || GRecording.bActive; }

	void SendKey(APlayerController* PC, const FKey& Key, EInputEvent Event, float Amount)
	{
		PC->InputKey(FInputKeyEventArgs::CreateSimulated(Key, Event, Amount));
	}

	void Release(FInjection& J)
	{
		APlayerController* PC = J.PC.Get();
		if (!PC || !J.Key.IsValid()) return;
		if (J.bAxis)
		{
			SendKey(PC, J.Key, IE_Axis, 0.f); // sticks keep their last value otherwise
		}
		else if (J.bPressed)
		{
			SendKey(PC, J.Key, IE_Released, 0.f);
		}
		J.bPressed = false;
	}

	void Apply(FInjection& J)
	{
		APlayerController* PC = J.PC.Get();
		if (!PC) return;
		if (const UInputAction* Action = J.Action.Get())
		{
			if (UEnhancedPlayerInput* EPI = Cast<UEnhancedPlayerInput>(PC->PlayerInput))
			{
				EPI->InjectInputForAction(Action, FInputActionValue(Action->ValueType, J.Value));
			}
			return;
		}
		if (J.bAxis)
		{
			SendKey(PC, J.Key, IE_Axis, float(J.Value.X));
		}
		else if (!J.bPressed)
		{
			SendKey(PC, J.Key, IE_Pressed, 1.f);
			J.bPressed = true;
		}
	}

	float Percentile(TArray<float> V, float P)
	{
		if (V.Num() == 0) return 0.f;
		V.Sort();
		const int32 Idx = FMath::Clamp(FMath::CeilToInt(P * V.Num()) - 1, 0, V.Num() - 1);
		return V[Idx];
	}

	TSharedPtr<FJsonObject> Summary(const TArray<float>& V)
	{
		TSharedPtr<FJsonObject> O = MakeShared<FJsonObject>();
		if (V.Num() == 0) return O;
		double Sum = 0;
		float Max = V[0], Min = V[0];
		for (float X : V) { Sum += X; Max = FMath::Max(Max, X); Min = FMath::Min(Min, X); }
		auto R = [](double X) { return FMath::RoundToDouble(X * 100.0) / 100.0; };
		O->SetNumberField(TEXT("avg"), R(Sum / V.Num()));
		O->SetNumberField(TEXT("p95"), R(Percentile(V, 0.95f)));
		O->SetNumberField(TEXT("p99"), R(Percentile(V, 0.99f)));
		O->SetNumberField(TEXT("min"), R(Min));
		O->SetNumberField(TEXT("max"), R(Max));
		return O;
	}

	bool Tick(float)
	{
		const double Now = FPlatformTime::Seconds();
		const bool bPie = GEditor && GEditor->PlayWorld;
		for (int32 I = GInjections.Num() - 1; I >= 0; --I)
		{
			FInjection& J = GInjections[I];
			const bool bDone = !bPie || !J.PC.IsValid() || (Now >= J.EndTime && J.Frames >= J.MinFrames);
			if (bDone)
			{
				Release(J);
				GInjections.RemoveAt(I);
				continue;
			}
			Apply(J);
			++J.Frames;
		}

		if (GRecording.bActive)
		{
			if (GRecording.bSkipFirst)
			{
				GRecording.bSkipFirst = false;
			}
			else
			{
				GRecording.FrameMs.Add(float(FApp::GetDeltaTime() * 1000.0));
				GRecording.GameMs.Add(float(FPlatformTime::ToMilliseconds(GGameThreadTime)));
				GRecording.RenderMs.Add(float(FPlatformTime::ToMilliseconds(GRenderThreadTime)));
				GRecording.RhiMs.Add(float(FPlatformTime::ToMilliseconds(GRHIThreadTime)));
				GRecording.GpuMs.Add(float(FPlatformTime::ToMilliseconds64(RHIGetGPUFrameCycles(0))));
				if (GRecording.FrameMs.Num() >= GRecording.Wanted) GRecording.bActive = false;
			}
		}

		if (!IsBusy())
		{
			GTickHandle.Reset();
			return false; // removes the ticker
		}
		return true;
	}

	void EnsureTicking()
	{
		if (!GTickHandle.IsValid())
		{
			GTickHandle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateStatic(&Tick), 0.0f);
		}
		// A background editor throttles to a few frames per second ("Use Less CPU when in Background"),
		// which would stretch every held key and poison frame timings. Not while we drive or measure.
		if (!GThrottleHandle.IsValid() && GEditor)
		{
			UEditorEngine::FShouldDisableCPUThrottling D = UEditorEngine::FShouldDisableCPUThrottling::CreateStatic(&IsBusy);
			GThrottleHandle = D.GetHandle();
			GEditor->ShouldDisableCPUThrottlingDelegates.Add(MoveTemp(D));
		}
	}

	APlayerController* PiePlayer(int32 Index, FString& Err)
	{
		UWorld* World = GEditor ? GEditor->PlayWorld.Get() : nullptr;
		if (!World)
		{
			Err = TEXT("PIE is not running (start it with editor_action pie_start, or use pie_test steps)");
			return nullptr;
		}
		APlayerController* PC = UGameplayStatics::GetPlayerController(World, Index);
		if (!PC)
		{
			Err = FString::Printf(TEXT("no player controller %d in the PIE world"), Index);
		}
		return PC;
	}

	FVector ParseValue(const TSharedPtr<FJsonObject>& P, bool& bGiven)
	{
		bGiven = false;
		const TSharedPtr<FJsonValue> V = P->TryGetField(TEXT("value"));
		if (!V.IsValid() || V->IsNull()) return FVector(1, 0, 0);
		bGiven = true;
		if (V->Type == EJson::Array)
		{
			const TArray<TSharedPtr<FJsonValue>>& A = V->AsArray();
			return FVector(A.Num() > 0 ? A[0]->AsNumber() : 0, A.Num() > 1 ? A[1]->AsNumber() : 0, A.Num() > 2 ? A[2]->AsNumber() : 0);
		}
		if (V->Type == EJson::Boolean) return FVector(V->AsBool() ? 1 : 0, 0, 0);
		return FVector(V->AsNumber(), 0, 0);
	}

	TSharedPtr<FJsonObject> HandlePieInput(const TSharedPtr<FJsonObject>& P)
	{
		FString Mode = TEXT("press");
		P->TryGetStringField(TEXT("mode"), Mode);
		Mode = Mode.ToLower();

		if (Mode == TEXT("release_all") || Mode == TEXT("stop"))
		{
			const int32 N = GInjections.Num();
			for (FInjection& J : GInjections) Release(J);
			GInjections.Reset();
			TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
			Out->SetNumberField(TEXT("released"), N);
			return Out;
		}
		if (Mode != TEXT("press") && Mode != TEXT("hold") && Mode != TEXT("axis"))
		{
			return MCPError(FString::Printf(TEXT("unknown mode '%s' (press, hold, axis, release_all)"), *Mode));
		}

		int32 PlayerIndex = 0;
		P->TryGetNumberField(TEXT("player"), PlayerIndex);
		FString Err;
		APlayerController* PC = PiePlayer(PlayerIndex, Err);
		if (!PC) return MCPError(Err);

		FInjection J;
		J.PC = PC;
		bool bValueGiven = false;
		J.Value = ParseValue(P, bValueGiven);
		double Duration = Mode == TEXT("press") ? 0.1 : 1.0;
		P->TryGetNumberField(TEXT("duration_s"), Duration);
		Duration = FMath::Clamp(Duration, 0.0, 600.0);
		P->TryGetNumberField(TEXT("min_frames"), J.MinFrames);
		J.MinFrames = FMath::Max(1, J.MinFrames);

		FString ActionPath, KeyName;
		P->TryGetStringField(TEXT("action"), ActionPath);
		P->TryGetStringField(TEXT("key"), KeyName);
		if (!ActionPath.IsEmpty())
		{
			FString ObjPath = ActionPath;
			if (!ObjPath.Contains(TEXT(".")))
			{
				ObjPath += TEXT(".") + FPackageName::GetShortName(ObjPath); // /Game/Input/IA_Jump -> .IA_Jump
			}
			const UInputAction* Action = LoadObject<UInputAction>(nullptr, *ObjPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Action)
			{
				return MCPError(FString::Printf(TEXT("InputAction '%s' not found"), *ActionPath));
			}
			if (!Cast<UEnhancedPlayerInput>(PC->PlayerInput))
			{
				return MCPError(TEXT("the PIE player uses legacy input (PlayerInput is not EnhancedPlayerInput); inject raw keys with \"key\" instead"));
			}
			J.Action = Action;
			J.Label = Action->GetPathName();
		}
		else if (!KeyName.IsEmpty())
		{
			J.Key = FKey(FName(*KeyName));
			if (!J.Key.IsValid())
			{
				return MCPError(FString::Printf(TEXT("unknown key '%s' (FKey names: SpaceBar, W, LeftShift, LeftMouseButton, MouseX, MouseY, "
					"Gamepad_LeftX, Gamepad_FaceButton_Bottom, ...)"), *KeyName));
			}
			// Analog keys (mouse deltas, sticks, triggers) take a value every frame; digital keys go down then up.
			J.bAxis = Mode == TEXT("axis") || (J.Key.IsAxis1D() && !J.Key.IsDigital());
			if (J.bAxis && !J.Key.IsAxis1D())
			{
				return MCPError(FString::Printf(TEXT("'%s' is not a 1D axis key; use mode press/hold, or MouseX / MouseY / Gamepad_LeftX ..."), *KeyName));
			}
			J.Label = J.Key.GetFName().ToString();
		}
		else
		{
			return MCPError(TEXT("pie_input needs \"key\" (an FKey name such as SpaceBar) or \"action\" (an InputAction asset path)"));
		}

		J.Id = GNextId++;
		J.EndTime = FPlatformTime::Seconds() + Duration;
		// Apply now so the input lands in the very next frame even if this command arrived mid-frame.
		Apply(J);
		J.Frames = 0;
		GInjections.Add(J);
		EnsureTicking();

		TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
		Out->SetNumberField(TEXT("id"), J.Id);
		Out->SetStringField(J.Action.IsValid() ? TEXT("action") : TEXT("key"), J.Label);
		Out->SetStringField(TEXT("mode"), Mode);
		Out->SetBoolField(TEXT("per_frame"), J.Action.IsValid() || J.bAxis);
		TArray<TSharedPtr<FJsonValue>> Val;
		for (int32 I = 0; I < 3; ++I) Val.Add(MakeShared<FJsonValueNumber>(J.Value[I]));
		Out->SetArrayField(TEXT("value"), Val);
		Out->SetNumberField(TEXT("duration_s"), Duration);
		if (APawn* Pawn = PC->GetPawn()) Out->SetStringField(TEXT("pawn"), Pawn->GetName());
		Out->SetNumberField(TEXT("active"), GInjections.Num());
		return Out;
	}

	TSharedPtr<FJsonObject> HandleFrameStats(const TSharedPtr<FJsonObject>& P)
	{
		FString Action = TEXT("read");
		P->TryGetStringField(TEXT("action"), Action);
		Action = Action.ToLower();
		if (Action == TEXT("start"))
		{
			int32 Frames = 120;
			P->TryGetNumberField(TEXT("frames"), Frames);
			GRecording = FRecording();
			GRecording.Wanted = FMath::Clamp(Frames, 1, 20000);
			GRecording.bActive = true;
			GRecording.StartTime = FPlatformTime::Seconds();
			GRecording.bPie = GEditor && GEditor->PlayWorld;
			EnsureTicking();
			TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
			Out->SetNumberField(TEXT("frames"), GRecording.Wanted);
			Out->SetBoolField(TEXT("pie"), GRecording.bPie);
			return Out;
		}
		if (Action == TEXT("stop"))
		{
			GRecording.bActive = false;
		}
		else if (Action != TEXT("read"))
		{
			return MCPError(FString::Printf(TEXT("unknown action '%s' (start, read, stop)"), *Action));
		}
		TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
		const FRecording& R = GRecording;
		Out->SetBoolField(TEXT("done"), !R.bActive);
		Out->SetNumberField(TEXT("frames"), R.FrameMs.Num());
		Out->SetNumberField(TEXT("wanted"), R.Wanted);
		Out->SetBoolField(TEXT("pie"), R.bPie);
		if (R.FrameMs.Num() > 0)
		{
			TSharedPtr<FJsonObject> Frame = Summary(R.FrameMs);
			Out->SetObjectField(TEXT("frame_ms"), Frame);
			const double Avg = Frame->GetNumberField(TEXT("avg"));
			Out->SetNumberField(TEXT("fps_avg"), Avg > 0 ? FMath::RoundToDouble(10000.0 / Avg) / 10.0 : 0.0);
			// GRenderThreadTime & co. are filled by the Slate renderer when it presents a window; with
			// -RenderOffscreen some stay 0. Report those as unavailable rather than as a perfect 0 ms.
			TArray<TSharedPtr<FJsonValue>> Unavailable;
			auto Put = [&](const TCHAR* Name, const TArray<float>& V)
			{
				const bool bAllZero = !V.ContainsByPredicate([](float X) { return X > 0.f; });
				if (bAllZero)
				{
					Out->SetField(Name, MakeShared<FJsonValueNull>());
					Unavailable.Add(MakeShared<FJsonValueString>(Name));
				}
				else
				{
					Out->SetObjectField(Name, Summary(V));
				}
			};
			Put(TEXT("game_ms"), R.GameMs);
			Put(TEXT("render_ms"), R.RenderMs);
			Put(TEXT("rhi_ms"), R.RhiMs);
			Put(TEXT("gpu_ms"), R.GpuMs);
			if (Unavailable.Num()) Out->SetArrayField(TEXT("unavailable"), Unavailable);
			Out->SetNumberField(TEXT("elapsed_s"), FMath::RoundToDouble((FPlatformTime::Seconds() - R.StartTime) * 100.0) / 100.0);
			Out->SetStringField(TEXT("rhi"), GDynamicRHI ? FString(GDynamicRHI->GetName()) : FString());
		}
		return Out;
	}

	// ---------------------------------------------------------------------------------------------
	// PIE viewport size, spawning and possession
	// ---------------------------------------------------------------------------------------------

	FIntPoint GPendingPieSize(0, 0);   // applied to the PIE viewport once PIE has started
	FIntPoint GSavedWindowSize(0, 0);  // NewWindowWidth/Height before a session we started, restored when it ends
	FDelegateHandle GPostPieStartedHandle;
	FDelegateHandle GShutdownPieHandle;

	/** EndPlayMap saves the PIE window's last client size as the next NewWindowWidth/Height (minus the title bar, so
	 *  every session shrinks it: 720 -> 686 -> 680 ... -> 18). Put back what our session started with. */
	void OnShutdownPie(bool /*bSimulating*/)
	{
		if (GSavedWindowSize.X > 0)
		{
			ULevelEditorPlaySettings* Defaults = GetMutableDefault<ULevelEditorPlaySettings>();
			if (Defaults->NewWindowWidth != GSavedWindowSize.X || Defaults->NewWindowHeight != GSavedWindowSize.Y)
			{
				Defaults->NewWindowWidth = GSavedWindowSize.X;
				Defaults->NewWindowHeight = GSavedWindowSize.Y;
				Defaults->SaveConfig();
			}
		}
		GSavedWindowSize = FIntPoint(0, 0);
	}

	TArray<TSharedPtr<FJsonValue>> Vec3(const FVector& V)
	{
		return { MakeShared<FJsonValueNumber>(V.X), MakeShared<FJsonValueNumber>(V.Y), MakeShared<FJsonValueNumber>(V.Z) };
	}

	FSceneViewport* PieSceneViewport()
	{
		UWorld* World = GEditor ? GEditor->PlayWorld.Get() : nullptr;
		UGameViewportClient* GVC = World ? World->GetGameViewport() : nullptr;
		return GVC ? GVC->GetGameViewport() : nullptr;
	}

	void OnPostPieStarted(bool /*bSimulating*/)
	{
		if (GPendingPieSize.X > 0 && GPendingPieSize.Y > 0)
		{
			FString Err;
			FMCPPlayCommands::SetPieViewportSize(GPendingPieSize.X, GPendingPieSize.Y, Err);
		}
		GPendingPieSize = FIntPoint(0, 0);
	}

	bool ReadVector(const TSharedPtr<FJsonObject>& P, const TCHAR* Field, FVector& Out)
	{
		const TArray<TSharedPtr<FJsonValue>>* A = nullptr;
		if (P->TryGetArrayField(Field, A) && A && A->Num() >= 3)
		{
			Out = FVector((*A)[0]->AsNumber(), (*A)[1]->AsNumber(), (*A)[2]->AsNumber());
			return true;
		}
		double N = 0;
		if (P->TryGetNumberField(Field, N))
		{
			Out = FVector(N);
			return true;
		}
		return false;
	}

	/** "/Game/BP_Car" (Blueprint asset), "/Game/BP_Car.BP_Car_C", "/Script/Engine.StaticMeshActor" or "StaticMeshActor". */
	UClass* ResolveActorClass(const FString& Name, FString& Err)
	{
		UClass* Class = nullptr;
		if (Name.StartsWith(TEXT("/")))
		{
			FString Path = Name;
			if (!Path.Contains(TEXT(".")))
			{
				Path += TEXT(".") + FPackageName::GetShortName(Path);
			}
			UObject* Obj = LoadObject<UObject>(nullptr, *Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
			if (!Obj && !Path.EndsWith(TEXT("_C")))
			{
				Obj = LoadObject<UObject>(nullptr, *(Path + TEXT("_C")), nullptr, LOAD_NoWarn | LOAD_Quiet);
			}
			if (UBlueprint* BP = Cast<UBlueprint>(Obj))
			{
				Class = BP->GeneratedClass;
			}
			else
			{
				Class = Cast<UClass>(Obj);
			}
		}
		else
		{
			Class = FEpicUnrealMCPReflectionCommands::ResolveClass(Name);
		}
		if (!Class)
		{
			Err = FString::Printf(TEXT("class '%s' not found (a Blueprint asset path /Game/BP_X, a class path /Script/Engine.StaticMeshActor "
				"or a native class name)"), *Name);
			return nullptr;
		}
		if (!Class->IsChildOf(AActor::StaticClass()))
		{
			Err = FString::Printf(TEXT("%s is not an Actor class"), *Class->GetPathName());
			return nullptr;
		}
		if (Class->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated | CLASS_NewerVersionExists))
		{
			Err = FString::Printf(TEXT("%s is abstract or deprecated (a stale Blueprint class? recompile it)"), *Class->GetPathName());
			return nullptr;
		}
		return Class;
	}

	AActor* FindPieActor(UWorld* World, const FString& LabelOrName)
	{
		AActor* ByName = nullptr;
		for (TActorIterator<AActor> It(World); It; ++It)
		{
#if WITH_EDITOR
			if (It->GetActorLabel().Equals(LabelOrName, ESearchCase::IgnoreCase))
			{
				return *It;
			}
#endif
			if (!ByName && It->GetName().Equals(LabelOrName, ESearchCase::IgnoreCase))
			{
				ByName = *It;
			}
		}
		return ByName;
	}

	bool Possess(UWorld* World, int32 PlayerIndex, APawn* Pawn, bool bDestroyPrevious, TSharedPtr<FJsonObject>& Out, FString& Err)
	{
		APlayerController* PC = UGameplayStatics::GetPlayerController(World, PlayerIndex);
		if (!PC)
		{
			Err = FString::Printf(TEXT("no player controller %d in the PIE world"), PlayerIndex);
			return false;
		}
		APawn* Previous = PC->GetPawn();
		if (Previous == Pawn)
		{
			Out->SetBoolField(TEXT("possessed"), true);
			Out->SetStringField(TEXT("previous_pawn"), Previous->GetName());
			return true;
		}
		PC->Possess(Pawn); // unpossesses the PC's old pawn and takes the new one from its AI controller, if any
		if (PC->GetPawn() != Pawn)
		{
			Err = FString::Printf(TEXT("%s did not possess %s (the pawn may refuse possession, e.g. bCanBePossessed false or pending kill)"),
				*PC->GetName(), *Pawn->GetName());
			return false;
		}
		Out->SetBoolField(TEXT("possessed"), true);
		Out->SetStringField(TEXT("controller"), PC->GetName());
		if (Previous)
		{
			Out->SetStringField(TEXT("previous_pawn"), Previous->GetName());
			if (bDestroyPrevious)
			{
				Previous->Destroy();
				Out->SetBoolField(TEXT("previous_destroyed"), true);
			}
		}
		return true;
	}

	/**
	 * spawn_in_pie: class, location [x,y,z], rotation [pitch,yaw,roll], scale (number or [x,y,z]), label,
	 * properties {path: value} (set before construction scripts and BeginPlay), collision ("always" | "adjust" |
	 * "dont_spawn"), possess (bool), player (index), destroy_previous (bool).
	 */
	TSharedPtr<FJsonObject> HandleSpawnInPie(const TSharedPtr<FJsonObject>& P)
	{
		UWorld* World = GEditor ? GEditor->PlayWorld.Get() : nullptr;
		if (!World)
		{
			return MCPError(TEXT("PIE is not running (start it with editor_action pie_start, or use a pie_test spawn step)"));
		}
		FString ClassName, Err;
		P->TryGetStringField(TEXT("class"), ClassName);
		UClass* Class = ResolveActorClass(ClassName, Err);
		if (!Class)
		{
			return MCPError(Err);
		}
		FVector Loc = FVector::ZeroVector, Rot = FVector::ZeroVector, Scale = FVector::OneVector;
		ReadVector(P, TEXT("location"), Loc);
		ReadVector(P, TEXT("rotation"), Rot);
		ReadVector(P, TEXT("scale"), Scale);
		const FTransform Xf(FRotator(Rot.X, Rot.Y, Rot.Z), Loc, Scale);

		FActorSpawnParameters SP;
		SP.bDeferConstruction = true;
		SP.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		FString Collision;
		if (P->TryGetStringField(TEXT("collision"), Collision))
		{
			if (Collision == TEXT("adjust")) SP.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButDontSpawnIfColliding;
			else if (Collision == TEXT("dont_spawn")) SP.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::DontSpawnIfColliding;
		}
		FString Name;
		if (P->TryGetStringField(TEXT("name"), Name) && !Name.IsEmpty())
		{
			SP.Name = FName(*Name);
			SP.NameMode = FActorSpawnParameters::ESpawnActorNameMode::Requested; // made unique if taken
		}
		AActor* Actor = World->SpawnActor(Class, &Xf, SP);
		if (!Actor)
		{
			return MCPError(FString::Printf(TEXT("SpawnActor(%s) failed (blocked by collision? pass collision=\"always\")"), *Class->GetName()));
		}

		// Properties before construction + BeginPlay (what "expose on spawn" does). Paths that only exist after the
		// construction script (Blueprint-added components) are retried once the actor is constructed.
		const TSharedPtr<FJsonObject>* Props = nullptr;
		P->TryGetObjectField(TEXT("properties"), Props);
		TArray<TSharedPtr<FJsonValue>> SetEarly, SetLate, Failed;
		TArray<TPair<FString, TSharedPtr<FJsonValue>>> Retry;
		if (Props && Props->IsValid())
		{
			for (const auto& Pair : (*Props)->Values)
			{
				const FString Key(*Pair.Key);
				FString E;
				if (FEpicUnrealMCPReflectionCommands::SetPropertyByPath(Actor, Key, Pair.Value, E))
				{
					SetEarly.Add(MakeShared<FJsonValueString>(Key));
				}
				else
				{
					Retry.Emplace(Key, Pair.Value);
				}
			}
		}
		Actor->FinishSpawning(Xf);
		for (const auto& Pair : Retry)
		{
			FString E;
			if (IsValid(Actor) && FEpicUnrealMCPReflectionCommands::SetPropertyByPath(Actor, Pair.Key, Pair.Value, E))
			{
				SetLate.Add(MakeShared<FJsonValueString>(Pair.Key));
			}
			else
			{
				Failed.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("%s: %s"), *Pair.Key, *E)));
			}
		}
		if (!IsValid(Actor))
		{
			return MCPError(FString::Printf(TEXT("%s was destroyed during construction or BeginPlay"), *Class->GetName()));
		}
		FString Label;
#if WITH_EDITOR
		if (P->TryGetStringField(TEXT("label"), Label) && !Label.IsEmpty())
		{
			Actor->SetActorLabel(Label);
		}
#endif
		TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
		Out->SetStringField(TEXT("name"), Actor->GetName());
#if WITH_EDITOR
		Out->SetStringField(TEXT("label"), Actor->GetActorLabel());
#endif
		Out->SetStringField(TEXT("class"), Class->GetPathName());
		Out->SetArrayField(TEXT("location"), Vec3(Actor->GetActorLocation()));
		Out->SetBoolField(TEXT("has_begun_play"), Actor->HasActorBegunPlay());
		Out->SetArrayField(TEXT("properties_set"), SetEarly);
		if (SetLate.Num()) Out->SetArrayField(TEXT("properties_set_after_construction"), SetLate);
		if (Failed.Num()) Out->SetArrayField(TEXT("properties_failed"), Failed);

		bool bPossess = false;
		P->TryGetBoolField(TEXT("possess"), bPossess);
		if (bPossess)
		{
			APawn* Pawn = Cast<APawn>(Actor);
			if (!Pawn)
			{
				return MCPError(FString::Printf(TEXT("spawned %s, but it is not a Pawn, so it cannot be possessed"), *Actor->GetName()));
			}
			int32 PlayerIndex = 0;
			bool bDestroy = false;
			P->TryGetNumberField(TEXT("player"), PlayerIndex);
			P->TryGetBoolField(TEXT("destroy_previous"), bDestroy);
			if (!Possess(World, PlayerIndex, Pawn, bDestroy, Out, Err))
			{
				return MCPError(Err);
			}
		}
		if (Failed.Num())
		{
			Out->SetBoolField(TEXT("success"), false);
			Out->SetStringField(TEXT("error"), FString::Printf(TEXT("spawned %s but could not set: %s"), *Actor->GetName(),
				*FString::JoinBy(Failed, TEXT("; "), [](const TSharedPtr<FJsonValue>& V) { return V->AsString(); })));
		}
		return Out;
	}

	/** pie_possess: target (actor label or name in the PIE world), player, destroy_previous. */
	TSharedPtr<FJsonObject> HandlePiePossess(const TSharedPtr<FJsonObject>& P)
	{
		UWorld* World = GEditor ? GEditor->PlayWorld.Get() : nullptr;
		if (!World)
		{
			return MCPError(TEXT("PIE is not running"));
		}
		FString Target, Err;
		P->TryGetStringField(TEXT("target"), Target);
		AActor* Actor = FindPieActor(World, Target);
		if (!Actor)
		{
			return MCPError(FString::Printf(TEXT("no actor labelled or named '%s' in the PIE world"), *Target));
		}
		APawn* Pawn = Cast<APawn>(Actor);
		if (!Pawn)
		{
			return MCPError(FString::Printf(TEXT("%s (%s) is not a Pawn"), *Actor->GetName(), *Actor->GetClass()->GetName()));
		}
		int32 PlayerIndex = 0;
		bool bDestroy = false;
		P->TryGetNumberField(TEXT("player"), PlayerIndex);
		P->TryGetBoolField(TEXT("destroy_previous"), bDestroy);
		TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
		Out->SetStringField(TEXT("pawn"), Pawn->GetName());
		if (!Possess(World, PlayerIndex, Pawn, bDestroy, Out, Err))
		{
			return MCPError(Err);
		}
		return Out;
	}

	/** pie_viewport: width, height. Fixes the running PIE viewport to that size (0 = follow the window again). */
	TSharedPtr<FJsonObject> HandlePieViewport(const TSharedPtr<FJsonObject>& P)
	{
		int32 W = 0, H = 0;
		P->TryGetNumberField(TEXT("width"), W);
		P->TryGetNumberField(TEXT("height"), H);
		FString Err;
		if (!FMCPPlayCommands::SetPieViewportSize(W, H, Err))
		{
			return MCPError(Err);
		}
		TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
		const FIntPoint Size = PieSceneViewport()->GetSizeXY();
		Out->SetNumberField(TEXT("width"), Size.X);
		Out->SetNumberField(TEXT("height"), Size.Y);
		Out->SetBoolField(TEXT("fixed"), PieSceneViewport()->HasFixedSize());
		return Out;
	}
}

bool FMCPPlayCommands::SetPieViewportSize(int32 Width, int32 Height, FString& OutError)
{
	FSceneViewport* Viewport = PieSceneViewport();
	if (!Viewport)
	{
		OutError = TEXT("PIE is not running (no PIE game viewport)");
		return false;
	}
	if (Width < 0 || Height < 0 || Width > 8192 || Height > 8192 || (Width == 0) != (Height == 0) || (Width > 0 && (Width < 16 || Height < 16)))
	{
		OutError = FString::Printf(TEXT("bad PIE viewport size %dx%d (16..8192, or 0x0 to follow the window)"), Width, Height);
		return false;
	}
	// A fixed size makes the scene viewport ignore its widget's geometry: in an offscreen editor the PIE window's
	// layout can be anything (a stale saved window size, the title bar subtracted, ...), the render target is not.
	Viewport->SetFixedViewportSize(uint32(Width), uint32(Height));
	if (Width > 0 && Viewport->GetSizeXY() != FIntPoint(Width, Height))
	{
		OutError = FString::Printf(TEXT("the PIE viewport stayed %dx%d (asked for %dx%d)"), Viewport->GetSizeXY().X, Viewport->GetSizeXY().Y, Width, Height);
		return false;
	}
	return true;
}

TSharedPtr<FJsonObject> FMCPPlayCommands::StartPie(const TSharedPtr<FJsonObject>& Params)
{
	if (!GEditor)
	{
		return MCPError(TEXT("no editor"));
	}
	if (GEditor->PlayWorld || GEditor->IsPlaySessionRequestQueued())
	{
		return MCPError(TEXT("PIE already running"));
	}
	int32 W = 0, H = 0;
	Params->TryGetNumberField(TEXT("width"), W);
	Params->TryGetNumberField(TEXT("height"), H);
	if ((W > 0) != (H > 0) || W < 0 || H < 0 || W > 8192 || H > 8192 || (W > 0 && (W < 16 || H < 16)))
	{
		return MCPError(FString::Printf(TEXT("bad PIE size %dx%d: pass both width and height (16..8192) or neither"), W, H));
	}
	FString Mode = TEXT("new_window");
	Params->TryGetStringField(TEXT("mode"), Mode);

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	ULevelEditorPlaySettings* Defaults = GetMutableDefault<ULevelEditorPlaySettings>();
	// When a PIE session ends, the editor saves the PIE window's last size as NewWindowWidth/Height (unless
	// CenterNewWindow). An offscreen editor can save a degenerate size (e.g. 1190x18), and every later PIE window
	// opens like that. Repair it in the saved settings.
	if (Defaults->NewWindowWidth < 160 || Defaults->NewWindowHeight < 120)
	{
		TSharedPtr<FJsonObject> Was = MakeShared<FJsonObject>();
		Was->SetNumberField(TEXT("width"), Defaults->NewWindowWidth);
		Was->SetNumberField(TEXT("height"), Defaults->NewWindowHeight);
		Out->SetObjectField(TEXT("repaired_saved_window_size"), Was);
		Defaults->NewWindowWidth = 1280;
		Defaults->NewWindowHeight = 720;
		Defaults->SaveConfig();
	}

	FRequestPlaySessionParams Session;
	ULevelEditorPlaySettings* Settings = DuplicateObject<ULevelEditorPlaySettings>(Defaults, GetTransientPackage());
	if (W > 0)
	{
		Settings->NewWindowWidth = W;
		Settings->NewWindowHeight = H;
	}
	Session.EditorPlaySettings = Settings;
	if (Mode == TEXT("viewport"))
	{
		FLevelEditorModule& LevelEditor = FModuleManager::LoadModuleChecked<FLevelEditorModule>(TEXT("LevelEditor"));
		TSharedPtr<IAssetViewport> Target = LevelEditor.GetFirstActiveViewport();
		if (!Target.IsValid())
		{
			return MCPError(TEXT("mode=viewport needs a level viewport (none is active); use mode=new_window"));
		}
		Session.DestinationSlateViewport = TWeakPtr<IAssetViewport>(Target);
	}
	else if (Mode != TEXT("new_window"))
	{
		return MCPError(FString::Printf(TEXT("unknown PIE mode '%s' (new_window, viewport)"), *Mode));
	}
	// Whatever the window or viewport widget ends up as, render PIE at exactly W x H.
	GPendingPieSize = FIntPoint(W, H);
	GSavedWindowSize = FIntPoint(Defaults->NewWindowWidth, Defaults->NewWindowHeight);
	GEditor->RequestPlaySession(Session);
	Out->SetStringField(TEXT("mode"), Mode);
	if (W > 0)
	{
		Out->SetNumberField(TEXT("width"), W);
		Out->SetNumberField(TEXT("height"), H);
	}
	Out->SetStringField(TEXT("note"), TEXT("PIE starts on the next editor tick; poll project_context.pie_running"));
	return Out;
}

void FMCPPlayCommands::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("pie_input"), &HandlePieInput, false);
	Registry.Register(TEXT("frame_stats"), &HandleFrameStats, false);
	Registry.Register(TEXT("spawn_in_pie"), &HandleSpawnInPie, false);
	Registry.Register(TEXT("pie_possess"), &HandlePiePossess, false);
	Registry.Register(TEXT("pie_viewport"), &HandlePieViewport, false);
	if (!GPostPieStartedHandle.IsValid())
	{
		GPostPieStartedHandle = FEditorDelegates::PostPIEStarted.AddStatic(&OnPostPieStarted);
		GShutdownPieHandle = FEditorDelegates::ShutdownPIE.AddStatic(&OnShutdownPie);
	}
}

void FMCPPlayCommands::Shutdown()
{
	if (GPostPieStartedHandle.IsValid())
	{
		FEditorDelegates::PostPIEStarted.Remove(GPostPieStartedHandle);
		FEditorDelegates::ShutdownPIE.Remove(GShutdownPieHandle);
		GPostPieStartedHandle.Reset();
		GShutdownPieHandle.Reset();
	}
	if (GThrottleHandle.IsValid() && GEditor)
	{
		GEditor->ShouldDisableCPUThrottlingDelegates.RemoveAll(
			[](const UEditorEngine::FShouldDisableCPUThrottling& D) { return D.GetHandle() == GThrottleHandle; });
	}
	GThrottleHandle.Reset();
	GInjections.Reset();
	GRecording = FRecording();
	if (GTickHandle.IsValid())
	{
		FTSTicker::GetCoreTicker().RemoveTicker(GTickHandle);
		GTickHandle.Reset();
	}
}
