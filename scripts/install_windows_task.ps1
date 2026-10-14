param(
    [string]$Workspace = "",
    [string]$Python = "",
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [switch]$Install,
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
if ($Install -and $Remove) { throw "Choose either -Install or -Remove." }
$TaskName = "Lite Recommender Local Service"
$ProjectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($Workspace)) { $Workspace = Join-Path $ProjectDir "workspace" }
if (-not [System.IO.Path]::IsPathRooted($Workspace)) { $Workspace = Join-Path (Get-Location).Path $Workspace }
$Workspace = [System.IO.Path]::GetFullPath($Workspace)
if ($Remove) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed $TaskName"
    exit 0
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Command = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $Command) { throw "Pass -Python with the full path to python.exe (Python 3.10+)." }
    $Python = $Command.Source
}
# Resolve the actual interpreter instead of retaining a launcher/Windows alias.
$ResolvedPython = & $Python -c "import sys; assert sys.version_info >= (3,10); print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10 or newer is required." }
$Python = "$ResolvedPython".Trim()
if ($Workspace.Contains('"') -or $Python.Contains('"')) { throw "Paths may not contain quotes." }
$Arguments = '-m lite_recommender --workspace "' + $Workspace + '" serve --port ' + $Port
if (-not $Install) {
    Write-Output "Task: $TaskName"
    Write-Output "At logon for: $env:USERDOMAIN\$env:USERNAME"
    Write-Output "Command: `"$Python`" $Arguments"
    Write-Output "Working directory: $ProjectDir"
    Write-Output "Preview only. Pass -Install to register; -Remove to unregister."
    exit 0
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw "Task already exists. Review or remove it before reinstalling."
}
Push-Location $ProjectDir
try {
    & $Python -m lite_recommender --workspace $Workspace init
    if ($LASTEXITCODE -ne 0) { throw "Workspace initialization failed." }
} finally { Pop-Location }
$UserId = "$env:USERDOMAIN\$env:USERNAME"
$Action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "Registered $TaskName. Enable the weekly schedule separately in the local UI."
