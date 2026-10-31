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
1. Store at least one provider key (Windows Credential Manager, service `personal-agent`), e.g.
   `python -c "import keyring; keyring.set_password('personal-agent','ANTHROPIC_API_KEY','<key>')"`
   (an env var such as `ANTHROPIC_API_KEY` also works as a dev fallback). Default models are in `config/default.yaml`;
   override in `%APPDATA%\personal-agent\config.yaml`.
2. Create the workspace folder the agent may read: `%USERPROFILE%\Documents\agent-workspace` (see `config/policy.yaml`).
3. Terminal 1: `python -m uv run agent-core` (daemon on 127.0.0.1:8765). Terminal 2: `python -m uv run agent-chat`.

The per-install API token is created on first start and stored in Credential Manager; logs go to `%APPDATA%\personal-agent\logs\`.

## Phase 1 behaviour and limits
- Tools: `file_read` (workspace only), `web_search` (DuckDuckGo, results flagged untrusted), `shell` (tiny allowlist of read-only commands, run without a shell; everything else is refused until approvals exist in Phase 3).
- T2/T3 tools are denied (no HITL yet). `GET /health` is intentionally unauthenticated and returns only `{"status":"ok"}`.
- Not verified here: calls to real LLM providers (no API key was available); tests use a scripted fake model.
