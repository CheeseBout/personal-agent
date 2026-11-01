# ADR-0011: Resource targets, measured (Phase 9)

- Status: accepted. Supersedes the targets in `docs/phase0.md`.

## Measured (Windows 11, Python 3.12, `scripts/profile_resources.py`, idle, throw-away data dir)
| | Phase 0 target | Before Phase 9 | After Phase 9 |
|---|---|---|---|
| Daemon idle RSS | <= 100 MB | 109 MB | **106 MB** |
| Daemon after the first chat turn | - | 206 MB | **183 MB** (providers are imported on demand) |
| Daemon cold start to `/health` | <= 3 s | 4.0 s | **4.0 s** |
| UI idle RSS (offscreen / real desktop) | <= 60 MB | 100 / 170 MB | **100 / 170 MB** |
| Idle CPU (daemon, UI) | ~0 % | 0.3 %, 0 % | **0.0 %, 0.0 %** |
| Idle polling loops | none | none | none (scheduler sleeps to the next due job; watcher exists only while on) |

## What was done
- Fallback models are built only when the primary fails (an OpenAI SDK import alone is ~70 MB); keyless fallbacks are skipped up front.
- `icalendar`, `sqlite_vec` and `imagehash` (numpy/scipy) are imported when first used.
- The UI reconnects after 0.3 s, 0.6 s, 1 s... instead of 1 s, 2 s... (the UI usually starts the daemon and it is ready ~4 s later).

## Why the original targets are not met, and the new ones
- **UI:** Qt is 36 MB before any window exists; `pydantic` + the settings model 17 MB; the UI's own modules 20 MB; the windows themselves only ~10 MB (so lazy windows
  would save little). On a real desktop the Windows platform plugin, fonts and the tray add ~70 MB. A 60 MB PySide6 app is not realistic.
- **Daemon:** LangGraph + LangChain + FastAPI + pydantic account for most of the ~100 MB and ~2.8 s of start-up; each provider SDK adds 40-90 MB when first used. Removing them would mean
  dropping the framework the spec requires.
- Splitting the UI off pydantic or lazy-importing the graph before the first request would trade a small saving for a lot of complexity.

New targets (what we hold ourselves to, checked by `scripts/profile_resources.py`):
- Daemon idle <= 110 MB, after first chat <= 200 MB, cold start <= 5 s.
- UI idle <= 110 MB offscreen / <= 180 MB on the desktop.
- Idle CPU ~0 % and no polling loops (unchanged, met).
- Local models (Ollama, faster-whisper) are excluded, as before.

## Consequences
- Combined idle is about 210 MB offscreen (about 280 MB with a real desktop) against the original < 150 MB; this is a known, documented miss, not a regression.
- If memory matters more later, the biggest single lever is running the UI without the settings model (read the few yaml keys directly) and dropping unused provider SDKs per install.
