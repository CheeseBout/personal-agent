# Phase 0 – Plan

## Environment (verified)
Windows 11, Python 3.12.10, git 2.55, Docker 29.7.2, Node (for Codex), codex-cli 0.163.0 (logged in), uv 0.12.22 (installed via `python -m pip install --user uv`; run as `python -m uv ...` until Scripts dir is on PATH).

## Stack decisions
See `docs/adr/`. PySide6, SQLite+sqlite-vec+FTS5, cloud embeddings default, keyless search default, two-process split with token, Codex via `codex exec`.

## Pins
Pinned in `pyproject.toml` from PyPI latest on 2026-10-31; `uv lock` resolves 162 packages including every extra. `tenacity` is intentionally not pinned directly (`google-genai` needs `<9.2.0`). LangGraph/Langfuse/MCP-adapter *APIs* are not yet checked against docs; each is checked in the phase that first uses it.

## Resource targets (measured in Phase 9, tracked from Phase 1)
- Idle daemon (no optional local models): <= 100 MB RSS
- Idle UI + avatar: <= 60 MB RSS  (combined < 150 MB)
- Idle CPU: ~0% (no polling; avatar timer only while animating)
- Daemon cold start to ready: <= 3 s; Docker/Langfuse/embedding model: lazy only

## Assumptions (no blocking questions)
- Cloud keys will be entered in the first-run wizard; none are needed to run Phase 1 tests (mock LLMs).
- Workspace folder default: `%USERPROFILE%\Documents\agent-workspace`; data under `%APPDATA%\personal-agent\`.
- Config model names in `config/default.yaml` are placeholders, verified against provider docs in Phase 1.

## Risks
| Risk | Mitigation |
|---|---|
| Fast-moving LangGraph/LangChain APIs | Exact pins, lockfile, check docs per phase, thin adapters in `llm/` and `graph/` |
| sqlite-vec is pre-1.0 | VectorStore interface, Windows load test in Phase 4 |
| Prompt injection via web/email/OCR | Untrusted-data tagging, T2 gate for actions derived from such content, injection tests (Phase 3) |
| Docker Desktop absent/slow | Lazy start, graceful fallback to all-T2 |
| Qt + asyncio in one process | UI is a pure client of the HTTP/WS API; qasync or a worker thread for WS |
| Codex has no MCP mode in this version | ADR-0006 (`codex exec`) |
| Secrets leakage in logs/traces | keyring only, redaction filter in structlog and Langfuse hooks, test in Phase 9 |
