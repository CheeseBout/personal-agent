# Decisions

Short records of the choices that shaped the design: what, why, and the consequence.

| Decision | Why | Consequence |
|---|---|---|
| **Two processes**: daemon (FastAPI + WebSocket on 127.0.0.1) and UI, with a random per-install token | the UI can restart without killing tasks; loopback alone does not stop other local processes or web pages | token generated on first run and kept in Credential Manager; CORS off; `Origin` and token checked on every connection |
| **PySide6**, not PyQt6 | same Qt 6 API, LGPL licence | UI dependencies live in the optional `ui` extra; the daemon never imports Qt |
| **QtWebSockets** in the UI, no asyncio bridge | one event loop, no extra dependency | the client reconnects by itself (0.3 s, 0.6 s, 1 s ... 4 s) |
| **A pure-Python model between events and windows** (`ui/model.py`, `ui/avatar_state.py`) | plan status, approvals and avatar priority are unit-testable without Qt | windows only render |
| **SQLite for everything**: sqlite-vec for vectors, FTS5 for keywords | already needed for the LangGraph checkpointer; tiny, no server | sqlite-vec is pre-1.0, so vector code is confined to `rag/hybrid.py`; hybrid score is **0.7 × cosine + 0.3 × coverage²** (reciprocal-rank fusion let one shared word outrank the right passage) |
| **Embeddings: local `bge-m3` via Ollama** (1024 dims), cloud/fastembed optional | multilingual (vi/en), free, no quota, no documents leaving the machine | needs Ollama running; changing model/dimension drops old vectors and re-indexes (the index records which model made them) |
| **Pluggable web search**, DuckDuckGo keyless by default | works on first run; better providers are a config change | keyless scraping can break |
| **Approval gate before the tools node**, one `interrupt()` per batch | LangGraph re-runs the interrupted node on resume; a pure, idempotent gate avoids running tools twice | pending approvals live in the checkpoint and in `approvals.db` (both must agree); an approved edit must keep the same risk tier; resume values are always a non-empty dict (`resume_command`) because LangGraph ignores a falsy one |
| **Taint is sticky per thread** | injected text stays in the history and can keep steering later turns | start a new thread for a clean slate; tainted ⇒ T1 side effects need approval and remembered rules are ignored |
| **The agent cannot change its own rules**: policy/config/MCP/databases live in a denied folder; settings changes go through a whitelisted, authenticated API; secrets are write-only | an agent that can edit its policy has no policy | policy edits made in the UI are saved to `%APPDATA%\personal-agent\policy.yaml`, the shipped file is only the default |
| **Models: Haiku 5.5 for plan/verify, local `gemma4:e2b` for classify/vision, Gemini 3.5 flash-lite for chat and workers; no Sonnet anywhere** | cost | `gemma4:e4b` with thinking on was rejected (13-65 s per call, invalid JSON); `e2b` with thinking off passed the evals at ~3-4 s more per turn and falls back to Haiku on a timeout or bad JSON; fallback models are built lazily |
| **Speech-to-text in the daemon, text-to-speech in the UI** | STT is a metered model call that must respect the daily cap and also serves audio attachments; TTS is free and needs the sound device | |
| **Screen: structured first** (UI Automation → local OCR → vision on request), input by `SendInput`; privacy as a code filter; one approval = a short validated sequence; opt-in, bounded, unpersisted watching | pixels are the last resort; a prompt is not a privacy boundary | apps without UI Automation, elevated windows and games are out of reach; the blocklist is name-based |
| **Avatar click-through via the window mask**; one timer for state changes; the default skin is drawn in code | portable, no polling, no binary art in the repo | |
| **Damaged files are quarantined, never deleted** | a typo or a crash must not stop the daemon or destroy evidence | `*.corrupt-<time>` / `*.invalid-<time>` next to the original |
| **Commit hygiene**: work on the current branch, no AI attribution in commits | project rule | |
| **Independent review with `codex exec -s read-only`** instead of an MCP server | the Codex CLI has no MCP server mode | review is by reading (its sandbox cannot run Windows code); never pass secrets in briefs |

## Resource targets, measured
Idle on Windows 11, Python 3.12 (`scripts/profile_resources.py`):

| | Original target | Measured |
|---|---|---|
| Daemon idle RSS | ≤ 100 MB | 106 MB (183 MB after the first chat: provider SDKs load on demand) |
| Daemon start to `/health` | ≤ 3 s | 4.0 s (3.6 s for the packaged build when warm) |
| UI idle RSS | ≤ 60 MB | ~100 MB offscreen, ~170 MB on a real desktop |
| Combined | < 150 MB | ~210 MB offscreen, ~280 MB on a desktop |
| Idle CPU / polling loops | ~0 % / none | 0.0 % / none |

**Why the memory and start-up targets are missed:** Qt is 36 MB before any window exists, pydantic and the settings model 17 MB, the UI's own modules 20 MB, the windows only ~10 MB; a real desktop adds the platform plugin, fonts and tray. The daemon's ~100 MB and ~2.8 s of import time are LangGraph + LangChain + FastAPI + pydantic,
and each provider SDK adds 40-90 MB when first used. What was done: provider SDKs and fallback models load only when needed; `icalendar`, `sqlite_vec` and `imagehash` (numpy/scipy) are imported on first use; no polling.
**Targets held from now on:** daemon idle ≤ 110 MB, after the first chat ≤ 200 MB, start ≤ 5 s; UI ≤ 110 MB offscreen / ≤ 180 MB on a desktop. The biggest remaining levers: a UI that reads the few yaml keys without the settings model, and per-install provider SDKs.
