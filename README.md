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
