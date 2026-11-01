"""Continuous screen mode: low-FPS capture + perceptual-hash change detection, in memory only.

- Off by default; only the user turns it on (UI button / hotkey). It switches itself off after `watch_max_minutes`.
- Nothing is stored on disk. The single newest "meaningful change" frame is kept in RAM so the agent can look at it when it asks
  (`screen_describe(target="last_change")`); stopping clears it.
- If the foreground window is on the privacy blocklist the frame is dropped before it is hashed or stored.
- The UI gets small events (state, change count, window title) and shows a visible indicator while this runs.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

from personal_agent.perception import capture, windows
from personal_agent.settings.settings import ScreenCfg

HASH_SIZE = 8  # 8x8 -> 64-bit hash, distance 0..64


class ScreenWatcher:
    def __init__(self, cfg: Callable[[], ScreenCfg], emit: Callable[[dict[str, Any]], None],
                 grab: Callable[[ScreenCfg], capture.Shot] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._cfg, self._emit, self._clock = cfg, emit, clock
        self._grab = grab or (lambda c: capture.capture("screen", c))
        self.state = "off"  # off | on | paused
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_hash: Any = None
        self._frame: capture.Shot | None = None
        self._started = 0.0
        self.changes = 0
        self.private = False

    # ---- control --------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self.state != "off":
                return
            self.state, self._started, self.changes, self.private = "on", self._clock(), 0, False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="screen-watch", daemon=True)
            self._thread.start()
        self._announce()

    def stop(self, reason: str = "user") -> None:
        with self._lock:
            if self.state == "off":
                return
            self.state, self._last_hash, self._frame = "off", None, None  # nothing is kept
            self._stop.set()
        self._announce(reason)

    def pause(self) -> None:
        with self._lock:
            if self.state != "on":
                return
            self.state = "paused"
        self._announce()

    def resume(self) -> None:
        with self._lock:
            if self.state != "paused":
                return
            self.state = "on"
        self._announce()

    def status(self) -> dict[str, Any]:
        c = self._cfg()
        left = max(0.0, c.watch_max_minutes * 60 - (self._clock() - self._started)) if self.state != "off" else 0.0
        return {"type": "screen_mode", "state": self.state, "interval_s": c.watch_interval_s, "threshold": c.watch_change_threshold,
                "changes": self.changes, "private": self.private, "minutes_left": round(left / 60, 1)}

    def _announce(self, reason: str = "") -> None:
        ev = self.status()
        if reason:
            ev["reason"] = reason
        self._emit(ev)

    # ---- sampling -----------------------------------------------------------------------------------------------------------
    def latest(self) -> capture.Shot | None:
        with self._lock:
            return self._frame if self.state != "off" else None

    def tick(self) -> bool:
        """One sample. Returns True if it was a meaningful change. (Public so tests can drive it without threads.)"""
        c = self._cfg()
        if self._clock() - self._started > c.watch_max_minutes * 60:
            self.stop("timeout")
            return False
        fg = windows.foreground_window()
        if fg is not None and windows.view_block_reason(fg, c):
            if not self.private:
                self.private = True
                self._announce()
            return False  # dropped before hashing: a private window leaves no trace at all
        if self.private:
            self.private = False
            self._announce()
        try:
            shot = self._grab(c)
        except capture.CaptureError:
            return False
        import imagehash  # lazy: pulls in numpy + scipy, which the idle daemon should not pay for

        h = imagehash.phash(shot.image.resize((128, 128)), hash_size=HASH_SIZE)
        with self._lock:
            if self.state != "on":
                return False
            last, self._last_hash = self._last_hash, h
            if last is None or (h - last) < c.watch_change_threshold:
                return False
            self._frame, self.changes = shot, self.changes + 1
            n = self.changes
        self._emit({"type": "screen_change", "title": shot.title[:80], "distance": int(h - last), "changes": n})
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.state == "on":
                with contextlib.suppress(Exception):  # a capture hiccup must not kill the watcher thread
                    self.tick()
            self._stop.wait(max(0.5, self._cfg().watch_interval_s))
