# ADR-0010: Screen perception and control (Phase 8)

- Status: accepted

## Decision
- **Perception runs in the daemon, control UI is in the UI process.** Tools (`screen_*`, `ui_tree`, `ui_act`) and continuous watching live in the daemon next to the policy gate; the UI shows
  the badge, the highlight and the capture hotkeys. They talk over the existing WebSocket (`highlight`, `screen_mode`, `screen_viewed`, `control_aborted`).
- **Structured first, pixels second:** UI Automation (pywinauto) for element trees and the Invoke pattern; Windows' built-in OCR (PowerShell, no new dependency) for local text; a vision
  model only on request. Input is `SendInput` through ctypes (no pyautogui dependency).
- **Privacy is a filter, not a prompt:** the blocklist is applied to every capture/read/control path in code and blacks windows out of images; the model never sees the original.
- **One approval = a short, validated sequence** (<= 8 steps), shown as a preview with a kill-switch / mouse-move abort. Control is T2, never "remember", attended-only.
- **Safety rules the agent cannot configure away:** no control of shells/system tools or of its own UI (process ids registered by every UI), no Win-key combos, password fields refused,
  target must be uncovered and in front, a connected UI is required to run (so the outline and abort are visible).
- **Continuous mode is opt-in, bounded and unpersisted:** only the user starts it, it ends by itself (timer, kill switch, last UI gone), and keeps one in-memory frame.

## Consequences
- Apps that do not expose UIA, elevated windows and some games cannot be operated; vision plus coordinates is the (approved) fallback.
- The title/program blocklist is best-effort and user-editable; it cannot know apps it was not told about.
- Perception adds about 10 MB to the idle daemon (Pillow, mss).
