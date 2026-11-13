# personal-agent

A self-hosted, single-user agentic assistant for Windows: LangGraph agent with tools, memory, document search, parallel sub-agents, a safety gate with human approvals, a Docker sandbox, a desktop companion (Electron: tray, chat, avatar, voice)
and screen perception/control. Vietnamese and English; cloud LLMs by default (Claude Haiku, Gemini flash-lite), local embeddings.

## What it does
- **Chats and plans:** classifies a request, answers simple ones directly, plans harder ones with checkable success criteria, runs independent lookups in parallel sub-agents and verifies the result. Token, cost, step and daily budgets are enforced in code.
- **Uses tools safely:** files, shell, code in a Docker sandbox, web, email, calendar, clipboard, HTTP APIs, MCP servers, documents (RAG with citations), long-term memory, schedules. Anything that changes the real world stops at an **approval card**; text read from the web, files, mail or the screen can never trigger an action on its own.
- **Sees and operates the screen:** window list, vision, local OCR, UI Automation, and guarded mouse/keyboard control with privacy masking, on-screen highlight and abort on mouse move or the kill switch.
- **Stays in your control:** kill switch (`Ctrl+Alt+K`), audit log, redaction of secrets, local-only API with a token, nothing sent anywhere you did not configure.

## Quick start
```powershell
python -m pip install --user uv
.\scripts\run.ps1            # installs everything, starts the daemon and the avatar; open the chat from the avatar or the tray (a wizard asks for a key)
.\scripts\run.ps1 -Window    # same, and open the chat window at start
.\scripts\run.ps1 -Chat      # terminal chat instead
```
Add a key in the wizard or copy `.env.example` to `.env` (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, ...). Optional: Ollama (`ollama pull bge-m3`) for document search, Docker Desktop for sandboxed code, Langfuse keys for tracing.
Requirements: Windows 10/11, Python 3.12+, and Node.js 20+ for the desktop UI (not needed for the daemon or the terminal chat).

## Documentation
| | |
|---|---|
| [docs/user-guide.md](docs/user-guide.md) | install, keys, chat, approvals, desktop UI, hotkeys, voice, screen, documents, memory, integrations, configuration, troubleshooting |
| [docs/architecture.md](docs/architecture.md) | processes, a turn through the graph, models, tools, knowledge, sandbox, screen, storage, footprint (with diagram) |
| [docs/security.md](docs/security.md) | threat model, policy tiers, approvals, sandbox, screen rules, review results, residual risks |
| [docs/development.md](docs/development.md) | setup, tests and evals, packaging, how to add a tool / MCP server / provider / skin, conventions, known gaps |
| [docs/decisions.md](docs/decisions.md) | design decisions and the measured resource targets |


## Development
```powershell
python -m uv sync --group dev --extra mcp --extra voice --extra screen --extra observability
python -m uv run pytest && python -m uv run ruff check . && python -m uv run mypy src
cd electron-ui; npm install; npm run typecheck; npm test; npm run e2e      # the desktop UI
```
Entry points: `agent-core` (daemon), `agent-chat` (terminal), `agent-eval` (scenario evals); the desktop UI is the Electron app in `electron-ui/` (`npm run dev`). Build a Windows bundle with `.\scripts\build.ps1`.
