#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"

class UWorld;
class AActor;

/**
 * Drive and measure a running PIE session (used by pie_test steps).
 *   pie_input    press / hold / axis input on the PIE player controller: raw keys (SpaceBar, W, MouseX,
 *                Gamepad_LeftX, ...) or Enhanced Input actions (injected every frame for the duration)
 *   frame_stats  start recording N frames, then read average / p95 frame, game, render, RHI and GPU times
 *   pie_worlds   the PIE world contexts of a (multi-client) session: server, client:1..N, who is connected
 * In-PIE commands take an optional "world" (or "net_role") target: "" / "pie" = GEditor->PlayWorld (the old
 * behaviour), "server" = the listen / dedicated server world, "client:N" = the N-th client world (1-based).
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
	 * "viewport" = the active level viewport), net_mode ("standalone" | "listen_server" | "client" = Play As Client with
	 * an in-process dedicated server) and clients (number of NM_Client worlds). Without net_mode the saved Play settings
	 * are used unchanged. Always one editor process (RunUnderOneProcess). Repairs a degenerate saved PIE window size.
	 */
	static TSharedPtr<FJsonObject> StartPie(const TSharedPtr<FJsonObject>& Params);
	/** Fix the running PIE viewport's render size (0x0 lets it follow its window again). */
	static bool SetPieViewportSize(int32 Width, int32 Height, FString& OutError, UWorld* World = nullptr);

	/** Every PIE world, in PIE instance order. */
	static TArray<UWorld*> GetPieWorlds();
	/** "server", "client:N", "standalone:N" (or "" for a non-PIE world). */
	static FString PieWorldTarget(const UWorld* World);
	/** A world target ("", "pie", "server", "client:2", ...) to its PIE world; nullptr + OutError if there is none. */
	static UWorld* ResolvePieWorld(const FString& Target, FString& OutError);
	/** Same, reading the target from params "world" or "net_role". */
	static UWorld* ResolvePieWorld(const TSharedPtr<FJsonObject>& Params, FString& OutError);
	/** The actor's replication id ("" when it has none yet, or under Iris): equal on the server and every client. */
	static FString NetIdOf(const AActor* Actor);
	/** The copy of Actor in another PIE world: same net id, else same name (level-placed actors), else the only
	 *  actor of its class there. nullptr when none. */
	static AActor* FindReplica(const AActor* Actor, UWorld* InWorld);
};
