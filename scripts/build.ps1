# Builds the Windows bundle in dist\personal-agent:
#   agent-core.exe, agent-chat.exe   the daemon and the terminal chat (PyInstaller, one folder)
#   ui\agent-ui.exe                  the desktop UI (Electron, built from electron-ui\; needs Node.js 20+)
#   .\scripts\build.ps1                 build
#   .\scripts\build.ps1 -Smoke          build, then start the packaged daemon on a spare port and check /health, and the packaged UI
# Optional installer: see docs/development.md (Packaging; Inno Setup script in packaging\installer.iss).
param([switch]$Smoke)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m uv sync --group build --extra voice --extra screen --extra observability --extra mcp
python -m uv run pyinstaller packaging/personal-agent.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
npm --prefix electron-ui ci
if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
npm --prefix electron-ui run dist
if ($LASTEXITCODE -ne 0) { throw "the Electron build failed" }
if (Test-Path dist\personal-agent\ui) { Remove-Item dist\personal-agent\ui -Recurse -Force }
Copy-Item electron-ui\dist\win-unpacked dist\personal-agent\ui -Recurse
$size = [math]::Round((Get-ChildItem dist\personal-agent -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "built dist\personal-agent ($size MB)"
if ($Smoke) { python -m uv run python scripts/smoke_packaged.py }
