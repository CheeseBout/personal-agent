# Development launcher: starts the daemon (if it is not running) and the desktop UI from the repo.
#   .\scripts\run.ps1            daemon + the avatar only (Electron, electron-ui\; needs Node.js 20+); open the chat from the avatar or the tray
#   .\scripts\run.ps1 -Window    same, and open the chat window at start
#   .\scripts\run.ps1 -Chat      daemon + terminal chat
param([switch]$Chat, [switch]$NoUi, [switch]$Window)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m uv sync --extra voice --extra screen --extra observability --extra mcp | Out-Null
$up = $false
try { $up = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8765/health).StatusCode -eq 200 } catch {}
if (-not $up) {
    Write-Host "starting the daemon..."
    Start-Process -WindowStyle Hidden python -ArgumentList "-m", "uv", "run", "agent-core"
    1..40 | ForEach-Object { Start-Sleep -Milliseconds 250; try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8765/health).StatusCode -eq 200) { $up = $true } } catch {}; if ($up) { return } }
}
if (-not $up) { throw "the daemon did not come up on 127.0.0.1:8765 - run 'python -m uv run agent-core' to see why" }
if ($Chat) { python -m uv run agent-chat }
elseif (-not $NoUi) {
    if (-not (Test-Path electron-ui\node_modules)) { npm --prefix electron-ui install }
    if (-not $Window) { $env:PERSONAL_AGENT_BACKGROUND = "1" }
    npm --prefix electron-ui run start
}
