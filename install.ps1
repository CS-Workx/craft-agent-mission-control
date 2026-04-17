# Mission Control installer — PowerShell wrapper around install.py.
#
# The real logic lives in install.py (cross-platform Python, stdlib-only).
# This wrapper exists so Windows users can run `.\install.ps1`.
#
# Usage:
#   pwsh install.ps1                    # interactive
#   pwsh install.ps1 -Yes               # non-interactive
#   pwsh install.ps1 -NoAutostart       # skip scheduled task
#   pwsh install.ps1 -Port 9753         # pick a port
#   pwsh install.ps1 -Help              # pass through

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

$installScript = Join-Path $ScriptDir "install.py"
if (-not (Test-Path $installScript)) {
    Write-Error "install.py not found next to install.ps1: $installScript"
    exit 1
}

if ($ForwardedArgs) {
    & $python.Source $installScript @ForwardedArgs
} else {
    & $python.Source $installScript
}
exit $LASTEXITCODE
