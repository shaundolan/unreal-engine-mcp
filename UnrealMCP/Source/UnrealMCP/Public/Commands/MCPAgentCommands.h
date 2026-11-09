#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"

/**
 * The in-editor agent panel and its undo accounting, over MCP.
 *   agent_turn   {action: begin|end|undo|status, turn}: the agent runner marks each turn so its undo steps can be
 *                counted and reverted as one ("Undo last agent turn").
 *   agent_panel  {action: status|open|close|start_runner|stop_runner|connect|disconnect|send|stop|confirm|reset|
 *                transcript|undo_last_turn, ...}: drive the "UnrealMCP Agent" tab without the UI (offscreen editors,
 *                tests): the same client object the Slate tab renders.
 */
class UNREALMCP_API FMCPAgentCommands
{
public:
	static void Register(IMCPCommandRegistry& Registry);
};
