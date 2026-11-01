# Packaging for Windows

## What you get
`scripts\build.ps1` builds **one folder**, `dist\personal-agent`, with three programs sharing one copy of the libraries:

| Program | What | Console |
|---|---|---|
| `agent-core.exe` | the daemon (API on 127.0.0.1:8765) | yes |
| `agent-ui.exe` | tray, chat, approvals, avatar, voice; starts the daemon next to it when it is not running | no |
| `agent-chat.exe` | terminal chat client | yes |

Size: about 416 MB (PySide6, LangChain provider SDKs, pywinauto/comtypes, numpy/scipy for image hashing). Build time: about 10 minutes.
Use `-Smoke` or `python -m uv run python scripts/smoke_packaged.py [--chat]` to check the result.

## How it finds things
- `config/` (defaults, shipped policy, default avatar) and `docker/sandbox/` are bundled and found through `settings.app_root()` (the PyInstaller bundle, or the repo in development).
- Everything you can change lives in `%APPDATA%\personal-agent\`: `config.yaml` (settings), **`policy.yaml`** (your edited safety policy; the shipped copy stays the default and may sit in a read-only
  install folder), `mcp.yaml`, `skins\`, databases, `logs\` (JSON, rotated), `.env` (optional keys). Uninstalling does not delete this folder.
- API keys: Windows Credential Manager (set from the first-run wizard / Settings), or `%APPDATA%\personal-agent\.env`.
- Auto-start: Settings -> General -> "Start with Windows" (HKCU Run key); the installer script has the same option.

## Installer
`packaging\installer.iss` is an [Inno Setup](https://jrsoftware.org/isinfo.php) script (per-user install, shortcuts, optional auto-start, stops the programs on uninstall, keeps your data):
```powershell
.\scripts\build.ps1
iscc packaging\installer.iss          # -> dist\PersonalAgentSetup.exe
```
**It was written but not compiled in this repository** (Inno Setup is not installed on the development machine): treat it as a starting point. Code signing is not set up; an unsigned
installer triggers SmartScreen warnings.

## Verified (2026-11-01)
- Build completes (twice: before and after the review fixes); `agent-core.exe` starts without any environment help, answers `/health` in 3.6 s warm (up to ~9 s the very first time after a build, while Windows
  scans the new files), finds the bundled config and policy, enforces the token.
- A real question (Haiku classifier/planner, Gemini worker) is answered by the packaged daemon: the lazily imported provider SDKs are in the bundle.
- `agent-ui.exe` starts and keeps running (offscreen); `agent-chat.exe` starts.
- Idle memory of the packaged daemon: about 119 MB at start, about 198 MB after the first real question (same order as the source install, see ADR-0011).

## Not verified / known gaps
- Screen perception/control (UI Automation), voice (edge-tts, microphone), MCP servers and Langfuse inside the bundle were not exercised; their packages and hidden imports are included but only
  the daemon, a chat turn and the UI start-up were run from the bundle.
- The installer script, uninstall flow, upgrade-over-existing-install and code signing.
- Docker sandbox image build from the bundle (needs Docker and network; the Dockerfile is bundled).
- Antivirus products sometimes flag unsigned PyInstaller executables.
