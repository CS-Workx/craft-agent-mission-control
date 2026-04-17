# Mission Control uninstaller — PowerShell wrapper around uninstall.py.
#
# Usage:
#   pwsh uninstall.ps1              # interactive
#   pwsh uninstall.ps1 -Yes         # non-interactive
#   pwsh uninstall.ps1 -DryRun      # show what would be removed

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ForwardedArgs
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Error "Python 3.9+ is required but was not found on PATH."
    exit 1
}

$uninstallScript = Join-Path $ScriptDir "uninstall.py"
if (-not (Test-Path $uninstallScript)) {
    Write-Error "uninstall.py not found next to uninstall.ps1: $uninstallScript"
    exit 1
}

if ($ForwardedArgs) {
    & $python.Source $uninstallScript @ForwardedArgs
} else {
    & $python.Source $uninstallScript
}
exit $LASTEXITCODE
