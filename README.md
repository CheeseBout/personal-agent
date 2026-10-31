# personal-agent

Self-hosted, single-user agentic AI for Windows (LangGraph ReAct, Vietnamese + English, cloud-first LLMs, desktop companion).
Full specification: `PROMPT.md`. Plan and decisions: `docs/phase0.md`, `docs/adr/`.

## Dev quick start
```powershell
python -m pip install --user uv
python -m uv sync --group dev
python -m uv run pytest
python -m uv run ruff check .
```
Status: Phase 0 (plan + skeleton). No runnable agent yet.
