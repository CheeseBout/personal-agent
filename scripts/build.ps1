# Builds the Windows bundle: dist\personal-agent\{agent-core.exe, agent-ui.exe, agent-chat.exe} (PyInstaller, one folder).
#   .\scripts\build.ps1                 build
#   .\scripts\build.ps1 -Smoke          build, then start the packaged daemon on a spare port and check /health
# Optional installer: see docs/development.md (Packaging; Inno Setup script in packaging\installer.iss).
param([switch]$Smoke)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m uv sync --group build --extra ui --extra voice --extra screen --extra observability --extra mcp
python -m uv run pyinstaller packaging/personal-agent.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$size = [math]::Round((Get-ChildItem dist\personal-agent -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "built dist\personal-agent ($size MB)"
if ($Smoke) { python -m uv run python scripts/smoke_packaged.py }
