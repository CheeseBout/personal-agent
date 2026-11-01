# Development launcher: starts the daemon (if it is not running) and the desktop UI from the repo.
#   .\scripts\run.ps1            daemon + UI
#   .\scripts\run.ps1 -Chat      daemon + terminal chat
param([switch]$Chat, [switch]$NoUi)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m uv sync --extra ui --extra voice --extra screen --extra observability --extra mcp | Out-Null
$up = $false
try { $up = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8765/health).StatusCode -eq 200 } catch {}
if (-not $up) {
    Write-Host "starting the daemon..."
    Start-Process -WindowStyle Hidden python -ArgumentList "-m", "uv", "run", "agent-core"
    1..40 | ForEach-Object { Start-Sleep -Milliseconds 250; try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8765/health).StatusCode -eq 200) { $up = $true } } catch {}; if ($up) { return } }
}
if (-not $up) { throw "the daemon did not come up on 127.0.0.1:8765 - run 'python -m uv run agent-core' to see why" }
if ($Chat) { python -m uv run agent-chat } elseif (-not $NoUi) { python -m uv run agent-ui }
