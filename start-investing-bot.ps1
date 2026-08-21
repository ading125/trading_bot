param(
    [switch]$UnlockCredentials
)

$ErrorActionPreference = "Stop"
$unlockArgument = if ($UnlockCredentials) { " --unlock-credentials" } else { "" }
$linuxCommand = 'INVESTING_BOT_DATA_DIR="$PWD/data" .venv/bin/python -m investing_bot serve' + $unlockArgument

Write-Host "Starting Investing Bot at http://127.0.0.1:8000/"
wsl.exe --cd $PSScriptRoot bash -lc $linuxCommand

if ($LASTEXITCODE -ne 0) {
    throw "Investing Bot exited with status $LASTEXITCODE"
}
