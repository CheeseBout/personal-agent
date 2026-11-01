# Phase 8 - Screen perception and control

Install: `python -m uv sync --extra screen --extra ui --extra voice` (Pillow, mss, imagehash, pywinauto). Config: `screen:` in
`config/default.yaml` or Settings -> Screen. Checks on a real desktop: `scripts/screen_check.py` and `scripts/agent_screen_e2e.py`.

## What the agent can do (tools)
| Tool | Tier | What it does |
|---|---|---|
| `screen_windows` | T0, untrusted | Visible windows (title, program, rectangle). Private windows show as "(private window)" |
| `screen_describe(question, target)` | T0, untrusted | Looks at the screen / active window / `window:<title>` / `last_change` with the `vision` model (image goes to the cloud, only now) |
| `screen_ocr(target)` | T0, untrusted | Local Windows OCR (offline, free) with line rectangles in screen pixels, usable as click targets |
| `ui_tree(window)` | T0, untrusted | UI Automation element tree: role, name, id, exact rectangle, enabled. Prefer this over pixels |
| `ui_act(actions)` | **T2, never remembered, attended only** | Up to 8 steps (click / double_click / right_click / type / key / scroll / wait / focus) approved together |

Every view tells the UI ("the agent looked at your screen", shown as a bubble), is marked `[UNTRUSTED SCREEN CONTENT]`, and taints the task
(so anything with side effects that follows needs approval, as for web pages). The vision model is told that on-screen text is data.

## For you (UI)
- **Capture** (`Ctrl+Alt+S` whole screen; tray/avatar menu: active window after 2 s, or drag a region) -> the image is attached to the chat, you decide whether to send it.
- **Continuous watching** (`Ctrl+Alt+W`, tray, avatar menu): low-FPS capture + perceptual-hash change detection in the daemon. A **red badge** at the top of the
  screen and a **red dot on the avatar** show while it runs; click the badge to stop. It switches itself off after `watch_max_minutes` (30), when the kill
  switch engages, and when the last UI disconnects. Nothing is written to disk; only the newest changed frame is kept in memory so the agent can look at
  it when it asks (`target="last_change"`). Frames are dropped before hashing while a private window is in front.
- **Highlight**: before each agent click/type the target is outlined for `highlight_ms` (700 ms) with a label.
- Settings -> Screen: let the agent look / use mouse+keyboard, private window titles and programs, watch interval/sensitivity/limit, steps per approval.

## Privacy rules
- **View blocklist** (`block_titles`, `block_processes`; defaults: password managers, banking and sign-in pages, wallets, credential dialogs, in English and
  Vietnamese): such windows are never captured, read, listed by name or controlled. A full-screen/region capture **blacks them out** instead of failing; capturing
  one directly is refused.
- Typing into **password fields** is refused (UIA `IsPassword`); their values are never read. The element tree never shows values of password fields.
- Images are only sent to a model when the agent calls `screen_describe`, are never saved, and are downscaled (`max_image_px` 1280).

## Why the agent cannot misuse control (all enforced in code and tested)
- Control is refused for **shells and system tools** (cmd, PowerShell, Windows Terminal, regedit, Task Manager, Settings, MMC...): typing there would bypass the shell policy.
- Control is refused for **the agent's own windows**: every UI registers its process id with the daemon, so the agent can never click "Approve" on its own approval card.
- The Win key and Ctrl+Alt+Del are refused. Text is limited to 2000 characters; at most `max_actions` (8) steps per approval; `ui_act` is not allowed in unattended (scheduled) runs.
- Every step re-checks its target window (also for clicks by coordinates), refuses when **another window covers the target** (a mouse click lands on whatever is on top),
  and before any keystroke **verifies that the target really is the front window** (Windows may refuse a focus change: then nothing is typed rather than typing elsewhere).
- Before each action the target is outlined; the run needs a connected UI; **moving the mouse** more than `abort_move_px` (25) or the **kill switch** stops it immediately.
- Control prefers UIA (Invoke pattern, no mouse) and only uses the mouse as a fallback.

## Verified
- 79 new tests (51 logic, 8 watcher, 6 daemon API, 14 UI): privacy and safety rules, key parsing, step validation, the control engine on a fake backend (invoke, fallback, password, shells, own windows,
  covered target, front-window check, mouse abort, kill switch, limits), masking, change detection on synthetic frames (threshold, baseline, private window, auto-off,
  nothing kept after stop, no busy loop), the daemon API (mode switch, kill switch, pid registration, settings validation, UI-disconnect stop), overlays, region selection,
  UI wiring, settings tab.
- `scripts/screen_check.py` on a real Windows 11 desktop (3 consecutive runs, 21/21): real capture with a private window blacked out, OCR with coordinates, UIA tree with
  password detection, click through UIA, typing Vietnamese text, refusals (password, private window, covered target), abort on mouse movement, and a real vision call.
- `scripts/agent_screen_e2e.py` (12/12): hotkeys capture and watch, a new window noticed as a change, then the real agent (real models) used `ui_tree` then `ui_act` to click
  the Save button: approval card first, nothing clicked before approval, outline shown, click delivered, answer reported.

## Found by running it for real (and fixed)
OCR output with raw control characters crashed the JSON parser; `Save` matched a label before the button (exact name and operable controls now rank first); a failed focus change
would have typed into whatever was in front (now verified); a click could land on a covering window (now refused); Non-ASCII letters were accepted as hotkeys (`ñ`).

## Limits
- **Vietnamese OCR needs the Windows OCR language pack**; without it Vietnamese text comes out garbled (the tool reports the language; `screen_describe` reads it fine).
- A window capture shows what is on screen in that rectangle: if another (non-private) window covers it, you capture that. The tools do not move windows by themselves.
- Only the primary monitor is captured as "screen"; region selection maps Qt screens to monitors by order. **Several monitors / mixed DPI were not tested.**
- UI Automation quality depends on the app: browsers and Electron expose partial trees, games none. Elevated (admin) windows cannot be controlled from a normal process.
- The blocklist is title/program-name matching: it cannot know your private apps unless you add them; for browsers only the active tab's title is visible to it.
- Vision calls take about 10-13 s and cost about $0.001 each with the default model. First OCR/UIA calls cost 1-2 s.
- Idle cost: the daemon grew from about 99 MB to about 109 MB (Pillow, mss); the watcher thread exists only while watching.
- This reduces, not eliminates, prompt-injection risk from screen text: the real barrier is that every action still needs your approval, with the untrusted-content warning shown on the card.
