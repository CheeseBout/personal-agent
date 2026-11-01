"""Screen features through the real daemon app: continuous-mode switch, events pushed to the UI, own-pid protection, kill switch, settings."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from fakes import FakeBrain, ScriptedModel, answer
from personal_agent.core.app import create_app
from personal_agent.perception import capture, hooks, windows

AUTH = {"Authorization": "Bearer test-token"}


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.client = TestClient(create_app(FakeBrain(ScriptedModel(responses=[answer("ok")])), tmp / "c.db", "test-token"))

    def __enter__(self) -> Rig:
        self.client.__enter__()
        self.ws = self.client.websocket_connect("/ws", headers=AUTH).__enter__()
        assert self.ws.receive_json()["type"] == "approvals"
        return self

    def __exit__(self, *a: object) -> None:
        self.ws.__exit__(*a)
        self.client.__exit__(*a)

    def until(self, kind: str) -> dict[str, Any]:
        while True:
            ev: dict[str, Any] = self.ws.receive_json()
            if ev["type"] == kind:
                return ev


@pytest.fixture(autouse=True)
def fake_screen(monkeypatch: pytest.MonkeyPatch) -> Any:
    shot = capture.Shot(Image.new("RGB", (64, 64), (200, 200, 200)), "screen", "Notes", "notepad.exe", (0, 0, 64, 64), 1.0)
    monkeypatch.setattr(capture, "capture", lambda *a, **k: shot)
    monkeypatch.setattr(windows, "foreground_window", lambda: None)
    yield
    windows._own_pids.clear()
    windows._own_pids.add(__import__("os").getpid())


def test_screen_mode_can_be_switched_and_every_ui_hears_about_it(tmp_path: Path) -> None:
    with Rig(tmp_path) as r:
        r.ws.send_json({"type": "screen_mode", "action": "on"})
        st = r.until("screen_mode")
        assert st["state"] == "on" and st["interval_s"] == 2.0 and st["minutes_left"] > 29
        r.ws.send_json({"type": "screen_mode", "action": "pause"})
        assert r.until("screen_mode")["state"] == "paused"
        r.ws.send_json({"type": "screen_mode", "action": "resume"})
        assert r.until("screen_mode")["state"] == "on"
        r.ws.send_json({"type": "screen_mode", "action": "off"})
        assert r.until("screen_mode")["state"] == "off"
        r.ws.send_json({"type": "screen_mode", "action": "status"})
        assert r.until("screen_mode")["state"] == "off"
        r.ws.send_json({"type": "screen_mode", "action": "explode"})
        assert r.until("error")["code"] == "bad_request"


def test_the_kill_switch_ends_continuous_watching_and_blocks_restarting_it(tmp_path: Path) -> None:
    with Rig(tmp_path) as r:
        r.ws.send_json({"type": "screen_mode", "action": "on"})
        assert r.until("screen_mode")["state"] == "on"
        r.ws.send_json({"type": "kill"})
        got = {}
        while {"screen_mode", "killed"} - set(got):  # both arrive, in either order
            ev = r.ws.receive_json()
            got[ev["type"]] = ev
        assert got["screen_mode"]["state"] == "off" and got["screen_mode"]["reason"] == "kill switch"
        r.ws.send_json({"type": "screen_mode", "action": "on"})
        assert "kill switch" in r.until("error")["message"]
        r.ws.send_json({"type": "release_kill"})
        r.until("kill_released")
        r.ws.send_json({"type": "screen_mode", "action": "on"})
        assert r.until("screen_mode")["state"] == "on"
        r.ws.send_json({"type": "screen_mode", "action": "off"})
        r.until("screen_mode")


def test_a_ui_registers_its_pid_so_the_agent_can_never_operate_it(tmp_path: Path) -> None:
    with Rig(tmp_path) as r:
        r.ws.send_json({"type": "about", "ui_pid": 31337})
        assert r.until("about")["pid"]
        assert 31337 in windows.own_pids()
        w = windows.WindowInfo(5, "Whatever", "python.exe", 31337, (0, 0, 9, 9))
        assert "own window" in (windows.control_deny_reason(w, __import__("personal_agent.settings.settings", fromlist=["x"]).ScreenCfg()) or "")


def test_tool_side_events_reach_the_ui_from_worker_threads(tmp_path: Path) -> None:
    with Rig(tmp_path) as r:
        assert hooks.observers() == 1  # one connected UI: control would be allowed to run
        t = threading.Thread(target=lambda: hooks.emit({"type": "highlight", "rect": [1, 2, 3, 4], "label": "click: Save", "ms": 700}))
        t.start()
        t.join()
        ev = r.until("highlight")
        assert ev["rect"] == [1, 2, 3, 4] and ev["ms"] == 700
        hooks.emit({"type": "screen_viewed", "kind": "describe", "title": "Notes"})
        assert r.until("screen_viewed")["title"] == "Notes"
    assert hooks.observers() == 0  # daemon shutdown resets the hooks


def test_screen_settings_round_trip_and_bad_values_are_rejected(tmp_path: Path) -> None:
    with Rig(tmp_path) as r:
        r.ws.send_json({"type": "settings_get"})
        s = r.until("settings")
        assert s["screen"]["control"] is True and "keepass" in s["screen"]["block_titles"]
        r.ws.send_json({"type": "settings_set", "patch": {"screen": {"watch_interval_s": 5, "block_titles": ["my vault"], "control": False}}})
        s = r.until("settings")
        assert s["screen"]["watch_interval_s"] == 5 and s["screen"]["block_titles"] == ["my vault"] and s["screen"]["control"] is False
        r.ws.send_json({"type": "screen_mode", "action": "status"})
        assert r.until("screen_mode")["interval_s"] == 5  # the running watcher sees the new settings
        for bad in ({"watch_interval_s": 0.01}, {"max_actions": 500}, {"watch_change_threshold": 0}, {"nope": 1}):
            r.ws.send_json({"type": "settings_set", "patch": {"screen": bad}})
            assert r.until("error")["code"] == "bad_request"


def test_watching_stops_by_itself_when_the_last_ui_disconnects(tmp_path: Path) -> None:
    with TestClient(create_app(FakeBrain(ScriptedModel(responses=[answer("ok")])), tmp_path / "c.db", "test-token")) as client:
        with client.websocket_connect("/ws", headers=AUTH) as ws:
            ws.receive_json()
            ws.send_json({"type": "screen_mode", "action": "on"})
            while ws.receive_json()["type"] != "screen_mode":
                pass
        # the UI closed (or crashed): the daemon must not keep watching the screen with no badge anywhere
        with client.websocket_connect("/ws", headers=AUTH) as ws2:
            ws2.receive_json()
            ws2.send_json({"type": "screen_mode", "action": "status"})
            ev = ws2.receive_json()
            while ev["type"] != "screen_mode":
                ev = ws2.receive_json()
            assert ev["state"] == "off"
