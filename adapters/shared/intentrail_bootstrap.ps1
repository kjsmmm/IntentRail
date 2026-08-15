$ErrorActionPreference = "Stop"
$HostName = $null
$EventName = $null
for ($index = 0; $index -lt $args.Count; $index++) {
  if ($args[$index] -eq "--host" -and $index + 1 -lt $args.Count) { $HostName = $args[++$index]; continue }
  if ($args[$index] -eq "--event" -and $index + 1 -lt $args.Count) { $EventName = $args[++$index]; continue }
}
if (-not $HostName -or -not $EventName) { throw "IntentRail bootstrap requires --host and --event." }
# `$input` is pipeline-scoped and can be empty when a script is launched with
# `powershell -File` on hosted Windows runners. Console.In preserves redirected
# Hook stdin consistently across Windows PowerShell and PowerShell Core.
$Payload = [Console]::In.ReadToEnd()

function Invoke-WithPayload {
  param([string]$Executable, [string[]]$Arguments)
  if ($Payload) { $Payload | & $Executable @Arguments } else { & $Executable @Arguments }
  $script:ChildExitCode = $LASTEXITCODE
}

$scriptPath = Join-Path $PSScriptRoot "intentrail_bootstrap.py"
$locator = $null
if ($env:LOCALAPPDATA) {
  $candidate = Join-Path $env:LOCALAPPDATA "IntentRail\cli-path.txt"
  if (Test-Path -LiteralPath $candidate) { $locator = $candidate }
}
if ($locator) {
  $cli = (Get-Content -LiteralPath $locator -TotalCount 1).Trim()
  if ($cli -and (Test-Path -LiteralPath $cli)) { Invoke-WithPayload $cli @("hook", "--host", $HostName, "--event", $EventName); exit $script:ChildExitCode }
}
$managed = Get-Command intentrail -ErrorAction SilentlyContinue
if ($managed) { Invoke-WithPayload $managed.Source @("hook", "--host", $HostName, "--event", $EventName); exit $script:ChildExitCode }
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) { Invoke-WithPayload $uv.Source @("run", "--quiet", "--script", $scriptPath, "hook", "--host", $HostName, "--event", $EventName); exit $script:ChildExitCode }
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) { Invoke-WithPayload $python.Source @($scriptPath, "hook", "--host", $HostName, "--event", $EventName); exit $script:ChildExitCode }
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { Invoke-WithPayload $py.Source @("-3", $scriptPath, "hook", "--host", $HostName, "--event", $EventName); exit $script:ChildExitCode }

$message = "IntentRail runtime unavailable; install with uv tool or pipx, then run intentrail doctor."
if ($EventName -eq "PreToolUse") {
  if ($HostName -eq "copilot-cli") {
    @{ permissionDecision = "deny"; permissionDecisionReason = $message } | ConvertTo-Json -Compress
  } else {
    @{ hookSpecificOutput = @{ hookEventName = "PreToolUse"; permissionDecision = "deny"; permissionDecisionReason = $message } } | ConvertTo-Json -Compress -Depth 4
  }
} else {
  @{ systemMessage = $message } | ConvertTo-Json -Compress
}
