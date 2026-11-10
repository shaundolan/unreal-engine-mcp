#include "Commands/EpicUnrealMCPPythonCommands.h"
#include "IPythonScriptPlugin.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"

bool FEpicUnrealMCPPythonCommands::IsPythonAvailable()
{
	IPythonScriptPlugin* Python = IPythonScriptPlugin::Get();
	return Python && Python->IsPythonAvailable();
}

static const TCHAR* LogTypeName(EPythonLogOutputType Type)
{
	switch (Type)
	{
	case EPythonLogOutputType::Warning: return TEXT("Warning");
	case EPythonLogOutputType::Error:   return TEXT("Error");
	default:                            return TEXT("Info");
	}
}

/**
 * params: code (string), mode ("file" | "statement" | "eval", default "file")
 * result: command_result (string; the value for "eval"), log [{type, output}], python_ok (bool)
 *
 * A Python exception is NOT a transport error: the reply is success with python_ok=false and
 * the traceback in log, so the client can show it to the model.
 */
static TSharedPtr<FJsonObject> HandleExecutePython(const TSharedPtr<FJsonObject>& Params)
{
	IPythonScriptPlugin* Python = IPythonScriptPlugin::Get();
	if (!Python || !Python->IsPythonAvailable())
	{
		return MCPError(TEXT("Python is not available: enable the 'Python Editor Script Plugin' (PythonScriptPlugin) and restart the editor"));
	}

	FString Code;
	if (!Params->TryGetStringField(TEXT("code"), Code) || Code.IsEmpty())
	{
		return MCPError(TEXT("missing 'code'"));
	}
	FString Mode = TEXT("file");
	Params->TryGetStringField(TEXT("mode"), Mode);

	FPythonCommandEx Command;
	Command.Command = Code;
	Command.Flags = EPythonCommandFlags::Unattended; // never block on a modal dialog
	Command.FileExecutionScope = EPythonFileExecutionScope::Private;
	if (Mode == TEXT("statement"))
	{
		Command.ExecutionMode = EPythonCommandExecutionMode::ExecuteStatement;
	}
	else if (Mode == TEXT("eval"))
	{
		Command.ExecutionMode = EPythonCommandExecutionMode::EvaluateStatement;
	}
	FString TempScript;
	if (Mode != TEXT("statement") && Mode != TEXT("eval"))
	{
		// Multi-statement script. ExecuteFile guesses whether its input is literal code or a path to a .py file,
		// and any script that merely mentions ".py" (even in a comment) gets treated as a path ("Could not load
		// Python file"). Write the script to its own temp file and execute that path, so there is nothing to guess.
		Command.ExecutionMode = EPythonCommandExecutionMode::ExecuteFile;
		const FString Dir = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("UnrealMCP/python"));
		IFileManager::Get().MakeDirectory(*Dir, /*Tree*/ true);
		TempScript = Dir / FString::Printf(TEXT("mcp_%s.py"), *FGuid::NewGuid().ToString(EGuidFormats::Digits));
		if (FFileHelper::SaveStringToFile(Code, *TempScript, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			Command.Command = FString::Printf(TEXT("\"%s\""), *TempScript);
		}
		else
		{
			TempScript.Reset(); // fall back to the literal script
		}
	}

	const bool bOk = Python->ExecPythonCommandEx(Command);
	if (!TempScript.IsEmpty())
	{
		IFileManager::Get().Delete(*TempScript, /*RequireExists*/ false, /*EvenReadOnly*/ true, /*Quiet*/ true);
	}

	TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
	Out->SetBoolField(TEXT("python_ok"), bOk);
	Out->SetStringField(TEXT("command_result"), Command.CommandResult);
	TArray<TSharedPtr<FJsonValue>> Log;
	for (const FPythonLogOutputEntry& Entry : Command.LogOutput)
	{
		TSharedPtr<FJsonObject> E = MakeShared<FJsonObject>();
		E->SetStringField(TEXT("type"), LogTypeName(Entry.Type));
		E->SetStringField(TEXT("output"), Entry.Output);
		Log.Add(MakeShared<FJsonValueObject>(E));
	}
	Out->SetArrayField(TEXT("log"), Log);
	return Out;
}

void FEpicUnrealMCPPythonCommands::Register(IMCPCommandRegistry& Registry)
{
	// Not wrapped in a transaction: scripts manage their own with unreal.ScopedEditorTransaction,
	// and a blanket transaction around arbitrary code (asset saves, level loads) misbehaves.
	Registry.Register(TEXT("execute_python"), &HandleExecutePython, false);
}
