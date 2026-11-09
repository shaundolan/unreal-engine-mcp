#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

/**
 * Counts the editor undo transactions one agent turn made, so "Undo last agent turn" can revert exactly those.
 *
 * Every mutating MCP command is its own FScopedTransaction, so a turn is N transactions in the global buffer. Begin
 * records the top active transaction; End collects the ids of the transactions added since. UndoLast undoes them only
 * if they are still the top N active transactions (nothing else was done or undone in between), and refuses when a
 * map switch reset the buffer or PIE is running. Edits made without a transaction (raw execute_python outside
 * ScopedEditorTransaction, asset deletes, saves) are not undoable by any means and are not counted.
 */
class FMCPAgentTurns
{
public:
	static FMCPAgentTurns& Get();

	TSharedPtr<FJsonObject> Begin(int32 Turn);
	TSharedPtr<FJsonObject> End(int32 Turn);
	TSharedPtr<FJsonObject> UndoLast();
	TSharedPtr<FJsonObject> Status() const;

	/** Steps the last finished turn can undo (0 if none or not undoable); Why says what blocks it. */
	int32 UndoableSteps(FString* Why = nullptr) const;
	int32 LastTurn() const { return Record.Turn; }

private:
	struct FRecord
	{
		int32 Turn = 0;
		bool bOpen = false;
		bool bReset = false;
		bool bUndone = false;
		int32 StartActive = 0;
		FGuid StartTop;
		TArray<FGuid> Ids;
		TArray<FString> Titles;
	};
	FRecord Record;
};
