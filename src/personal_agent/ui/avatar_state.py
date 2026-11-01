"""Maps what the agent is doing to the avatar's animation state. Pure logic with an injectable clock (no timers, no Qt).

States: idle, listening, thinking, working, speaking, awaiting_approval, success, error, sleeping.
Priority (highest first): awaiting_approval > listening > speaking > error > success > working/thinking > sleeping > idle.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

STATES = ("idle", "listening", "thinking", "working", "speaking", "awaiting_approval", "success", "error", "sleeping")
FLASH_S = {"success": 2.5, "error": 4.0}  # how long a one-shot result stays on screen
WORKING_EVENTS = {"tool_call", "tool_result", "worker"}
THINKING_EVENTS = {"intent", "plan", "verdict"}
ERROR_CODES_QUIET = {"approval_pending"}  # a normal condition, not a failure animation


class AvatarState:
    def __init__(self, sleep_after_s: float = 300.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock, self.sleep_after_s = clock, sleep_after_s
        self.connected = True
        self.busy = False
        self.listening = False
        self.speaking = False
        self.pending_approvals = 0
        self._working = False
        self._flash: tuple[str, float] | None = None
        self._last_activity = clock()

    def touch(self) -> None:
        self._last_activity = self._clock()

    def user_sent(self) -> None:
        self.busy, self._working, self._flash = True, False, None
        self.touch()

    def on_event(self, ev: dict[str, Any]) -> str:
        t, now = ev.get("type"), self._clock()
        if t in WORKING_EVENTS:
            self.busy, self._working = True, True
        elif t in THINKING_EVENTS:
            self.busy = True
            self._working = False if t != "verdict" else self._working
        elif t == "approvals":
            self.pending_approvals = len(ev.get("items", []))
        elif t == "approval_request":
            self.pending_approvals += len(ev.get("approvals", []))
        elif t == "paused":
            self.busy = False
        elif t == "final":
            self.busy, self._working = False, False
            self._flash = ("error" if ev.get("stopped") else "success", now + FLASH_S["success"])
        elif t == "error" and ev.get("code") not in ERROR_CODES_QUIET:
            self.busy, self._working = False, False
            self._flash = ("error", now + FLASH_S["error"])
        elif t == "killed":
            self.busy, self._working, self.pending_approvals = False, False, 0
            self._flash = ("error", now + FLASH_S["error"])
        self.touch()
        return self.current()

    def current(self) -> str:
        now = self._clock()
        if self.pending_approvals > 0:
            return "awaiting_approval"
        if self.listening:
            return "listening"
        if self.speaking:
            return "speaking"
        if self._flash and now < self._flash[1]:
            return self._flash[0]
        if self.busy:
            return "working" if self._working else "thinking"
        if not self.connected:
            return "sleeping"
        return "sleeping" if now - self._last_activity >= self.sleep_after_s else "idle"

    def next_change_in(self) -> float | None:
        """Seconds until current() may change by itself (flash expiry / falling asleep); None = nothing scheduled.
        The UI uses one single-shot timer from this instead of polling."""
        now = self._clock()
        if self._flash and now < self._flash[1]:
            return self._flash[1] - now
        if not self.busy and self.connected and self.current() == "idle":
            return max(0.0, self.sleep_after_s - (now - self._last_activity))
        return None
