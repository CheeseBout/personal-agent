# personal-agent

Self-hosted, single-user agentic AI for Windows (LangGraph ReAct, Vietnamese + English, cloud-first LLMs, desktop companion).
Full specification: `PROMPT.md`. Plan and decisions: `docs/phase0.md`, `docs/adr/`.

## Dev quick start
```powershell
python -m pip install --user uv
python -m uv sync --group dev
python -m uv run pytest
python -m uv run ruff check .
python -m uv run mypy src
```

## Run (Phase 1)
1. Provide at least one provider key. Easiest: `copy .env.example .env` and fill in `ANTHROPIC_API_KEY=...`
   (`.env` is git-ignored). Lookup order: real env vars > `.env` (`PERSONAL_AGENT_ENV_FILE`, `./.env`, then
   `%APPDATA%\personal-agent\.env`) > Windows Credential Manager (service `personal-agent`). Default models are in
   `config/default.yaml`; override in `%APPDATA%\personal-agent\config.yaml`.
2. Create the workspace folder the agent may read: `%USERPROFILE%\Documents\agent-workspace` (see `config/policy.yaml`).
3. Terminal 1: `python -m uv run agent-core` (daemon on 127.0.0.1:8765). Terminal 2: `python -m uv run agent-chat`.

The per-install API token is created on first start and stored in Credential Manager; logs go to `%APPDATA%\personal-agent\logs\`.

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
Cheap Haiku handles chat and bookkeeping; Sonnet is used for planning, tool work on planned tasks and verification.
No software can guarantee an API key never runs out - also set a spend limit in the Anthropic Console. Details: `docs/phase2.md`.
Real-provider checks (cost a few cents): `python -m uv run pytest -m live -s`.

## Phase 1 behaviour and limits
- Tools: `file_read` (workspace only), `web_search` (DuckDuckGo, results flagged untrusted), `shell` (tiny allowlist of read-only commands, run without a shell; everything else is refused until approvals exist in Phase 3).
- T2/T3 tools are denied (no HITL yet). `GET /health` is intentionally unauthenticated and returns only `{"status":"ok"}`.
- Not verified here: calls to real LLM providers (no API key was available); tests use a scripted fake model.
