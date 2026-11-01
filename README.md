# personal-agent

Self-hosted, single-user agentic AI for Windows (LangGraph ReAct, Vietnamese + English, cloud-first LLMs, desktop companion).
Full specification: `PROMPT.md`. Plan and decisions: `docs/phase0.md`, `docs/adr/`.

## Quick start
```powershell
python -m pip install --user uv
.\scripts\run.ps1            # installs everything, starts the daemon and the desktop UI (first run opens a setup wizard)
.\scripts\run.ps1 -Chat      # or the terminal chat instead of the UI
```
Documentation map: `docs/architecture.md` (diagram), `docs/howto.md` (add a tool / MCP server / provider / skin), `docs/security.md` (threat model and review),
`docs/packaging.md` (Windows bundle and installer), `docs/phase1.md` ... `docs/phase9.md` (what each phase built, how it was verified, known gaps), `docs/adr/` (decisions).

## Dev quick start
```powershell
python -m uv sync --group dev --extra mcp --extra ui --extra voice --extra screen --extra observability   # everything the tests need
python -m uv run pytest                     # unit, integration, UI (offscreen) and failure-injection tests
python -m uv run ruff check .
python -m uv run mypy src
python -m uv run python scripts/profile_resources.py   # idle memory / CPU / start-up time
```
Real-desktop and real-model checks (they use your keys and move your mouse; read the header of each script first): `scripts/desktop_check.py`, `scripts/screen_check.py`,
`scripts/agent_screen_e2e.py`, `python -m uv run agent-eval`.

## Run (Phase 1)
1. Provide at least one provider key. Easiest: `copy .env.example .env` and fill in `ANTHROPIC_API_KEY=...`
   (`.env` is git-ignored). Lookup order: real env vars > `.env` (`PERSONAL_AGENT_ENV_FILE`, `./.env`, then
   `%APPDATA%\personal-agent\.env`) > Windows Credential Manager (service `personal-agent`). Default models are in
   `config/default.yaml`; override in `%APPDATA%\personal-agent\config.yaml`.
2. Create the workspace folder the agent may read: `%USERPROFILE%\Documents\agent-workspace` (see `config/policy.yaml`).
3. Terminal 1: `python -m uv run agent-core` (daemon on 127.0.0.1:8765). Terminal 2: `python -m uv run agent-chat`.

The per-install API token is created on first start and stored in Credential Manager; logs go to `%APPDATA%\personal-agent\logs\`.

## Phase 8: screen perception and control
`python -m uv sync --extra screen --extra ui --extra voice`. The agent can list windows, look at the screen with a vision model, read text locally (OCR), read a window's
UI Automation tree and, with your approval for every call, click and type. Capture with `Ctrl+Alt+S`; continuous watching with `Ctrl+Alt+W` (red badge, auto-off, nothing saved).
Password managers, banking and sign-in windows are blacked out; shells and the agent's own windows can never be controlled. Details, safety properties and limits: `docs/phase8.md`.
Real-desktop checks: `python -m uv run python scripts/screen_check.py` and `scripts/agent_screen_e2e.py` (they move your mouse and type into a test window).

## Phase 7: desktop UI
`python -m uv sync --extra ui --extra voice`, then `python -m uv run agent-ui`: tray icon, chat (markdown, attachments, pasted images, voice),
approval center with diffs, manager pages (memory, documents, MCP, schedules, audit, usage, policy), settings, first-run wizard and a desktop
avatar with skins (`docs/skins.md`). Hotkeys: `Ctrl+Alt+K` kill switch, `Ctrl+Alt+Space` talk, `Ctrl+Alt+A` chat. Vietnamese and English.
Details, defaults and what was not verified on a real desktop: `docs/phase7.md`.

## Phase 6: observability and evals
Local metrics (latency, error rate, tool success per day) are always on: `/usage` in `agent-chat`. Optional Langfuse tracing: set
`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` in `.env` and run `python -m uv sync --extra observability`.
Eval harness (real models, about $0.01): `python -m uv run agent-eval [--lang vi] [--compare tests/evals/baseline.json]`.
Details and measured findings: `docs/phase6.md`.

## Phase 5: sub-agents
For plans with two or more independent lookups the supervisor hands them to small parallel workers (cheap Haiku by default), each with
its own context and only the tools of its role (`workers:` in `config/default.yaml`). Workers never ask you for approval: if something
needs it they stop and report it, and the supervisor asks you through the normal approval card. `agent-chat` shows
`[worker s1/web] done ...` lines and the roles in the plan. Switch off with `workers.enabled: false`. Details and limits: `docs/phase5.md`.

## Phase 4: tools, knowledge and memory
- Documents: put md/docx/pdf in the workspace, ask the agent to `rag_ingest` them (or list a folder under `rag.watch` in
  `config/default.yaml` to keep it indexed automatically), then ask questions; answers cite file / heading / page.
- Memory: the agent saves facts with `remember`; after planned tasks it *proposes* more. Review them with `/memory`, approve with
  `/memory-ok <id>`, delete with `/memory-del <id>`. Nothing sensitive is stored without your approval.
- Other tools: email (set `EMAIL_*` in `.env`), calendar (`calendar.ics` in the workspace), clipboard, web, HTTP APIs
  (allow-list hosts under `http.hosts` in `config/policy.yaml`), MCP servers (`%APPDATA%\personal-agent\mcp.yaml`, see
  `src/personal_agent/mcp_client.py`), scheduling (`schedule_task`, approval required). Details and limits: `docs/phase4.md`.
- Desktop-touching tests (clipboard): `python -m uv run pytest -m desktop`.

## Phase 3: safety
Writes, shell commands and anything touching existing files stop at an **approval card** (`y` / `n` + reason / `e` edit args /
`r` remember for that folder). `/kill` in `agent-chat` (or `POST /kill` with the token) halts everything immediately.
Policy lives in `config/policy.yaml` (hot-reloaded). Sandbox tools need Docker Desktop running; without it they report
that clearly and everything else keeps working. Audit trail: `/audit`. Details and limits: `docs/phase3.md`.
Real-container tests (need Docker running): `python -m uv run pytest -m docker`.

## Phase 2: budgets and quota protection
Per-task and **daily** caps live in `config/default.yaml` (`budgets:`; defaults: 1M tokens / $2 per day). Override in
`%APPDATA%\personal-agent\config.yaml`. In `agent-chat`: `/usage` shows today's totals, `/name <name>` renames the agent.
Defaults (no Sonnet): local `gemma4:e4b` classifies intent, Haiku plans and verifies, `gemini-3.5-flash-lite` does chat, summaries and tool work, local `bge-m3` embeds documents (needs Ollama running; `ollama pull gemma4:e4b bge-m3`).
No software can guarantee an API key never runs out - also set a spend limit in the Anthropic Console. Details: `docs/phase2.md`.
Real-provider checks (cost a few cents): `python -m uv run pytest -m live -s`.

## Phase 1 behaviour and limits
- Tools: `file_read` (workspace only), `web_search` (DuckDuckGo, results flagged untrusted), `shell` (tiny allowlist of read-only commands, run without a shell; everything else is refused until approvals exist in Phase 3).
- T2/T3 tools are denied (no HITL yet). `GET /health` is intentionally unauthenticated and returns only `{"status":"ok"}`.
- Not verified here: calls to real LLM providers (no API key was available); tests use a scripted fake model.
