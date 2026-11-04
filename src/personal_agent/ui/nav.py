"""Left navigation rail shared by the chat and manager windows, and the hamburger that collapses it in both at once.

The rail only navigates: a page key is emitted and the owner decides what to show. Collapsed or open is one app-wide
state (`RAIL`), saved to `ui_state.json` in the data folder so it survives a restart.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QRadialGradient, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from personal_agent.settings.settings import data_dir
from personal_agent.ui.i18n import tr
from personal_agent.ui.theme import tok

# (key, English, Vietnamese); None draws a divider. Keys other than chat / approvals / settings are manager pages.
ITEMS: list[tuple[str, str, str] | None] = [
    ("chat", "Chat", "Trò chuyện"), ("approvals", "Approvals", "Chờ duyệt"), None,
    ("memory", "Memory", "Bộ nhớ"), ("rag", "Documents", "Tài liệu"), None,
    ("mcp", "Tools / MCP", "Công cụ / MCP"), ("schedules", "Schedules", "Lịch chạy"), ("rules", "Remembered approvals", "Quy tắc đã nhớ"),
    ("policy", "Policy", "Chính sách"), ("audit", "Audit log", "Nhật ký kiểm toán"), None,
    ("usage", "Usage", "Mức dùng"), ("settings", "Settings", "Cài đặt")]
WIDTH = 224


class RailState(QObject):
    changed = Signal(bool)  # True = collapsed

    def __init__(self) -> None:
        super().__init__()
        self._collapsed: bool | None = None  # read lazily, so tests can point PERSONAL_AGENT_HOME elsewhere first

    @staticmethod
    def _file() -> Any:
        return data_dir() / "ui_state.json"

    @property
    def collapsed(self) -> bool:
        if self._collapsed is None:
            try:
                self._collapsed = bool(json.loads(self._file().read_text(encoding="utf-8")).get("rail_collapsed"))
            except (OSError, ValueError, AttributeError):
                self._collapsed = False
        return self._collapsed

    def set(self, collapsed: bool, save: bool = True) -> None:
        if collapsed == self.collapsed:
            return
        self._collapsed = collapsed
        if save:
            try:
                self._file().write_text(json.dumps({"rail_collapsed": collapsed}), encoding="utf-8")
            except OSError:
                pass  # a read-only data folder only costs the remembered state
        self.changed.emit(collapsed)

    def toggle(self) -> None:
        self.set(not self.collapsed)


RAIL = RailState()


class Orb(QWidget):
    """The logo: a small glowing ball in the highlight colour."""

    def __init__(self, size: int = 28) -> None:
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, c = self.width(), QColor(tok("accent"))
        halo = QColor(c)
        halo.setAlpha(70)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(self.rect())
        g = QRadialGradient(QPointF(w * 0.4, w * 0.36), w * 0.5)
        g.setColorAt(0, QColor(255, 255, 255, 190))
        g.setColorAt(0.55, c)
        g.setColorAt(1, c)
        p.setBrush(g)
        p.drawEllipse(self.rect().adjusted(3, 3, -3, -3))


class NavRail(QFrame):
    navigate = Signal(str)

    def __init__(self, current: str, name: str = "") -> None:
        super().__init__()
        self.setObjectName("NavRail")
        self.setFixedWidth(WIDTH)
        self.current = current
        self.buttons: dict[str, QPushButton] = {}
        self.badges: dict[str, QLabel] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 14, 10, 12)
        lay.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 10)
        brand.addWidget(Orb())
        self.name = QLabel(name)
        self.name.setProperty("role", "title")
        brand.addWidget(self.name, 1)
        lay.addLayout(brand)
        for it in ITEMS:
            if it is None:
                sep = QFrame()
                sep.setObjectName("NavSep")
                sep.setFixedHeight(1)
                lay.addSpacing(6)
                lay.addWidget(sep)
                lay.addSpacing(6)
                continue
            key = it[0]
            b = QPushButton()
            b.setProperty("role", "nav")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            if key == "approvals":  # the only item that carries a count
                row = QHBoxLayout(b)
                row.setContentsMargins(0, 0, 8, 0)
                row.addStretch()
                badge = QLabel("0")
                badge.setObjectName("NavBadge")
                badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                badge.hide()
                badge.setFixedHeight(18)
                row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
                self.badges[key] = badge
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch()
        RAIL.changed.connect(self._apply)
        self._apply(RAIL.collapsed)
        self.set_current(current)
        self.retranslate()

    def _apply(self, collapsed: bool) -> None:
        self.setVisible(not collapsed)

    def set_current(self, key: str) -> None:
        self.current = key
        for k, b in self.buttons.items():
            b.setProperty("current", k == key)
            b.style().unpolish(b)
            b.style().polish(b)

    def set_badge(self, n: int) -> None:
        b = self.badges["approvals"]
        b.setText(str(n))
        b.setVisible(n > 0)

    def set_name(self, name: str) -> None:
        self.name.setText(name)

    def retranslate(self) -> None:
        for it in ITEMS:
            if it:
                self.buttons[it[0]].setText(tr(it[1], it[2]))


class _BurgerTip(QObject):
    """Keeps the hamburger's tooltip in step with the rail. A QObject, so Qt drops the connection when the button dies."""

    def __init__(self, button: QToolButton) -> None:
        super().__init__(button)
        self.button = button
        RAIL.changed.connect(self.update)
        self.update(RAIL.collapsed)

    def update(self, collapsed: bool) -> None:
        text = tr("Show sidebar (Ctrl+B)", "Hiện thanh bên (Ctrl+B)") if collapsed else tr("Hide sidebar (Ctrl+B)", "Ẩn thanh bên (Ctrl+B)")
        self.button.setToolTip(text)
        self.button.setAccessibleName(text)


def top_bar(window: QWidget, title: QLabel) -> QFrame:
    """Slim bar above a window's content: the hamburger (also Ctrl+B) and the page title."""
    bar = QFrame()
    bar.setObjectName("TopBar")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(10, 6, 14, 6)
    b = QToolButton()
    b.setObjectName("Burger")
    b.setText("☰")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(RAIL.toggle)
    _BurgerTip(b)
    QShortcut(QKeySequence("Ctrl+B"), window, RAIL.toggle)
    title.setProperty("role", "title")
    lay.addWidget(b)
    lay.addWidget(title, 1)
    bar.burger = b  # type: ignore[attr-defined]
    return bar
