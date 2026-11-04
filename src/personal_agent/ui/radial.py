"""Radial menu: a ring of slots drawn around the avatar (opened with the right mouse button).

Drawn from the same theme tokens as every window: surface-coloured slots, the user's highlight colour for hover and
focus, a filled red slot while a dangerous action is armed. Mouse, number keys 1-9 and the arrow keys + Enter all work.

A frameless translucent popup, so Qt closes it on any click outside and it never touches the avatar's own mouse handling
(drag, snap, headpat). A slot either runs an action, opens a sub-ring (the last slot of a sub-ring goes back), or - for a
dangerous action - needs a second click. `slot_at` is pure geometry and tested without Qt.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from personal_agent.ui.i18n import tr
from personal_agent.ui.theme import on_color, tok

THICK = 70  # ring width in pixels
GAP_DEG = 2.0  # visual gap between slots
MARGIN = 6


def slot_at(dx: float, dy: float, r_in: float, r_out: float, n: int) -> int | None:
    """Which of `n` slots lies at offset (dx, dy) from the ring centre. Slot 0 is centred at the top, the others follow clockwise."""
    r = math.hypot(dx, dy)
    if n <= 0 or not r_in <= r <= r_out:
        return None
    cw = math.degrees(math.atan2(dx, -dy)) % 360  # degrees clockwise from the top
    step = 360 / n
    return int(((cw + step / 2) % 360) // step)


@dataclass
class RadialItem:
    en: str
    vi: str
    glyph: str
    action: Callable[[], None] | None = None
    children: list[RadialItem] | None = None
    badge: Callable[[], int] | None = None  # a count drawn on the slot (e.g. pending approvals)
    active: Callable[[], bool] | None = None  # drawn highlighted while true (e.g. watching the screen)
    label: Callable[[], str] | None = None  # text that changes with state; defaults to tr(en, vi)
    danger: bool = False  # needs a second click

    def text(self) -> str:
        return self.label() if self.label else tr(self.en, self.vi)


BACK = RadialItem("Back", "Quay lại", "◀")


class RadialMenu(QWidget):
    closed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._root: list[RadialItem] = []
        self._stack: list[list[RadialItem]] = []
        self._armed: RadialItem | None = None
        self._hover: int | None = None
        self._r_in = 60.0
        self._r_out = self._r_in + THICK

    # ---- opening ------------------------------------------------------------------------------------------------------------
    def open_around(self, target: QWidget, items: list[RadialItem]) -> None:
        """Show the ring centred on `target` (shifted inward when that would run off the screen)."""
        self._root, self._stack, self._armed, self._hover = items, [items], None, None
        self._r_in = float(max(56, min(150, max(target.width(), target.height()) // 2 + 10)))
        self._r_out = self._r_in + THICK
        side = int(2 * (self._r_out + MARGIN))
        self.resize(side, side)
        c = target.frameGeometry().center()
        a = (QApplication.screenAt(c) or QApplication.primaryScreen()).availableGeometry()
        x = max(a.left(), min(c.x() - side // 2, a.right() - side + 1))
        y = max(a.top(), min(c.y() - side // 2, a.bottom() - side + 1))
        self.move(x, y)
        self.show()
        self.setFocus()

    @property
    def items(self) -> list[RadialItem]:
        cur = self._stack[-1] if self._stack else []
        return cur + [BACK] if len(self._stack) > 1 else cur

    @property
    def _centre(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def slot_center(self, i: int) -> QPointF:
        """Centre of slot `i` in widget coordinates (used by the painter and by tests)."""
        a = math.radians(i * 360 / len(self.items))
        r = self._r_in + THICK / 2
        return QPointF(self._centre.x() + r * math.sin(a), self._centre.y() - r * math.cos(a))

    # ---- choosing -----------------------------------------------------------------------------------------------------------
    def _slot(self, p: QPointF) -> int | None:
        return slot_at(p.x() - self._centre.x(), p.y() - self._centre.y(), self._r_in, self._r_out, len(self.items))

    def choose(self, i: int) -> None:
        item = self.items[i]
        self._armed = self._armed if item is self._armed else None
        if item is BACK:
            self._stack.pop()
        elif item.children is not None:
            self._stack.append(item.children)
        elif item.danger and self._armed is not item:
            self._armed = item  # the first click only arms it
        else:
            self.close()
            if item.action:
                QTimer.singleShot(0, item.action)  # after the popup is gone, so a dialog it opens is not eaten by the popup
            return
        self._hover = None
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        h = self._slot(e.position())
        if h != self._hover:
            self._hover = h
            self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        i = self._slot(e.position()) if e.button() == Qt.MouseButton.LeftButton else None
        if i is None:
            self.close()  # right click, or a click in the hole / outside the ring
        else:
            self.choose(i)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.choose(len(self.items) - 1) if len(self._stack) > 1 else self.close()
        elif Qt.Key.Key_1 <= e.key() <= Qt.Key.Key_9 and (i := e.key() - Qt.Key.Key_1) < len(self.items):
            self.choose(i)  # number keys pick a slot, clockwise from the top
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Up):
            n = len(self.items)
            forward = e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down)
            self._hover = (0 if forward else n - 1) if self._hover is None else (self._hover + (1 if forward else -1)) % n
            self.update()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self._hover is not None:
            self.choose(self._hover)

    def hideEvent(self, e: object) -> None:
        super().hideEvent(e)  # type: ignore[arg-type]
        self._stack, self._armed, self._hover = [], None, None
        self.closed.emit()

    # ---- painting -----------------------------------------------------------------------------------------------------------
    def _sector(self, i: int, n: int) -> QPainterPath:
        c, step = self._centre, 360 / n
        outer = QRectF(c.x() - self._r_out, c.y() - self._r_out, 2 * self._r_out, 2 * self._r_out)
        inner = QRectF(c.x() - self._r_in, c.y() - self._r_in, 2 * self._r_in, 2 * self._r_in)
        a0, a1 = i * step - step / 2 + GAP_DEG / 2, i * step + step / 2 - GAP_DEG / 2  # clockwise from the top
        start, span = 90 - a1, a1 - a0  # Qt counts counter-clockwise from 3 o'clock
        path = QPainterPath()
        path.arcMoveTo(outer, start)
        path.arcTo(outer, start, span)
        path.arcTo(inner, start + span, -span)
        path.closeSubpath()
        return path

    def paintEvent(self, _: object) -> None:
        items = self.items
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        surface, ink, muted, accent = tok("surface"), tok("ink"), tok("muted"), tok("accent")
        for i, item in enumerate(items):
            armed, hover = item is self._armed, i == self._hover
            on = bool(item.active and item.active())
            danger = item.danger or armed
            if armed:
                fill, text, edge = tok("critical"), on_color(tok("critical")), QPen(Qt.PenStyle.NoPen)
            elif hover:
                fill, text, edge = accent, tok("on_accent"), QPen(Qt.PenStyle.NoPen)
            elif on:
                fill, text, edge = tok("accent_soft"), ink, QPen(QColor(accent), 2)
            else:
                fill, text = surface, tok("critical") if danger else ink
                edge = QPen(QColor(tok("critical")) if danger else _alpha(muted, 90), 1)
            p.setBrush(_alpha(fill, 246))
            p.setPen(edge)
            p.drawPath(self._sector(i, len(items)))
            c = self.slot_center(i)
            p.setPen(QColor(text))
            f = QFont(self.font())
            g = QFont(self.font())
            g.setFamilies(["Segoe UI Symbol", "Segoe UI"])  # monochrome glyphs, so they take the slot's text colour
            g.setPointSizeF(15)
            p.setFont(g)
            p.drawText(QRectF(c.x() - 40, c.y() - 27, 80, 26), Qt.AlignmentFlag.AlignCenter, item.glyph + "︎")  # VS15: text, not colour emoji
            f.setPointSizeF(8.5)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            label = tr("Click again", "Bấm lần nữa") if armed else item.text() + (" ›" if item.children is not None else "")
            p.drawText(QRectF(c.x() - 40, c.y() + 1, 80, 30), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap), label)
            a = math.radians(i * 360 / len(items))
            if i < 9:  # the number key that picks this slot
                f.setPointSizeF(7)
                f.setWeight(QFont.Weight.Normal)
                p.setFont(f)
                p.setPen(_alpha(text, 150))
                r = self._r_out - 9
                p.drawText(QRectF(self._centre.x() + r * math.sin(a) - 8, self._centre.y() - r * math.cos(a) - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, str(i + 1))
            if item.badge and (n := item.badge()) > 0:  # same badge as the sidebar's, inverted while the slot is highlighted
                bx, by = c.x() + 30 * math.sin(a + 0.5), c.y() - 30 * math.cos(a + 0.5)
                p.setBrush(QColor(tok("on_accent") if hover else accent))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(bx, by), 10, 10)
                p.setPen(QColor(accent if hover else tok("on_accent")))
                f.setPointSizeF(8)
                f.setBold(True)
                p.setFont(f)
                p.drawText(QRectF(bx - 10, by - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, str(min(n, 99)))
        # a thin highlight-coloured frame around the avatar, like the logo's halo
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_alpha(accent, 170), 2))
        p.drawEllipse(self._centre, self._r_in - 4, self._r_in - 4)
        p.end()


def _alpha(color: str, a: int) -> QColor:
    c = QColor(color)
    c.setAlpha(a)
    return c
