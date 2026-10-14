param(
    [string]$Workspace = "",
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [switch]$NoOpen
)
$ErrorActionPreference = "Stop"
$ProjectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = Join-Path $ProjectDir "workspace"
} elseif (-not [System.IO.Path]::IsPathRooted($Workspace)) {
    $Workspace = Join-Path (Get-Location).Path $Workspace
}
$Workspace = [System.IO.Path]::GetFullPath($Workspace)
$Python = $null
$Prefix = @()
foreach ($Candidate in @("python", "py", "python3")) {
    $Command = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($null -eq $Command) { continue }
    $TryPrefix = @()
    if ($Candidate -eq "py") { $TryPrefix = @("-3") }
    & $Command.Source @TryPrefix -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $Python = $Command.Source
        $Prefix = $TryPrefix
        break
    }
}
if ($null -eq $Python) { throw "Python 3.10 or newer is required. Install Python and restart the launcher." }
Push-Location $ProjectDir
try {
    & $Python @Prefix -m lite_recommender --workspace $Workspace init
    if ($LASTEXITCODE -ne 0) { throw "Workspace initialization failed." }
    $Options = @("-m", "lite_recommender", "--workspace", $Workspace, "serve", "--port", "$Port")
    if (-not $NoOpen) { $Options += "--open" }
    & $Python @Prefix @Options
    if ($LASTEXITCODE -ne 0) { throw "The local server stopped with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
