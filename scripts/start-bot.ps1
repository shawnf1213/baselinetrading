# Runs the bot on this PC: backend, strategy engine and UI on http://127.0.0.1:8000.
# If the server stops, it is restarted after 15 seconds. Close this window to stop the bot.
#
#   Start-Process powershell -ArgumentList '-NoExit', '-File', 'scripts\start-bot.ps1' -WindowStyle Minimized
#
# The keys come from your Windows user environment (never from this file):
# APCA_API_KEY_ID, APCA_API_SECRET_KEY (Alpaca paper) and BASELINE_UI_TOKEN.

Set-Location (Split-Path $PSScriptRoot -Parent)
foreach ($name in "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "BASELINE_UI_TOKEN") {
    $value = [Environment]::GetEnvironmentVariable($name, "User")
    if (-not $value) { throw "$name is not set in your Windows user environment" }
    Set-Item "env:$name" $value
}
$Host.UI.RawUI.WindowTitle = "baselinetrading bot - http://127.0.0.1:8000"

while ($true) {
    Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') starting the server"
    & .\.venv\Scripts\python.exe -m uvicorn baselinetrading.server:app --host 127.0.0.1 --port 8000
    Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') the server stopped (exit code $LASTEXITCODE); restarting in 15 seconds. Close this window to stop the bot."
    Start-Sleep -Seconds 15
}
