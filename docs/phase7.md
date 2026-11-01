# Phase 7 - Desktop UI

Run: `python -m uv sync --extra ui --extra voice` then `python -m uv run agent-ui` (starts the daemon if it is not running; `--no-daemon`,
`--no-avatar`, `--thread ID` are available). The UI is a separate process that talks to the daemon only over the local WebSocket:
closing it never stops a task, and on reconnect the daemon re-sends pending approvals.

## What is in it
| Piece | Where | Notes |
|---|---|---|
| Daemon link | `ui/client.py` | QtWebSockets (no asyncio loop in the UI), token header, auto-reconnect with back-off 1/2/4/8 s |
| Conversation model | `ui/model.py` | Pure Python view of the event stream (transcript, plan, steps, approvals, usage). Windows only render it |
| Tray + app wiring | `ui/app.py` | Tray menu (chat, approvals, manage pages, settings, stop speaking, **kill switch**, quit UI), single-instance lock, daemon auto-start (detached) |
| Chat | `ui/chat.py` | Markdown transcript, Plan/Progress panel (goal, criteria, steps with status, daily budget meters), attachments (button, drag-drop, pasted images), microphone button |
| Approval center | `ui/approvals.py`, `ui/decisions.py` | One card per pending call: tool, tier/risk, reason, untrusted-content warning, expiry, editable args, colour diff preview, approve / approve-and-remember / reject (with reason), `CONFIRM` for T3. Decisions are sent per thread |
| Manager | `ui/pages.py` | Memory, Documents (RAG), Tools/MCP, Schedules, Remembered approvals, Audit log, Usage (Phase 6 report), Policy (YAML editor) |
| Settings + first-run wizard | `ui/settings_dialog.py` | General (name, languages, autostart, avatar, folders), Models (per role, with a Test button), API keys (write-only), Voice, Budgets, About (live CPU/RAM of UI and daemon, polled only while that tab is visible). Wizard: language, provider key + connection test, workspace folder |
| Avatar + skins | `ui/avatar.py`, `ui/skin.py`, `ui/avatar_state.py`, `docs/skins.md` | Transparent frameless always-on-top window, per-pixel click-through (window mask), drag, edge snapping, 0.5-3x scale, multi-monitor aware, optional idle wandering, speech bubble, drop files on it |
| Voice | `ui/voice_io.py`, `voice/stt.py` | Recording (16 kHz mono WAV, stops on 1.5 s of silence or at the cap), speech-to-text in the daemon, spoken answers with edge-tts (sentence by sentence, interruptible) |
| Windows integration | `ui/system.py` | Autostart (HKCU Run), global hotkeys via RegisterHotKey (kill, talk, chat), process stats |

## Daemon additions (all behind the same token)
`settings_get` / `settings_set` (whitelisted: models provider+model, agent language, daily caps, voice, ui, observability; anything else is
rejected), `secret_set` (write-only; values are never returned or logged, only names in a whitelist are accepted), `test_role`,
`policy_get` / `policy_set` (validated with the real engine, `.bak` kept) / `policy_add_path`, `transcribe`, `about`, and `attachments` on
user messages (images become multimodal blocks; text/md/csv/pdf/docx are extracted; audio is transcribed; max 4 files, 5 MB each).
Also fixed: a client dropping mid-send no longer produces a server stack trace.

## Defaults I chose
- STT = Gemini audio (`gemini-3.5-flash-lite`, about $0.00002 per short clip); `voice-local` (faster-whisper) is an optional extra. TTS = edge-tts (free, needs internet), off until enabled in Settings.
- Hotkeys: `Ctrl+Alt+K` kill switch, `Ctrl+Alt+Space` talk (press to start, press again or stop talking to send), `Ctrl+Alt+A` open chat. Editable in `ui.hotkeys`; a key taken by another program is reported, not fatal.
- The default avatar is drawn in code (9 states), so no art is required; see `docs/skins.md` to supply your own.
- Interface language follows the OS (`ui.language: auto`), switchable at runtime. Every visible string has a Vietnamese translation (a test enforces it).

## Verified
80 new tests (24 API, 20 logic, 36 UI): model, avatar state machine, decisions, hotkey parsing, voice helpers, autostart (throw-away registry key), settings/policy/attachment API,
skins (frames, fallbacks, path-traversal refusal), avatar (click vs double-click vs drag, drop, timer only while visible), chat, approval center,
manager pages, settings dialog, wizard, spoken answers (edge-tts mocked), recorder (fake device), and the **whole UI against a real daemon**
(uvicorn thread): connect, chat, rename, attachments, settings round-trip, approvals, reconnect after a dropped socket, wrong token.
Real calls checked by hand: edge-tts Vietnamese and English synthesis, then Gemini transcription of both (exact text back, about 3 s each).
Screenshots of the avatar states, chat, approval center and usage page were rendered offscreen and inspected.

## Not verified / known limits
- **Checked on a real Windows 11 desktop (1 screen, 125% scaling) on 2026-11-01** with `scripts/desktop_check.py` (21 checks, all passing): tray icon visible; avatar on screen, transparent corners show the desktop (no box), clicks pass through transparent pixels and hit the body (`WindowFromPoint`), topmost tool window without a taskbar button; a real question through the real daemon and models (avatar thinking -> success, answer shown in chat); approval opens the center and shows the sticky bubble; the three global hotkeys work when pressed from outside the app (chat, talk start/stop, kill switch and release); the microphone captured audio.
- **Still not verified:** several monitors with different DPI, tray balloon notifications (only that the call succeeds), and speech recognition quality from a real spoken sentence through the microphone (the test recording was discarded, not uploaded; the silence threshold `SPEECH_RMS` may need tuning).
- **Idle footprint misses the target.** Measured on 2026-11-01 at idle: daemon about 99 MB; UI about 99 MB offscreen but **170 MB on the real Windows platform**, so roughly 270 MB together vs the 150 MB goal. The UI is 36 MB of Qt plus 17 MB of pydantic/settings before any window exists; the daemon pays for LangGraph/LangChain. Profiling and trimming (lazy windows, lazier imports) belong to Phase 9.
- First answer after starting the daemon took 10-16 s on the desktop (later ones are a few seconds); the likely cause is the Ollama `bge-m3` model loading for the memory lookup. Not investigated further.
- Lip-sync is the "speaking" animation, not audio-driven. The avatar menu has no "screen mode" entry yet (Phase 8).
- The Policy page is a raw YAML editor with validation, not a form. Policy edits are powerful by design: they change what the agent may do.
- Attachments larger than 5 MB are refused; images are sent inline (they stay in the conversation checkpoint).
- The wizard adds a folder only when `paths.allow` is a flow list (`[a, b]`) or a block list; any other YAML shape asks you to edit by hand.
