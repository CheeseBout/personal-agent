"""Sidebar + hamburger, the neutral black dark theme, and the user's highlight colour."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from test_ui_windows import settings_event

from personal_agent.settings.settings import UiCfg
from personal_agent.ui import nav, theme
from personal_agent.ui.chat import ChatWindow
from personal_agent.ui.i18n import set_language
from personal_agent.ui.model import Session
from personal_agent.ui.pages import ManagerWindow
from personal_agent.ui.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Own data folder for the remembered sidebar state, and always start with the sidebar open."""
    set_language("en")
    monkeypatch.setenv("PERSONAL_AGENT_HOME", str(tmp_path))
    nav.RAIL._collapsed = False


def test_dark_theme_is_neutral_black_and_accents_follow_the_highlight() -> None:
    dark = theme.tokens(True, "#3340D9")
    assert dark["bg"] == "#0A0A0A" and all(dark[k][1:3] == dark[k][3:5] == dark[k][5:7] for k in ("bg", "surface", "sunken", "line", "ink", "muted"))
    assert dark["accent"] == "#3340D9" and dark["on_accent"] == "#FFFFFF"  # white text on deep blue
    assert theme.tokens(False)["on_accent"] == "#111111"  # near-black text on the default orange
    assert theme.mix("#FFFFFF", "#000000", 0.5) == "#808080"


class FakeApp:
    """Stands in for QApplication: restyling the real one after an earlier test destroyed a QWizard crashes Qt on Windows."""

    class _Style:
        def objectName(self) -> str:
            return "fusion"

    class _Hints:
        def colorScheme(self) -> Qt.ColorScheme:
            return Qt.ColorScheme.Dark

    def __init__(self) -> None:
        self.sheets: list[str] = []

    def style(self) -> _Style:
        return self._Style()

    def styleHints(self) -> _Hints:
        return self._Hints()

    def setPalette(self, _p: object) -> None:
        pass

    def setStyleSheet(self, s: str) -> None:
        self.sheets.append(s)


def test_set_highlight_reskins_the_running_app() -> None:
    app = FakeApp()
    theme.apply(app)  # type: ignore[arg-type]
    theme.set_highlight(app, "#0F9D8A")  # type: ignore[arg-type]
    assert theme.tok("accent") == "#0F9D8A" and theme.highlight() == "#0F9D8A" and "#0F9D8A" in app.sheets[-1] and "#0A0A0A" in app.sheets[-1]
    n = len(app.sheets)
    theme.set_highlight(app, "#0f9d8a")  # same colour: nothing to redo
    assert len(app.sheets) == n
    theme.set_highlight(app, theme.DEFAULT_HIGHLIGHT)  # type: ignore[arg-type]
    assert theme.tok("accent") == theme.DEFAULT_HIGHLIGHT


def test_highlight_setting_only_accepts_a_hex_colour() -> None:
    assert UiCfg().highlight == "#E8742F" and UiCfg(highlight="#00ff7f").highlight == "#00ff7f"
    for bad in ("orange", "#12345", "#GGGGGG", "red; border: none"):
        with pytest.raises(ValidationError):
            UiCfg(highlight=bad)


def test_hamburger_collapses_and_restores_the_sidebar_in_every_window(qapp: QApplication) -> None:
    chat, mgr = ChatWindow(Session()), ManagerWindow(lambda _m: None)
    assert not chat.rail.isHidden() and not mgr.rail.isHidden()
    chat.findChild(nav.QToolButton, "Burger").click()
    assert chat.rail.isHidden() and mgr.rail.isHidden()  # one app-wide state
    assert (Path(nav.data_dir()) / "ui_state.json").read_text(encoding="utf-8") == '{"rail_collapsed": true}'
    mgr.findChild(nav.QToolButton, "Burger").click()
    assert not chat.rail.isHidden() and not mgr.rail.isHidden()


def test_sidebar_navigates_between_pages_and_windows(qapp: QApplication) -> None:
    mgr, chat = ManagerWindow(lambda _m: None), ChatWindow(Session())
    asked: list[str] = []
    mgr.navigate_out.connect(asked.append)
    chat.navigate.connect(asked.append)
    mgr.rail.buttons["rag"].click()
    assert mgr.stack.currentWidget() is mgr.pages["rag"][0] and mgr.page_title.text() == "Documents" and mgr.rail.current == "rag"
    mgr.rail.buttons["chat"].click()
    mgr.rail.buttons["settings"].click()
    chat.rail.buttons["memory"].click()
    assert asked == ["chat", "settings", "memory"]


def test_approval_count_shows_as_a_badge() -> None:
    rail = nav.NavRail("chat")
    assert rail.badges["approvals"].isHidden()
    rail.set_badge(3)
    assert rail.badges["approvals"].text() == "3" and not rail.badges["approvals"].isHidden()
    rail.set_badge(0)
    assert rail.badges["approvals"].isHidden()


def test_settings_dialog_saves_the_chosen_highlight(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    previews: list[str] = []
    monkeypatch.setattr("personal_agent.ui.system.autostart_enabled", lambda *a, **k: False)
    monkeypatch.setattr("personal_agent.ui.settings_dialog.set_highlight", lambda _app, c: previews.append(c))
    d = SettingsDialog(sent.append, lambda: None)
    d.load(settings_event())  # an older daemon that sends no highlight means "default"
    assert d.highlight == theme.DEFAULT_HIGHLIGHT and d.hl_swatches[theme.DEFAULT_HIGHLIGHT].isChecked()
    d.save()
    assert sent == []
    d.hl_swatches["#7A4DE0"].click()
    assert previews == ["#7A4DE0"]  # the app re-skins immediately, before Save
    d.save()
    assert next(m for m in sent if m["type"] == "settings_set")["patch"] == {"ui": {"highlight": "#7A4DE0"}}
    d.hl_reset.click()
    assert previews[-1] == theme.DEFAULT_HIGHLIGHT and d.hl_swatches[theme.DEFAULT_HIGHLIGHT].isChecked()


def test_save_button_saves_once_closing_drops_the_unsaved_preview_and_swatches_have_names(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    previews: list[str] = []
    monkeypatch.setattr("personal_agent.ui.system.autostart_enabled", lambda *a, **k: False)
    monkeypatch.setattr("personal_agent.ui.settings_dialog.set_highlight", lambda _app, c: previews.append(c))
    d = SettingsDialog(sent.append, lambda: None)
    d.load(settings_event())
    d.name.setText("Bé Na")
    d.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()  # the real button, not d.save()
    assert [m["type"] for m in sent] == ["set_agent_name"]  # once, not twice
    assert d.hl_swatches["#3340D9"].accessibleName() == "Ultramarine" and "#3340D9" in d.hl_swatches["#3340D9"].toolTip()
    set_language("vi")
    d.retranslate()
    assert d.hl_swatches["#3340D9"].accessibleName() == "Xanh chàm"
    d.show()
    d.hl_swatches["#7A4DE0"].click()
    d.hide()  # Close without saving
    assert previews == ["#7A4DE0", theme.DEFAULT_HIGHLIGHT] and d.highlight == theme.DEFAULT_HIGHLIGHT
    d.show()
    d.hl_swatches["#7A4DE0"].click()
    d.save()
    d.hide()  # saved: the colour stays
    assert previews[-1] == "#7A4DE0" and d.highlight == "#7A4DE0"
