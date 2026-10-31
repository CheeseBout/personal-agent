# ADR-0006: Codex as independent tester via 'codex exec', not MCP

- Status: accepted (Phase 0)

## Decision
codex-cli 0.163.0 has no 'mcp-server' subcommand (verified: 'codex mcp-server' is parsed as a prompt and fails with 'stdin is not a terminal'). Use 'codex exec -s read-only --ephemeral' with a self-contained brief per phase.

## Why
Satisfies the intent of PROMPT 13.7 (independent tester/reviewer, read-only, no commits) while the MCP entry point does not exist. Smoke test passed (returned CODEX_OK).

## Consequences
Deviation from the 'via MCP' wording; revisit if a later Codex release restores an MCP server mode. Never pass secrets in briefs.
