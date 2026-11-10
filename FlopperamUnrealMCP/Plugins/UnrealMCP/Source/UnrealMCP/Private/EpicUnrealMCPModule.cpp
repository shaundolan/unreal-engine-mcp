#include "EpicUnrealMCPModule.h"
#include "MCPCommon.h"
#include "Agent/MCPAgentClient.h"
#include "Agent/SMCPAgentPanel.h"
#include "Commands/EpicUnrealMCPObservationCommands.h"
#include "Framework/Application/SlateApplication.h"
#include "Misc/CoreDelegates.h"
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
	// The "UnrealMCP Agent" tab (Window > Tools). Not in commandlets, which have no Slate.
	if (!IsRunningCommandlet())
	{
		MCPAgentPanel::RegisterTab();
	}
	UE_LOG(LogUnrealMCP, Display, TEXT("UnrealMCP module %s started"), UNREALMCP_PLUGIN_VERSION);
}

void FEpicUnrealMCPModule::ShutdownModule()
{
	if (!IsRunningCommandlet())
	{
		MCPAgentPanel::UnregisterTab();
	}
	// Stops a runner process the panel started; never touches runners started by someone else.
	FMCPAgentClient::Shutdown();
	if (GLog)
	{
		GLog->RemoveOutputDevice(&FMCPLogCapture::Get());
	}
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FEpicUnrealMCPModule, UnrealMCP)
