# Mirror UnrealMCP\ (the source of truth) into the sample project's Plugins folder.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "UnrealMCP"
$dst = Join-Path $root "FlopperamUnrealMCP\Plugins\UnrealMCP"
# /MIR mirrors (deletes extras); exclude build outputs. Robocopy exit codes < 8 mean success.
robocopy $src $dst /MIR /XD Binaries Intermediate /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with $LASTEXITCODE" }
Write-Host "synced $src -> $dst"
