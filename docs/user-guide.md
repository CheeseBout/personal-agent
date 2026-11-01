# User guide

## Install and run
```powershell
python -m pip install --user uv
.\scripts\run.ps1            # installs everything, starts the daemon and the desktop UI
.\scripts\run.ps1 -Chat      # terminal chat instead of the UI
```
Manually: `python -m uv sync --extra ui --extra voice --extra screen --extra observability --extra mcp`, then `python -m uv run agent-core` (daemon, 127.0.0.1:8765) and `python -m uv run agent-ui` (or `agent-chat`).
`agent-ui` starts the daemon itself when it is not running. A packaged Windows build is described in [development.md](development.md).

**Keys.** The first-run wizard stores a key in Windows Credential Manager and tests it. Alternatively copy `.env.example` to `.env` (git-ignored) and fill in `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, ...
Lookup order: environment > `.env` (`PERSONAL_AGENT_ENV_FILE`, `./.env`, `%APPDATA%\personal-agent\.env`) > Credential Manager. Keys are write-only in the UI and masked everywhere they could leak.
**Ollama** (`ollama pull bge-m3`) is needed for document search and memory similarity. **Docker Desktop** is needed only for sandboxed code.

**Folders.** The agent may only use folders allowed in `policy.yaml` (default `%USERPROFILE%\Documents\agent-workspace`); add more in Settings → General → "Add a folder". Data lives in `%APPDATA%\personal-agent\`.

## Talking to the agent
Vietnamese or English; it answers in your language and introduces itself by its configured name (Settings → General, takes effect on the next turn). Attach files (button, drag-drop, paste an image): images, text/md/csv, docx, pdf, audio (transcribed); at most 4 files and 15 MB.
Anything you attach is treated as untrusted text (see Approvals). Every answer shows steps, tokens and cost; the side panel shows the goal, success criteria, step status and today's budget.

Terminal commands (`agent-chat`): `/usage` `/name <n>` `/audit` `/rules` `/kill` `/resume-kill` `/memory` `/memory-ok <id>` `/memory-del <id>` `/rag` `/schedules` `/schedule-cancel <id>` `/mcp` `/mcp-reload` `/exit`.

## Approvals and the kill switch
Reads are automatic. Anything that changes the real world (writes, shell commands, sending mail, clipboard, scheduling, mouse/keyboard, applying sandbox changes) stops at an **approval card**:
tool, risk, why, the exact arguments (editable), a diff/preview and an "untrusted content seen" warning. Choose **approve**, **approve and remember** (only for low-risk file edits in one folder), **reject** (with a reason the agent sees) or edit the arguments (the edited call is what runs).
Dangerous (T3) actions are denied; if you enable them they need you to type `CONFIRM`. An unanswered card expires after 10 minutes and counts as a rejection. Pending cards survive restarts and are reachable from the tray, the avatar bubble and the chat.
**Kill switch:** `Ctrl+Alt+K`, the tray/avatar menu, "Stop all" in the chat, `/kill`, or `POST /kill`: cancels running turns, kills host subprocesses, stops the container and screen watching, rejects pending approvals. Release it from the menu (`/resume-kill`).

## Desktop UI
- **Tray** (chat, approvals, manager pages, settings, stop speaking, kill/resume, quit — quitting the UI leaves the agent running) and **global hotkeys**: `Ctrl+Alt+K` kill, `Ctrl+Alt+A` chat, `Ctrl+Alt+Space` talk (press to start, press again or stop talking to send), `Ctrl+Alt+S` capture the screen, `Ctrl+Alt+W` watch the screen. Change them in `ui.hotkeys`.
- **Avatar:** transparent always-on-top companion with states (idle, listening, thinking, working, speaking, awaiting approval, success, error, sleeping). Click = quick input, double-click = chat, drag, snap to edges, right-click menu, drop files on it, speech bubble. Scale, skin, wander and always-on-top in Settings. Skins: [development.md](development.md#avatar-skins).
- **Manager:** Memory, Documents (RAG), Tools/MCP, Schedules, Remembered approvals, Audit log, Usage (tokens, cost, error rate, tool success per day), Policy (YAML editor, validated, backup kept, applies immediately).
- **Settings:** names and languages, autostart, avatar, models per role (with a Test button), API keys, voice, daily budgets, screen rules, About (live CPU/RAM of UI and daemon).
- **Voice:** record with the mic button or the talk hotkey (stops on 1.5 s of silence; transcribed by Gemini, about $0.00002 a clip). "Read answers aloud" uses edge-tts (free, needs internet), sentence by sentence, interrupted when you talk or press stop.

## Screen
The agent can list windows, look at the screen (vision model; the image is sent to the cloud only then), read text locally (OCR; Vietnamese needs the Windows OCR language pack), read a window's UI Automation tree, and — each call approved by you — click, type and press keys (≤ 8 steps per approval).
Before each action the target is outlined; moving the mouse or the kill switch stops it. Every look is announced ("the agent looked at your screen").
**Capture** (`Ctrl+Alt+S`, menu: active window, region) attaches an image to the chat. **Watching** (`Ctrl+Alt+W`) shows a red badge and a red dot on the avatar, keeps nothing on disk and switches itself off after 30 minutes.
Password managers, banking/sign-in windows and wallets are blacked out of images and never controlled (edit the list in Settings → Screen). Rules: [security.md](security.md).

## Documents, memory, integrations
- **Documents:** ask the agent to `rag_ingest` files from the workspace, or list folders under `rag.watch`; answers cite file / heading / page.
- **Memory:** the agent saves facts (`remember`) and proposes more after planned tasks; proposals from tasks that read outside text, sensitive ones and low-confidence ones wait for your approval in Manager → Memory.
- **Email:** set `EMAIL_IMAP_HOST`, `EMAIL_SMTP_HOST`, `EMAIL_USER`, `EMAIL_PASSWORD` (app password) in `.env`. **Calendar:** a local `calendar.ics` in the workspace. **HTTP APIs:** list hosts and secret names under `http.hosts` in `policy.yaml`; the model names a secret, the tool injects it.
- **MCP servers:** `%APPDATA%\personal-agent\mcp.yaml` ([development.md](development.md#add-an-mcp-server)); enable/disable and reload in Manager.
- **Schedules:** ask for a recurring task; runs use the same policy, start untrusted (side effects need approval) and are limited to 20 jobs, ≥ 5 minutes apart.

## Configuration
`config/default.yaml` holds the defaults; your overrides go in `%APPDATA%\personal-agent\config.yaml` (the UI writes there; a broken file is set aside and the defaults are used). Main keys:
`models` (role → provider/model/fallbacks), `pricing`, `budgets` (per task: 25 steps, 40 tool calls, 600 s, 150k tokens, $0.50; daily: 1M tokens, $2.00), `workers` (parallel sub-agents, per-role tools), `rag`, `memory`, `scheduler`, `context`, `observability`, `voice`, `ui`, `screen`.
Models can be changed live in Settings. Set a spend limit in your provider console too: no software can guarantee a key never runs out, and daily caps can be overshot by one call chain.

## Observability and evals
Local metrics are always on. Langfuse tracing: put `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` in `.env` (`observability.langfuse_enabled` is on but inert without keys; if unreachable the agent just runs).
`python -m uv run agent-eval [--lang vi] [--only id] [--out run.json] [--compare tests/evals/baseline.json]` runs 10 Vietnamese/English scenarios on the real models (about $0.01) and records pass rate, steps and cost.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "no API key" / quota error | add a key (Settings → API keys), top up, or rely on the fallback chain; the error code says which |
| Document search says embeddings unavailable | start Ollama and `ollama pull bge-m3` |
| `sandbox_*` says Docker is unavailable | start Docker Desktop; other tools keep working |
| Hotkey unavailable | another program owns it; change `ui.hotkeys` (the tray tells you) |
| A file was renamed `*.corrupt-…` / `*.invalid-…` | it was damaged; the agent started fresh and kept the file for you |
| First answer is slow | the embedding model is loading; later answers take a few seconds |
