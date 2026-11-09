<#
Windows plugin dev loop for the sample project (Linux: scripts/ue_linux.sh).

  scripts\ue_windows.ps1 build       sync plugin copy + compile (this project's editor must be closed)
  scripts\ue_windows.ps1 launch      start the editor offscreen (no window) and wait until the MCP plugin answers ping
  scripts\ue_windows.ps1 launch-gui  same, with a visible editor window
  scripts\ue_windows.ps1 quit        close the editor over MCP (falls back to killing the editor this script started)
  scripts\ue_windows.ps1 rebuild     quit + build + launch

Environment:
  UE_ROOT          engine root (default C:\Program Files\Epic Games\UE_5.8)
  UE_PROJECT       .uproject to build/launch (default: the sample project)
  UE_LOG_DIR       build/editor logs and the editor pid file (default %TEMP%\unreal-mcp)
  UNREAL_MCP_PORT  port the launched editor listens on and ping/quit talk to (default 55557)
  UNREAL_MCP_HOST, UNREAL_MCP_TOKEN  as for the MCP server

Works in Windows PowerShell 5.1 and PowerShell 7. If scripts are blocked:
  powershell -ExecutionPolicy Bypass -File scripts\ue_windows.ps1 build
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("build", "launch", "launch-gui", "quit", "rebuild")]
    [string]$Command
)
$ErrorActionPreference = "Stop"
if (-not $Command) {
    Write-Host "usage: scripts\ue_windows.ps1 build|launch|launch-gui|quit|rebuild"
    exit 2
}

function Get-EnvOr([string]$Name, [string]$Default) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrEmpty($v)) { return $Default } else { return $v }
}

$Repo = Split-Path -Parent $PSScriptRoot
$UeRoot = Get-EnvOr "UE_ROOT" "C:\Program Files\Epic Games\UE_5.8"
$Project = Get-EnvOr "UE_PROJECT" (Join-Path $Repo "FlopperamUnrealMCP\FlopperamUnrealMCP.uproject")
$McpHost = Get-EnvOr "UNREAL_MCP_HOST" "127.0.0.1"
$Port = [int](Get-EnvOr "UNREAL_MCP_PORT" "55557")
$Token = Get-EnvOr "UNREAL_MCP_TOKEN" ""
# One log/pid dir per port, so several editors (one per port) never share editor.log or a pid file.
$LogDir = Get-EnvOr "UE_LOG_DIR" (Join-Path $env:TEMP "unreal-mcp\$Port")
$PidFile = Join-Path $LogDir "editor.pid"
$EditorLog = Join-Path $LogDir "editor.log"
$EditorExe = Join-Path $UeRoot "Engine\Binaries\Win64\UnrealEditor.exe"
$BuildBat = Join-Path $UeRoot "Engine\Build\BatchFiles\Build.bat"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# One framed request to the plugin: 4-byte big-endian length + UTF-8 JSON (see Python/unreal_mcp/protocol.py).
# Returns the reply object, or $null if nothing answers. No uv/Python needed, so it works before `uv sync`.
function Send-Mcp([string]$Type, [hashtable]$Params = @{}, [int]$TimeoutMs = 8000) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($McpHost, $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(2000)) { return $null }
        $client.EndConnect($connect)
        $client.ReceiveTimeout = $TimeoutMs
        $client.SendTimeout = $TimeoutMs
        $stream = $client.GetStream()
        $req = @{ id = 1; type = $Type; params = $Params }
        if ($Token) { $req.token = $Token }
        $payload = [System.Text.Encoding]::UTF8.GetBytes(($req | ConvertTo-Json -Compress -Depth 10))
        $header = [BitConverter]::GetBytes([uint32]$payload.Length)
        if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($header) }
        $stream.Write($header, 0, 4)
        $stream.Write($payload, 0, $payload.Length)
        $buf = New-Object byte[] 4
        $got = 0
        while ($got -lt 4) { $n = $stream.Read($buf, $got, 4 - $got); if ($n -le 0) { return $null }; $got += $n }
        if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($buf) }
        $len = [BitConverter]::ToUInt32($buf, 0)
        $body = New-Object byte[] $len
        $got = 0
        while ($got -lt $len) { $n = $stream.Read($body, $got, $len - $got); if ($n -le 0) { return $null }; $got += $n }
        return ([System.Text.Encoding]::UTF8.GetString($body) | ConvertFrom-Json)
    } catch {
        return $null
    } finally {
        $client.Close()
    }
}

function Test-Editor {
    $r = Send-Mcp "ping"
    return ($null -ne $r -and $r.status -eq "success")
}

# The editor this script started on $Port (pid file), only if that pid is still an UnrealEditor.
# quit never touches any other editor process.
function Get-OurEditor {
    if (-not (Test-Path $PidFile)) { return $null }
    $editorPid = [int](Get-Content $PidFile -Raw)
    $proc = Get-Process -Id $editorPid -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -eq "UnrealEditor") { return $proc }
    return $null
}

# PIDs of UnrealEditor processes that already have $Project open (any port).
function Get-ProjectEditors {
    $full = [IO.Path]::GetFullPath($Project)
    Get-CimInstance Win32_Process -Filter "Name = 'UnrealEditor.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($full, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
        ForEach-Object { $_.ProcessId }
}

function Invoke-Quit {
    $proc = Get-OurEditor
    if (Test-Editor) {
        Send-Mcp "execute_python" @{ code = "import unreal; unreal.SystemLibrary.quit_editor()" } 15000 | Out-Null
    } elseif (-not $proc) {
        Write-Host "no editor on port $Port"
        return
    }
    if (-not $proc) {
        # Not started by this script: only wait for the port to go quiet, never kill someone else's editor.
        for ($i = 0; $i -lt 45; $i++) { if (-not (Test-Editor)) { Write-Host "editor closed"; return }; Start-Sleep 2 }
        throw "editor on port $Port did not quit (not started by this script, so not killing it)"
    }
    if ($proc.WaitForExit(90000)) {
        Write-Host "editor closed"
    } else {
        Write-Host "editor did not quit; killing $($proc.Id)"
        Stop-Process -Id $proc.Id -Force
        Start-Sleep 5
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}

function Invoke-Build {
    if (-not (Test-Path $BuildBat)) { throw "Build.bat not found under UE_ROOT=$UeRoot" }
    $open = @(Get-ProjectEditors)
    if ($open.Count -gt 0) { throw "the project is open in editor pid(s) $($open -join ', '); quit it first" }
    & (Join-Path $PSScriptRoot "sync_plugin.ps1") | Out-Null
    $log = Join-Path $LogDir "build.log"
    $target = [IO.Path]::GetFileNameWithoutExtension($Project) + "Editor"
    # Start-Process, not `& Build.bat 2>&1`: Windows PowerShell 5.1 turns native stderr lines into
    # terminating errors under ErrorActionPreference=Stop. Arguments are joined as-is, so quote paths.
    $errLog = Join-Path $LogDir "build.stderr.log"
    $p = Start-Process -FilePath $BuildBat -NoNewWindow -Wait -PassThru `
        -ArgumentList @($target, "Win64", "Development", "`"-Project=$Project`"", "-WaitMutex", "-NoHotReloadFromIDE") `
        -RedirectStandardOutput $log -RedirectStandardError $errLog
    $code = $p.ExitCode
    if ((Test-Path $errLog) -and (Get-Item $errLog).Length -gt 0) { Get-Content $errLog | Add-Content $log }
    if ($code -eq 0) {
        $warnings = (Select-String -Path $log -Pattern "warning C\d+|: warning" -AllMatches | Measure-Object).Count
        Write-Host "build ok ($warnings warnings) -> $log"
    } else {
        Write-Host "BUILD FAILED (exit $code) -> $log"
        Select-String -Path $log -Pattern ": (fatal )?error|error C\d+|error LNK\d+" |
            ForEach-Object { ($_.Line -replace '.*\\Source\\UnrealMCP\\', '').Trim() } |
            Sort-Object -Unique | Select-Object -First 40 | ForEach-Object { Write-Host $_ }
        exit 1
    }
}

function Invoke-Launch([bool]$Gui) {
    if (-not (Test-Path $EditorExe)) { throw "UnrealEditor.exe not found under UE_ROOT=$UeRoot" }
    if (Test-Editor) { Write-Host "an editor already answers on port $Port"; return }
    $existing = Get-OurEditor
    if ($existing) { throw "editor $($existing.Id) is still starting or hung; run quit first" }
    $open = @(Get-ProjectEditors)
    if ($open.Count -gt 0) { throw "the project is already open in editor pid(s) $($open -join ', ') on another port" }
    $editorArgs = @("`"$Project`"", "-nosplash", "`"-abslog=$EditorLog`"",
        "-ini:Engine:[/Script/PythonScriptPlugin.PythonScriptPluginSettings]:bDeveloperMode=True",
        # a background editor throttles to a few fps ("Use Less CPU when in Background"), which slows pie_test
        "-ini:EditorPerProjectUserSettings:[/Script/UnrealEd.EditorPerformanceSettings]:bThrottleCPUWhenNotForeground=False")
    if (-not $Gui) { $editorArgs += "-RenderOffscreen"; $editorArgs += "-unattended" }  # no modal dialogs / crash-reporter window
    $env:UNREAL_MCP_PORT = "$Port"   # inherited by the editor: the plugin listens there
    $proc = Start-Process -FilePath $EditorExe -ArgumentList $editorArgs -PassThru
    Set-Content -Path $PidFile -Value $proc.Id
    for ($i = 1; $i -le 180; $i++) {
        if (Test-Editor) {
            Start-Sleep 10
            Write-Host "editor $($proc.Id) ready on port $Port after ~$($i * 5)s"
            return
        }
        if ($proc.HasExited) {
            Write-Host "EDITOR EXITED (code $($proc.ExitCode))"
            if (Test-Path $EditorLog) { Get-Content $EditorLog -Tail 20 }
            Remove-Item $PidFile -ErrorAction SilentlyContinue
            exit 1
        }
        Start-Sleep 5
    }
    throw "editor did not answer within 15 min (log: $EditorLog)"
}

switch ($Command) {
    "build" { Invoke-Build }
    "launch" { Invoke-Launch $false }
    "launch-gui" { Invoke-Launch $true }
    "quit" { Invoke-Quit }
    "rebuild" { Invoke-Quit; Invoke-Build; Invoke-Launch $false }
}
