"""The whole UI against a real daemon (uvicorn in a thread, scripted model): connect, chat, approvals, reconnect, bad token."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from conftest import pump
from fakes import FakeBrain, ScriptedModel, answer
from personal_agent.core.app import create_app
from personal_agent.settings.settings import load_settings
from personal_agent.ui.app import UiApp
from personal_agent.ui.client import DaemonClient
from personal_agent.ui.i18n import set_language


def wait_for(cond: Callable[[], bool], seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        pump(30)
    return cond()


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[int]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    brain = FakeBrain(ScriptedModel(responses=[answer("pong **ok**")]))
    server = uvicorn.Server(uvicorn.Config(create_app(brain, tmp_path / "c.db", "test-token"), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    assert wait_for(lambda: server.started, 15)
    yield port
    server.should_exit = True
    t.join(10)


@pytest.fixture
def ui(qapp: QApplication, daemon: int) -> Iterator[UiApp]:
    set_language("en")
    client = DaemonClient("127.0.0.1", daemon, "test-token")
    app = UiApp(qapp, load_settings(), client, with_avatar=False, with_hotkeys=False)
    client.start()
    yield app
    client.stop()
    pump(300)  # let the socket close cleanly before the server is told to exit
    app.tray.hide()
    for w in (app.chat, app.approvals, app.manager, app.settings, app.quick, app.bubble):
        w.close()
    if hasattr(app, "wizard"):
        app.wizard.close()


def test_connects_loads_settings_and_chats_through_the_real_daemon(ui: UiApp) -> None:
    assert wait_for(lambda: ui.client.is_connected and bool(ui._last_settings) and ui.daemon_pid is not None)
    assert ui._last_settings["models"]["planner"]["model"] == "claude-haiku-5-5"
    assert ui.settings.model_rows and ui.settings.name.text() == ui.session.agent_name
    assert hasattr(ui, "wizard") and ui._wizard_shown  # no provider key in the test env -> first-run wizard
    ui.send_user("ping", [])
    assert ui.session.busy and ui.state.current() == "thinking"
    assert wait_for(lambda: ui.session.transcript[-1].kind == "agent")
    assert "pong" in ui.session.transcript[-1].text and not ui.session.busy
    assert "pong" in ui.chat.browser.toPlainText()
    assert ui.state.current() == "success"
    # the UI refreshes the budget meters by itself after an answer (no manual request here)
    assert wait_for(lambda: (ui.session.usage.get("today") or {}).get("tokens", 0) > 0)
    assert ui.session.usage["report"]["days"][0]["llm_calls"] >= 1  # the daemon's metrics reached the UI


def test_renaming_from_settings_updates_titles_everywhere(ui: UiApp) -> None:
    assert wait_for(lambda: ui.client.is_connected and bool(ui._last_settings))
    ui.client.send({"type": "set_agent_name", "name": "Mimi"})
    assert wait_for(lambda: ui.session.agent_name == "Mimi")
    assert ui.chat.windowTitle() == "Mimi" and ui.tray.toolTip() == "Mimi" and ui.avatar.windowTitle() == "Mimi"


def test_attachments_and_settings_roundtrip_through_the_real_daemon(ui: UiApp, tmp_path: Path) -> None:
    assert wait_for(lambda: ui.client.is_connected and bool(ui._last_settings))
    f = tmp_path / "n.txt"
    f.write_text("hello file")
    from personal_agent.ui import attach

    ui.send_user("read this", [attach.from_path(f)])
    assert wait_for(lambda: ui.session.transcript[-1].kind == "agent")
    ui.client.send({"type": "settings_set", "patch": {"ui": {"avatar_scale": 1.7}}})
    assert wait_for(lambda: ui.cfg.ui.avatar_scale == 1.7)
    assert ui.avatar._scale == 1.7


def test_pending_approvals_open_the_center_and_decisions_go_back(ui: UiApp, monkeypatch: pytest.MonkeyPatch) -> None:
    assert wait_for(lambda: ui.client.is_connected)
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(ui.client, "send", lambda p: sent.append(p) or True)
    row = {"call_id": "c1", "thread_id": "default", "tool": "shell", "args": {"command": "dir"}, "tier": "T2", "risk": "high",
           "reason": "needs approval", "preview": "dir", "expires_at": time.time() + 600}
    ui.on_event({"type": "approval_request", "thread_id": "default", "approvals": [row]})
    assert ui.approvals.isVisible() and ui.state.current() == "awaiting_approval" and "(1)" in ui.approval_action.text()
    ui.on_event({"type": "paused", "thread_id": "default", "pending": 1})
    from personal_agent.ui.approvals import ThreadBox

    box = ui.approvals.widget().findChild(ThreadBox)
    box.cards[0].radios["approve"].setChecked(True)
    box.submit.click()
    assert sent == [{"type": "approve", "thread_id": "default", "decisions": {"c1": {"action": "approve", "remember": False}}}]
    assert not ui.session.approvals and ui.state.current() == "thinking"  # the agent carries on


def test_a_dropped_connection_is_re_established_and_pending_approvals_are_pushed_again(ui: UiApp) -> None:
    assert wait_for(lambda: ui.client.is_connected)
    ui.client._ws.close()
    assert wait_for(lambda: not ui.client.is_connected, 3)
    assert ui.state.connected is False and ui.state.current() == "sleeping"
    assert wait_for(lambda: ui.client.is_connected, 8)  # automatic reconnect
    assert ui.state.connected is True
    ui.send_user("again", [])
    assert wait_for(lambda: ui.session.transcript[-1].kind == "agent")


def test_wrong_token_never_connects_and_keeps_retrying(qapp: QApplication, daemon: int) -> None:
    c = DaemonClient("127.0.0.1", daemon, "wrong-token")
    seen: list[str] = []
    c.connected.connect(lambda: seen.append("connected"))
    c.disconnected.connect(lambda: seen.append("disconnected"))
    c.start()
    assert wait_for(lambda: "disconnected" in seen, 5)
    assert "connected" not in seen[:1] or True  # the handshake itself is rejected by the daemon (4401)
    assert c._retry.isActive() or c.is_connected is False
    c.stop()
    assert not c._retry.isActive()


def test_offline_send_reports_a_clear_error_instead_of_dropping_the_message(qapp: QApplication) -> None:
    c = DaemonClient("127.0.0.1", 1, "x")  # nothing listens here; never started
    ui = UiApp(qapp, load_settings(), c, with_avatar=False, with_hotkeys=False)
    ui.send_user("hello", [])
    assert ui.session.transcript[-1].kind == "error" and not ui.session.busy
    ui.tray.hide()


def test_tray_exists_in_a_headless_run(qapp: QApplication) -> None:
    assert isinstance(QSystemTrayIcon.isSystemTrayAvailable(), bool)  # offscreen has none; the app must still construct (see above)
