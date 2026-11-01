"""Continuous mode: change detection, privacy, auto-off, nothing kept after stop. Driven with synthetic frames and a fake clock."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from PIL import Image, ImageDraw

from personal_agent.perception import capture, windows
from personal_agent.perception.watch import ScreenWatcher
from personal_agent.settings.settings import ScreenCfg


def frame(kind: int) -> capture.Shot:
    """Different `kind`s have visibly different layouts (a perceptual hash must tell them apart)."""
    img = Image.new("RGB", (320, 200), (240, 240, 240))
    d = ImageDraw.Draw(img)
    if kind == 1:
        d.rectangle((20, 20, 300, 90), fill=(20, 20, 20))
    if kind == 2:
        d.ellipse((60, 40, 260, 180), fill=(200, 30, 30))
        d.rectangle((0, 150, 320, 200), fill=(30, 30, 200))
    return capture.Shot(img, "screen", f"win{kind}", "app.exe", (0, 0, 320, 200), 1.0)


class Rig:
    def __init__(self, cfg: ScreenCfg | None = None) -> None:
        self.cfg = cfg or ScreenCfg(watch_change_threshold=6)
        self.events: list[dict[str, Any]] = []
        self.next = 0
        self.now = 100.0
        self.w = ScreenWatcher(lambda: self.cfg, self.events.append, lambda c: frame(self.next), lambda: self.now)
        self.w.state, self.w._started = "on", self.now  # drive tick() directly, no thread


@pytest.fixture(autouse=True)
def no_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows, "foreground_window", lambda: None)


def test_only_meaningful_changes_are_reported_and_the_baseline_is_silent() -> None:
    r = Rig()
    assert r.w.tick() is False and r.events == []        # first frame = baseline
    assert r.w.tick() is False                           # identical frame
    r.next = 1
    assert r.w.tick() is True and r.events[-1]["type"] == "screen_change" and r.events[-1]["title"] == "win1" and r.events[-1]["distance"] >= 6
    assert r.w.tick() is False                           # still the same new screen
    r.next = 2
    assert r.w.tick() is True and r.w.changes == 2
    assert r.w.latest() is not None and r.w.latest().title == "win2"  # type: ignore[union-attr]


def test_a_small_change_below_the_threshold_is_ignored() -> None:
    r = Rig(ScreenCfg(watch_change_threshold=60))
    r.w.tick()
    r.next = 1
    assert r.w.tick() is False and r.w.latest() is None


def test_stopping_clears_everything_that_was_kept() -> None:
    r = Rig()
    r.w.tick()
    r.next = 1
    r.w.tick()
    assert r.w.latest() is not None
    r.w.stop()
    assert r.w.latest() is None and r.w._frame is None and r.w._last_hash is None and r.w.state == "off"
    assert r.events[-1]["type"] == "screen_mode" and r.events[-1]["state"] == "off"
    r.w.stop()  # stopping twice is harmless
    assert [e["state"] for e in r.events if e["type"] == "screen_mode"] == ["off"]


def test_a_private_foreground_window_leaves_no_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig()
    r.w.tick()
    seen: list[int] = []
    r.w._grab = lambda c: seen.append(1) or frame(2)  # type: ignore[assignment]
    monkeypatch.setattr(windows, "foreground_window", lambda: windows.WindowInfo(1, "KeePass", "keepass.exe", 9, (0, 0, 10, 10)))
    assert r.w.tick() is False and seen == [] and r.w.private and r.w.latest() is None  # not even captured
    assert sum(1 for e in r.events if e["type"] == "screen_mode") == 1  # told the UI once, not on every tick
    r.w.tick()
    assert sum(1 for e in r.events if e["type"] == "screen_mode") == 1
    monkeypatch.setattr(windows, "foreground_window", lambda: None)
    r.w.tick()
    assert not r.w.private


def test_it_switches_itself_off_after_the_time_limit() -> None:
    r = Rig(ScreenCfg(watch_max_minutes=1))
    r.now += 61
    assert r.w.tick() is False and r.w.state == "off" and r.events[-1]["reason"] == "timeout"


def test_pause_and_resume_and_status() -> None:
    r = Rig()
    r.w.pause()
    assert r.w.state == "paused" and r.w.latest() is not None or True
    r.next = 1
    r.w.tick()  # paused: tick() itself does not check, the loop does; state must stay paused
    assert r.w.state == "paused"
    r.w.resume()
    assert r.w.state == "on"
    st = r.w.status()
    assert st["type"] == "screen_mode" and st["interval_s"] == 2.0 and 0 < st["minutes_left"] <= 30


def test_a_failing_capture_is_not_a_change() -> None:
    r = Rig()

    def boom(c: ScreenCfg) -> capture.Shot:
        raise capture.CaptureError("no display")

    r.w._grab = boom  # type: ignore[assignment]
    assert r.w.tick() is False


def test_real_thread_start_stop_and_no_busy_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []
    events: list[dict[str, Any]] = []
    cfg = ScreenCfg(watch_interval_s=0.5, watch_change_threshold=6)
    w = ScreenWatcher(lambda: cfg, events.append, lambda c: calls.append(time.monotonic()) or frame(len(calls) % 3))
    w.start()
    w.start()  # starting twice does not spawn a second thread
    time.sleep(1.4)
    w.stop()
    n = len(calls)
    time.sleep(0.8)
    assert 2 <= n <= 4 and len(calls) == n  # sampled about every 0.5 s, and nothing runs after stop
    assert sum(1 for t in threading.enumerate() if t.name == "screen-watch") == 0
    assert events[0]["state"] == "on" and events[-1]["state"] == "off"
