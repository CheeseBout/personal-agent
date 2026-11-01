# ADR-0009: UI architecture (Phase 7)

- Status: accepted

## Decision
- **QtWebSockets, not asyncio/qasync.** The UI client uses `QWebSocket` so the Qt event loop is the only loop: no extra dependency, no bridge between two loops.
- **A pure-Python model between events and windows** (`ui/model.py`, `ui/avatar_state.py`). Windows render it; the logic (plan status, approval normalisation, avatar priority) is unit-tested without Qt.
- **Speech-to-text lives in the daemon, text-to-speech in the UI.** STT is a metered model call that must respect the daily cap and serve audio attachments too; TTS is free and needs the sound device.
- **Settings are whitelisted.** The UI may change model choices, languages, daily caps, voice and UI preferences; `workers`, MCP, RAG folders and per-task budgets are not editable over the API. Secrets are write-only.
- **Avatar click-through uses the window mask** (rebuilt per frame) instead of platform hit-testing: simple, portable, no per-pixel polling.
- **One timer for state changes** (flash expiry, falling asleep) scheduled from `next_change_in()`; the animation timer runs only while the avatar is visible. No polling loops when idle.
- **Default skin drawn in code** so the repo ships no binary art; skins are plain folders (see `docs/skins.md`).

## Consequences
- Window behaviour that depends on a real compositor (transparency, always-on-top, multi-monitor, tray) cannot be asserted in offscreen tests.
- Two processes at idle cost about 200 MB today; trimming is Phase 9 work.
