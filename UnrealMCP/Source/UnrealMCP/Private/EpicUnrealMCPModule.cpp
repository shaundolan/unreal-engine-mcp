#include "EpicUnrealMCPModule.h"
#include "MCPCommon.h"
#include "Commands/EpicUnrealMCPObservationCommands.h"
#include "Modules/ModuleManager.h"
#include "Misc/OutputDeviceRedirector.h"

#define LOCTEXT_NAMESPACE "FEpicUnrealMCPModule"

void FEpicUnrealMCPModule::StartupModule()
{
	// Capture the Output Log from the start so editor_log can show startup errors too.
	if (GLog)
	{
		GLog->AddOutputDevice(&FMCPLogCapture::Get());
	}
	UE_LOG(LogUnrealMCP, Display, TEXT("UnrealMCP module %s started"), UNREALMCP_PLUGIN_VERSION);
}

void FEpicUnrealMCPModule::ShutdownModule()
{
	if (GLog)
	{
		GLog->RemoveOutputDevice(&FMCPLogCapture::Get());
	}
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FEpicUnrealMCPModule, UnrealMCP)
