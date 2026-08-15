$ErrorActionPreference = "Stop"
$HostName = $null
$EventName = $null
for ($index = 0; $index -lt $args.Count; $index++) {
  if ($args[$index] -eq "--host" -and $index + 1 -lt $args.Count) { $HostName = $args[++$index]; continue }
  if ($args[$index] -eq "--event" -and $index + 1 -lt $args.Count) { $EventName = $args[++$index]; continue }
}
if (-not $HostName -or -not $EventName) { throw "IntentRail bootstrap requires --host and --event." }
# PowerShell's `$input` and Console.In can both be detached from redirected
# stdin in non-interactive hosts. Read the operating-system handle as bytes so
# Hook JSON survives hosted runners and Unicode is never transcoded.
$payloadStream = New-Object System.IO.MemoryStream
try {
  [Console]::OpenStandardInput().CopyTo($payloadStream)
  [byte[]]$PayloadBytes = $payloadStream.ToArray()
} finally {
  $payloadStream.Dispose()
}

function ConvertTo-NativeArgument {
  param([AllowEmptyString()][string]$Value)
  if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
  $builder = New-Object System.Text.StringBuilder
  [void]$builder.Append('"')
  $backslashes = 0
  foreach ($character in $Value.ToCharArray()) {
    if ($character -eq [char]92) { $backslashes++; continue }
    if ($character -eq [char]34) {
      if ($backslashes) { [void]$builder.Append((([string][char]92) * (($backslashes * 2) + 1))) }
      else { [void]$builder.Append([char]92) }
      [void]$builder.Append('"')
      $backslashes = 0
      continue
    }
    if ($backslashes) { [void]$builder.Append((([string][char]92) * $backslashes)); $backslashes = 0 }
    [void]$builder.Append($character)
  }
  if ($backslashes) { [void]$builder.Append((([string][char]92) * ($backslashes * 2))) }
  [void]$builder.Append('"')
  return $builder.ToString()
}

function Invoke-WithPayload {
  param([string]$Executable, [string[]]$Arguments)
  $start = New-Object System.Diagnostics.ProcessStartInfo
  $start.FileName = $Executable
  $start.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join " ")
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.RedirectStandardInput = $true
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $start
  try {
    [void]$process.Start()
    if ($PayloadBytes.Length -gt 0) {
      $process.StandardInput.BaseStream.Write($PayloadBytes, 0, $PayloadBytes.Length)
    }
    $process.StandardInput.BaseStream.Close()
    $process.WaitForExit()
    $script:ChildExitCode = $process.ExitCode
  } finally {
    $process.Dispose()
  }
}

function Test-NativeExecutable {
  param([string]$Executable)
  if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
  return [IO.Path]::GetExtension($Executable) -ieq ".exe"
}

$scriptPath = Join-Path $PSScriptRoot "intentrail_bootstrap.py"
$locator = $null
if ($env:LOCALAPPDATA) {
  $candidate = Join-Path $env:LOCALAPPDATA "IntentRail\cli-path.txt"
  if (Test-Path -LiteralPath $candidate) { $locator = $candidate }
}
if ($locator) {
  $cli = (Get-Content -LiteralPath $locator -TotalCount 1).Trim()
  if (Test-NativeExecutable $cli) { Invoke-WithPayload $cli @("hook", "--host", $HostName, "--event", $EventName); exit $script:ChildExitCode }
}
$managed = Get-Command intentrail -ErrorAction SilentlyContinue
if ($managed -and (Test-NativeExecutable $managed.Source)) { Invoke-WithPayload $managed.Source @("hook", "--host", $HostName, "--event", $EventName); exit $script:ChildExitCode }
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
