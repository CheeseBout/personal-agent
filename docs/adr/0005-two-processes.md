# ADR-0005: Daemon + UI split, local API protected by token

- Status: accepted (Phase 0)

## Decision
agent-core (FastAPI + WebSocket on 127.0.0.1) and agent-ui (PySide6) are separate processes; a random per-install token is stored in keyring and required on every request/WS.

## Why
UI can restart without killing tasks; avatar process stays light; localhost binding alone does not stop other local processes/browsers.

## Consequences
Token generated on first run; CORS disabled; WS checks Origin and token.
