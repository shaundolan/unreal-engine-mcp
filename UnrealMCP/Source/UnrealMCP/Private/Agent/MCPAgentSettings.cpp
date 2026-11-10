#include "Agent/MCPAgentSettings.h"

FString UMCPAgentSettings::ConfirmModeName(EMCPAgentConfirmMode Mode)
{
	switch (Mode)
	{
	case EMCPAgentConfirmMode::Off: return TEXT("off");
	case EMCPAgentConfirmMode::Strict: return TEXT("strict");
	case EMCPAgentConfirmMode::All: return TEXT("all");
	default: return TEXT("destructive");
	}
}
