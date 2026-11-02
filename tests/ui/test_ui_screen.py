"""Screen features in the UI (offscreen): overlays, region selection, capture-to-chat, screen events, settings tab."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from conftest import pump
from personal_agent.perception import capture
from personal_agent.settings.settings import load_settings
from personal_agent.ui import screen as ui_screen
from personal_agent.ui.app import UiApp
from personal_agent.ui.avatar import AvatarWindow
from personal_agent.ui.client import DaemonClient
from personal_agent.ui.i18n import set_language
from personal_agent.ui.screen import HighlightOverlay, RegionSelector, WatchIndicator
from personal_agent.ui.settings_dialog import SettingsDialog
from personal_agent.ui.skin import Skin
from personal_agent.ui.theme import STATE


@pytest.fixture(autouse=True)
def one_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    set_language("en")
    monkeypatch.setattr(capture, "monitor_rects", lambda: [(0, 0, 1920, 1080)])


def test_highlight_outlines_the_target_then_goes_away(qapp: QApplication) -> None:
    h = HighlightOverlay()
    h.show_rect((100, 100, 300, 160), "click: Save", 100)
    assert h.isVisible() and h.width() >= 200 and bool(h.windowFlags() & Qt.WindowType.WindowTransparentForInput)  # never steals clicks
    pump(600)
    assert not h.isVisible()


def test_watch_indicator_is_visible_only_while_watching_and_click_stops_it(qapp: QApplication) -> None:
    w = WatchIndicator()
    w.set_state("off")
    assert not w.isVisible()
    w.set_state("on", 12.4)
    assert w.isVisible() and "12 min" in w.text and w.color == STATE["error"]
    w.set_state("paused")
    assert "paused" in w.text and w.color == STATE["working"]
    w.set_state("on", 3, private=True)
    assert "private window skipped" in w.text
    got: list[int] = []
    w.clicked.connect(lambda: got.append(1))
    QTest.mouseClick(w, Qt.MouseButton.LeftButton)
    assert got == [1]
    w.set_state("off")
    assert not w.isVisible()


def test_region_selector_returns_a_physical_rectangle_and_cancels_cleanly(qapp: QApplication) -> None:
    r = RegionSelector()
    got: list[Any] = []
    cancelled: list[int] = []
    r.selected.connect(got.append)
    r.cancelled.connect(lambda: cancelled.append(1))
    r.start()
    QTest.mousePress(r, Qt.MouseButton.LeftButton, pos=QPoint(10, 20))
    QTest.mouseMove(r, QPoint(110, 90))
    QTest.mouseRelease(r, Qt.MouseButton.LeftButton, pos=QPoint(110, 90))
    assert len(got) == 1 and got[0][2] > got[0][0] and got[0][3] > got[0][1] and not r.isVisible()
    r.start()  # a click without a drag is a cancel
    QTest.mousePress(r, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    QTest.mouseRelease(r, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
    r.start()
    QTest.keyClick(r, Qt.Key.Key_Escape)
    assert len(got) == 1 and cancelled == [1, 1]


def test_physical_and_logical_coordinates_round_trip_on_one_monitor(qapp: QApplication) -> None:
    scr = qapp.primaryScreen()
    dpr = scr.devicePixelRatio()
    phys = (int(40 * dpr), int(50 * dpr), int(240 * dpr), int(150 * dpr))
    lg = ui_screen.physical_to_logical(phys)
    assert abs(lg.x() - 40) <= 1 and abs(lg.width() - 200) <= 1
    back = ui_screen.logical_to_physical(lg, 0)
    assert all(abs(a - b) <= 2 for a, b in zip(back, phys, strict=True))


def test_the_avatar_shows_a_red_dot_while_watching_and_stays_clickable_there(qapp: QApplication) -> None:
    a = AvatarWindow(Skin.default())
    a.show()
    before = a._pixmap().toImage()
    a.set_watching(True)
    after = a._pixmap().toImage()
    x, y = a.width() - 13, 13
    assert after.pixelColor(x, y).red() > 200 and after.pixelColor(x, y).green() < 140 and before.pixelColor(x, y) != after.pixelColor(x, y)
    assert a.mask().boundingRect().contains(x, y) or not a.mask().isEmpty()  # the mask is rebuilt with the dot
    a.set_watching(False)
    assert a._pixmap().toImage() == before


def test_stroking_the_head_shows_headpat_and_walking_shows_while_it_wanders(qapp: QApplication) -> None:
    a = AvatarWindow(Skin.default())
    a.show()
    for x in (10, 40, 10, 40, 10):  # three direction changes inside the head zone
        a._stroke(QPoint(x, 5))
    assert a._shown == "headpat"
    a._end_pat()
    assert a._shown == "idle"
    for x in (10, 40, 10, 40, 10):  # below the head: nothing
        a._stroke(QPoint(x, a.height() - 5))
    assert a._shown == "idle"
    a.set_wander(True)
    a._wander_step()
    assert a._shown == "walking"
    a._walk.stop()
    a._end_walk()
    assert a._shown == "idle"


# ---- UiApp wiring ---------------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def ui(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> Any:
    client = DaemonClient("127.0.0.1", 1, "x")
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(client, "send", lambda p: sent.append(p) or True)
    app = UiApp(qapp, load_settings(), client, with_avatar=True, with_hotkeys=False)
    app.sent = sent  # type: ignore[attr-defined]
    app.avatar.show()
    yield app
    for w in (app.chat, app.approvals, app.manager, app.settings, app.quick, app.bubble, app.avatar, app.highlight, app.watch_badge):
        w.hide()
    app.tray.hide()


def test_screen_mode_events_drive_the_badge_the_avatar_dot_and_the_menu(ui: UiApp) -> None:
    ui.on_event({"type": "screen_mode", "state": "on", "minutes_left": 29.5, "private": False})
    assert ui.watch_badge.isVisible() and ui.avatar._watching and all("Stop watching" in a.text() for a in ui._watch_actions)
    assert "Watching the screen" in ui.bubble._label.text()
    ui.on_event({"type": "screen_mode", "state": "off", "reason": "timeout", "minutes_left": 0})
    assert not ui.watch_badge.isVisible() and not ui.avatar._watching and all("Watch the screen" in a.text() for a in ui._watch_actions)
    assert "switched itself off" in ui.bubble._label.text()


def test_toggle_and_badge_click_send_screen_mode_requests(ui: UiApp) -> None:
    ui.toggle_watch()
    assert ui.sent[-1] == {"type": "screen_mode", "action": "on"}
    ui.on_event({"type": "screen_mode", "state": "on", "minutes_left": 30})
    ui.toggle_watch()
    assert ui.sent[-1] == {"type": "screen_mode", "action": "off"}
    ui.watch_badge.clicked.emit()
    assert ui.sent[-1] == {"type": "screen_mode", "action": "off"}


def test_agent_activity_is_always_visible_to_the_user(ui: UiApp) -> None:
    ui.on_event({"type": "highlight", "rect": [100, 100, 220, 140], "label": "click: Save", "ms": 300})
    assert ui.highlight.isVisible()
    ui.on_event({"type": "screen_viewed", "kind": "describe", "title": "Notes"})
    assert "looked at your screen" in ui.bubble._label.text()
    ui.on_event({"type": "control_aborted", "reason": "you moved the mouse, so the sequence was stopped"})
    assert "⛔" in ui.bubble._label.text()
    ui.on_event({"type": "screen_change", "title": "x", "distance": 12, "changes": 3})
    assert "×3" in ui.tray.toolTip()


def test_connecting_registers_the_ui_pid_with_the_daemon(ui: UiApp) -> None:
    import os

    ui.on_connected()
    assert {"type": "about", "ui_pid": os.getpid()} in ui.sent and {"type": "screen_mode", "action": "status"} in ui.sent


def test_capture_attaches_the_masked_image_to_the_chat_without_sending_it(ui: UiApp, monkeypatch: pytest.MonkeyPatch) -> None:
    att = {"name": "screen-1.png", "mime": "image/png", "data": "AAAA"}
    monkeypatch.setattr("personal_agent.ui.app.capture_attachment", lambda kind, cfg, rect=None: (att, 2, "Notes - Notepad"))
    ui.capture("screen")
    assert ui.chat.isVisible() and ui.chat._files == [att] and "2 private window" in ui.bubble._label.text()
    assert not any(m.get("type") == "user" for m in ui.sent)  # the user decides whether to send it


def test_capturing_a_private_window_is_refused_with_a_clear_message(ui: UiApp, monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(kind: str, cfg: Any, rect: Any = None) -> Any:
        raise capture.Blocked("refused: its title matches the privacy blocklist")

    monkeypatch.setattr("personal_agent.ui.app.capture_attachment", blocked)
    ui.capture("active_window")
    assert "🔒" in ui.bubble._label.text() and not ui.chat._files


def test_a_region_selection_triggers_a_region_capture(ui: UiApp, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, Any]] = []
    monkeypatch.setattr("personal_agent.ui.app.capture_attachment", lambda kind, cfg, rect=None: (seen.append((kind, rect)) or {"name": "r.png", "mime": "image/png", "data": "A"}, 0, ""))
    ui.region.selected.emit((10, 20, 110, 90))
    assert seen == [("region", (10, 20, 110, 90))]


def test_closing_the_ui_switches_watching_off(ui: UiApp) -> None:
    ui.quit()
    assert {"type": "screen_mode", "action": "off"} in ui.sent


def test_settings_screen_tab_sends_only_what_changed(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_ui_windows import settings_event

    ev = settings_event()
    ev["screen"] = load_settings().screen.model_dump()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr("personal_agent.ui.system.autostart_enabled", lambda *a, **k: False)
    d = SettingsDialog(sent.append, lambda: None)
    d.load(ev)
    d.save()
    assert sent == []
    d.agent_control.setChecked(False)
    d.block_titles.setPlainText("my vault\n\n  other secret  ")
    d.watch_interval.setValue(4.5)
    d.save()
    patch = next(m for m in sent if m["type"] == "settings_set")["patch"]
    assert patch == {"screen": {"control": False, "block_titles": ["my vault", "other secret"], "watch_interval_s": 4.5}}
    assert d.tabs.tabText(d.tabs.count() - 1) == "About"  # About stays last (its live stats key off that)
    assert "Screen" in [d.tabs.tabText(i) for i in range(d.tabs.count())]
