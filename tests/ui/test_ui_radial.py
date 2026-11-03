"""The radial menu around the avatar: slot geometry, choosing, sub-rings, the two-click danger slot, screen clamping, UiApp wiring."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from conftest import pump
from personal_agent.settings.settings import load_settings
from personal_agent.ui.app import UiApp
from personal_agent.ui.client import DaemonClient
from personal_agent.ui.i18n import set_language
from personal_agent.ui.radial import RadialItem, RadialMenu, slot_at


def test_slot_at_is_clockwise_from_the_top_and_ignores_the_hole_and_outside() -> None:
    assert slot_at(0, -100, 60, 130, 8) == 0  # straight up
    assert slot_at(100, 0, 60, 130, 8) == 2  # right
    assert slot_at(0, 100, 60, 130, 8) == 4  # down
    assert slot_at(-100, 0, 60, 130, 8) == 6  # left
    assert slot_at(0, -100, 60, 130, 4) == 0 and slot_at(100, 0, 60, 130, 4) == 1
    assert slot_at(0, -10, 60, 130, 8) is None and slot_at(0, -200, 60, 130, 8) is None  # in the hole / outside the ring
    assert slot_at(-1, -100, 60, 130, 8) == 0 and slot_at(1, -100, 60, 130, 8) == 0  # either side of the top line stays in slot 0
    assert slot_at(0, -100, 60, 130, 0) is None


@pytest.fixture
def menu(qapp: QApplication) -> Any:
    set_language("en")
    m = RadialMenu()
    yield m
    m.close()


def click(m: RadialMenu, i: int) -> None:
    QTest.mouseClick(m, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, m.slot_center(i).toPoint())


def target() -> QWidget:
    t = QWidget()
    t.resize(120, 120)
    t.move(600, 400)
    return t


def test_clicking_a_slot_runs_its_action_after_the_ring_closes(menu: RadialMenu) -> None:
    ran: list[str] = []
    menu.open_around(target(), [RadialItem("A", "A", "a", lambda: ran.append("a")), RadialItem("B", "B", "b", lambda: ran.append("b"))])
    assert menu.isVisible()
    click(menu, 1)
    assert not menu.isVisible()
    pump(50)
    assert ran == ["b"]


def test_a_click_in_the_hole_or_the_right_button_or_escape_closes_without_running(menu: RadialMenu) -> None:
    ran: list[int] = []
    items = [RadialItem("A", "A", "a", lambda: ran.append(1)), RadialItem("B", "B", "b", lambda: ran.append(2))]
    menu.open_around(target(), items)
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, menu.rect().center())
    assert not menu.isVisible()
    menu.open_around(target(), items)
    QTest.mouseClick(menu, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, menu.slot_center(0).toPoint())
    assert not menu.isVisible()
    menu.open_around(target(), items)
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()
    pump(50)
    assert ran == []


def test_a_parent_slot_opens_a_sub_ring_with_back_and_escape_goes_back(menu: RadialMenu) -> None:
    ran: list[str] = []
    kids = [RadialItem("X", "X", "x", lambda: ran.append("x")), RadialItem("Y", "Y", "y", lambda: ran.append("y"))]
    menu.open_around(target(), [RadialItem("P", "P", "p", children=kids), RadialItem("Q", "Q", "q")])
    click(menu, 0)
    assert menu.isVisible() and [i.en for i in menu.items] == ["X", "Y", "Back"]
    click(menu, 2)  # back
    assert [i.en for i in menu.items] == ["P", "Q"]
    click(menu, 0)
    QTest.keyClick(menu, Qt.Key.Key_Escape)  # escape in a sub-ring goes back instead of closing
    assert menu.isVisible() and [i.en for i in menu.items] == ["P", "Q"]
    click(menu, 0)
    QTest.keyClick(menu, Qt.Key.Key_2)  # number keys pick a slot
    pump(50)
    assert ran == ["y"] and not menu.isVisible()


def test_a_dangerous_slot_needs_a_second_click(menu: RadialMenu) -> None:
    ran: list[int] = []
    menu.open_around(target(), [RadialItem("Safe", "An toàn", "s", lambda: ran.append(0)), RadialItem("Stop", "Dừng", "!", lambda: ran.append(1), danger=True)])
    click(menu, 1)
    pump(50)
    assert ran == [] and menu.isVisible()  # armed, nothing ran
    click(menu, 0)  # choosing something else disarms it
    pump(50)
    assert ran == [0]
    menu.open_around(target(), [RadialItem("Stop", "Dừng", "!", lambda: ran.append(1), danger=True), RadialItem("Safe", "An toàn", "s")])
    click(menu, 0)
    click(menu, 0)
    pump(50)
    assert ran == [0, 1] and not menu.isVisible()


def test_the_ring_stays_on_screen_near_a_corner(menu: RadialMenu) -> None:
    t = target()
    t.move(0, 0)
    menu.open_around(t, [RadialItem("A", "A", "a")])
    screen = (QApplication.primaryScreen()).availableGeometry()
    assert screen.contains(menu.geometry().topLeft()) and menu.width() > t.width()


def test_badge_active_and_label_callbacks_are_read_while_painting(menu: RadialMenu) -> None:
    calls: list[str] = []

    def count() -> int:
        calls.append("badge")
        return 3

    def on() -> bool:
        calls.append("active")
        return True

    menu.open_around(target(), [RadialItem("W", "W", "w", badge=count, active=on, label=lambda: calls.append("label") or "Stop watching")])
    menu.grab()  # forces a paint
    assert {"badge", "active", "label"} <= set(calls)


@pytest.fixture
def ui(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> Any:
    set_language("en")
    client = DaemonClient("127.0.0.1", 1, "x")
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(client, "send", lambda p: sent.append(p) or True)
    app = UiApp(qapp, load_settings(), client, with_avatar=True, with_hotkeys=False)
    app.sent = sent  # type: ignore[attr-defined]
    app.avatar.show()
    yield app
    app.radial.close()
    for w in (app.chat, app.approvals, app.manager, app.settings, app.quick, app.bubble, app.avatar, app.highlight, app.watch_badge):
        w.hide()
    app.tray.hide()


def test_right_clicking_the_avatar_opens_the_ring_and_pauses_wandering(ui: UiApp) -> None:
    ui.avatar.set_wander(True)
    ui.avatar.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5)))
    assert ui.radial.isVisible() and not ui.avatar.wandering  # still, so the ring stays centred on it
    assert [i.en for i in ui.radial.items] == ["Chat", "Talk", "Capture", "Watch", "Approvals", "Manage", "More", "Pause agent"]
    ui.radial.close()
    assert ui.avatar.wandering  # restored when the ring closes


def test_ring_items_reach_the_app_and_the_kill_switch_asks_twice(ui: UiApp) -> None:
    ui.open_radial()
    names = [i.en for i in ui.radial.items]
    click(ui.radial, names.index("Manage"))
    assert [i.en for i in ui.radial.items][:3] == ["Memory", "Documents", "Tools / MCP"] and len(ui.radial.items) == 9  # 8 pages + back
    QTest.keyClick(ui.radial, Qt.Key.Key_Escape)
    click(ui.radial, names.index("Pause agent"))
    assert ui.radial.isVisible() and {"type": "kill"} not in ui.sent  # first click only arms it
    click(ui.radial, names.index("Pause agent"))
    pump(50)
    assert {"type": "kill"} in ui.sent
    ui.screen_state = "on"
    watch = next(i for i in ui._radial_items() if i.en == "Watch")
    assert watch.text() == "Stop watching" and watch.active is not None and watch.active()
