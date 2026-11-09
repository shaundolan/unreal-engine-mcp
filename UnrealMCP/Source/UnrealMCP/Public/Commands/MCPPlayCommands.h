#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"

/**
 * Drive and measure a running PIE session (used by pie_test steps).
 *   pie_input    press / hold / axis input on the PIE player controller: raw keys (SpaceBar, W, MouseX,
 *                Gamepad_LeftX, ...) or Enhanced Input actions (injected every frame for the duration)
 *   frame_stats  start recording N frames, then read average / p95 frame, game, render, RHI and GPU times
 * Both run asynchronously on the core ticker: the command returns at once and the editor keeps ticking.
 */
class UNREALMCP_API FMCPPlayCommands
{
public:
	static void Register(IMCPCommandRegistry& Registry);
	/** Stop all injections and recordings (editor shutdown / PIE end). */
	static void Shutdown();

	/**
	 * editor_action pie_start. params: width, height (render PIE at exactly this size), mode ("new_window" default, or
	 * "viewport" = the active level viewport). Repairs a degenerate saved PIE window size first.
	 */
	static TSharedPtr<FJsonObject> StartPie(const TSharedPtr<FJsonObject>& Params);
	/** Fix the running PIE viewport's render size (0x0 lets it follow its window again). */
	static bool SetPieViewportSize(int32 Width, int32 Height, FString& OutError);
};
