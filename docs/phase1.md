# Phase 1 – Core skeleton: report

## Built
Settings (YAML + user override, keyring secrets), LLM factory (4 providers, role routing, per-role fallbacks, lazy SDK imports), FastAPI daemon with token-protected WebSocket, SQLite checkpointer, minimal LangGraph ReAct graph (policy guard on every tool call), tools `file_read` / `web_search` / `shell`, `agent-chat` CLI, structured JSON logging with rotation + redaction.

## Verified (actually run)
- `ruff`, `mypy --strict` (27 files), `pytest`: 45 passed (mock LLM; policy, path forms incl. junction/ADS/UNC, shell refusals, graph loop, checkpoint history, API auth/origin, redaction, fallbacks).
- Real daemon process: `/health` ok, no-token WebSocket rejected, turn without key returns `no_api_key`, no wildcard bind.
- Idle daemon RSS ≈ 86 MB (target ≤ 100 MB).

## Codex review (read-only `codex exec`, 2 rounds)
Round 1: shell allowlist unsafe (fixed: argv exec, regex full-match, no PowerShell/git), Windows path forms (fixed), secret leakage in tool output/events (redaction added), Origin port regex (fixed).
Round 2: workspace executable hijack (fixed: absolute exe, refused if inside workspace); redaction limited to known formats (widened, still best-effort).
Codex could not execute Python in its sandbox, so its checks are code review only; test results above are mine. Status: **not fully independently verified**.

## Known gaps (accepted for Phase 1)
- Redaction is pattern-based, unknown credential formats pass through.
- `file_read` has a check-then-open race (junction swapped between check and open); requires another writer in the workspace.
- `GET /health` is public by design (status only). Drive-relative paths (`C:foo`) are not rejected explicitly, but containment still applies after `realpath`.
- Rate limits, cost/token accounting, no-progress detection, cancel/pause: Phase 2/3. Agent name is read at graph build, live rename arrives with Phase 2.
- Real provider calls untested (no API key). Model IDs in `config/default.yaml` still need confirming against provider docs.
- LangGraph API usage was checked against the installed package (`ToolNode` `awrap_tool_call`, `AsyncSqliteSaver`), not the online docs.

## Next: Phase 2 (intent classifier, planner/Goal, verifier, budgets, no-progress, compaction, vi/en, rename).
