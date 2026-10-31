# ADR-0001: PySide6 for the desktop UI

- Status: accepted (Phase 0)

## Decision
Use PySide6 (Qt for Python), not PyQt6.

## Why
Same Qt 6 API; PySide6 is LGPL (official Qt project), PyQt6 is GPL/commercial. No licence friction if the app is ever distributed.

## Consequences
UI deps live in the optional 'ui' extra; the daemon never imports Qt.
