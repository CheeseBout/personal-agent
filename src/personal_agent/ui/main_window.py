"""The one window: a sidebar plus a stack with the chat, the manager pages and the settings.

Chat, manager and settings used to be three windows with a sidebar each. Now the sidebar exists once; it switches
the page in place. Approvals stay a separate card on purpose: a risky action must be able to interrupt whatever page is open.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from personal_agent.ui.chat import ChatWindow
from personal_agent.ui.i18n import tr
from personal_agent.ui.nav import NavRail, top_bar
from personal_agent.ui.pages import ManagerWindow
from personal_agent.ui.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    approvals_requested = Signal()  # the sidebar's Approvals item: handled by the app (it is its own card)
    settings_opened = Signal()  # ask the daemon for fresh settings whenever the page is shown

    def __init__(self, chat: ChatWindow, manager: ManagerWindow, settings: SettingsDialog, name: str) -> None:
        super().__init__()
        self.chat, self.manager, self.settings, self._name = chat, manager, settings, name
        self.stack = QStackedWidget()
        for w in (chat, manager, settings):
            self.stack.addWidget(w)
        self.rail = NavRail("chat", name)
        self.rail.navigate.connect(self.open)
        self.page_title = QLabel()
        bar = top_bar(self, self.page_title)
        bar.layout().addWidget(manager.status)  # type: ignore[union-attr]
        manager.title_changed.connect(self._manager_title)
        shell = QWidget()
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.rail)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(bar)
        col.addWidget(self.stack, 1)
        row.addLayout(col, 1)
        self.setCentralWidget(shell)
        self.resize(1120, 700)
        self.current = "chat"
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(self._name)
        self.rail.set_name(self._name)
        self.rail.retranslate()
        self._title(self.current)

    def set_name(self, name: str) -> None:
        self._name = name
        self.retranslate()

    def _title(self, key: str) -> None:
        if key == "chat":
            self.page_title.setText(tr("Chat", "Trò chuyện"))
        elif key == "settings":
            self.page_title.setText(tr("Settings", "Cài đặt"))

    def _manager_title(self, text: str) -> None:
        if self.current not in ("chat", "settings"):
            self.page_title.setText(text)

    def open(self, key: str) -> None:
        """Switch the page in place (a sidebar click). Approvals are not a page: they raise their own card."""
        if key == "approvals":
            self.approvals_requested.emit()
            return
        self.current = key
        self.rail.set_current(key)
        if key == "chat":
            self.stack.setCurrentWidget(self.chat)
            self._title(key)
        elif key == "settings":
            self.stack.setCurrentWidget(self.settings)
            self._title(key)
            self.settings_opened.emit()
        else:
            self.stack.setCurrentWidget(self.manager)
            self.manager.open_page(key)

    def show_page(self, key: str = "chat") -> None:
        """Bring the window forward on `key` (from the tray, the avatar, a hotkey or the radial menu)."""
        self.open(key)
        self.show()
        self.raise_()
        self.activateWindow()
        if key == "chat":
            self.chat.composer.setFocus()

    def closeEvent(self, e: Any) -> None:  # closing only hides it: the app lives in the tray
        e.ignore()
        self.hide()
