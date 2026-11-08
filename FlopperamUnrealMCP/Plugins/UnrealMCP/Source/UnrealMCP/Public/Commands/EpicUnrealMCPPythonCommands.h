#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"

/** execute_python: run Unreal Python through the PythonScriptPlugin. */
class UNREALMCP_API FEpicUnrealMCPPythonCommands
{
public:
	static void Register(IMCPCommandRegistry& Registry);
	static bool IsPythonAvailable();
};
