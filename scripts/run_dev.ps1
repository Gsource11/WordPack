param(
    [string]$DataDir = "",
    [switch]$ResetData
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $repoRoot
$pythonExe = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$appEntry = Join-Path $repoRoot "app.py"

if (!(Test-Path -LiteralPath $pythonExe)) {
    throw "Python not found: $pythonExe"
}
if (!(Test-Path -LiteralPath $appEntry)) {
    throw "App entry not found: $appEntry"
}

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $DataDir = Join-Path $workspaceRoot ".wordpack-dev-data"
}
$resolvedDataDir = [System.IO.Path]::GetFullPath($DataDir)

if ($ResetData -and (Test-Path -LiteralPath $resolvedDataDir)) {
    Remove-Item -LiteralPath $resolvedDataDir -Recurse -Force
}
New-Item -ItemType Directory -Path $resolvedDataDir -Force | Out-Null

$env:WORDPACK_DATA_DIR = $resolvedDataDir
Write-Host "WORDPACK_DATA_DIR=$resolvedDataDir"
Write-Host "Python=$pythonExe"

& $pythonExe $appEntry
exit $LASTEXITCODE
