// pie_input / frame_stats: drive and measure a running PIE session from the MCP server.
//
// Both commands only schedule work and return. A core ticker applies injections every frame and
// samples frame timings, so the game thread is never blocked while PIE runs (the server sleeps
// between short commands, as pie_test already does).
#include "Commands/MCPPlayCommands.h"

#include "Containers/Ticker.h"
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
#include "Misc/EngineVersionComparison.h"
#include "Misc/PackageName.h"
#include "UObject/SoftObjectPath.h"
// GGameThreadTime & co. moved from RenderCore.h to RenderTimer.h in 5.x.
#if __has_include("RenderTimer.h")
#include "RenderTimer.h"
#else
#include "RenderCore.h"
#endif
#if !UE_VERSION_OLDER_THAN(5, 6, 0)
#include "InputKeyEventArgs.h"
#endif

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
#if UE_VERSION_OLDER_THAN(5, 6, 0)
		PC->InputKey(FInputKeyParams(Key, Event, double(Amount), Key.IsGamepadKey()));
#else
		PC->InputKey(FInputKeyEventArgs::CreateSimulated(Key, Event, Amount));
#endif
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
}

void FMCPPlayCommands::Register(IMCPCommandRegistry& Registry)
{
	Registry.Register(TEXT("pie_input"), &HandlePieInput, false);
	Registry.Register(TEXT("frame_stats"), &HandleFrameStats, false);
}

void FMCPPlayCommands::Shutdown()
{
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
