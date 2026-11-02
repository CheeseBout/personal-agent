"""The avatar: a transparent, frameless, always-on-top, draggable desktop companion plus its speech bubble.

Light by design: the animation timer only runs while the window is visible AND the current animation has more than one
frame; there is no polling timer. Click-through where transparent comes from the window mask (rebuilt per frame).
"""

from __future__ import annotations

import random

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QVBoxLayout, QWidget

from personal_agent.ui.skin import Animation, Skin
from personal_agent.ui.theme import AVATAR_INK, STATE

SNAP_PX = 24
DRAG_PX = 4
PAT_ZONE = 0.4  # top fraction of the avatar that counts as the head
PAT_MIN_PX = 6  # horizontal travel that counts as one stroke segment
PAT_STROKES = 3  # direction changes needed (about two back-and-forth strokes)
PAT_IDLE_MS = 700  # pause that cancels a half-done stroke
PAT_MS = 2500  # how long the headpat pose stays


def snap_to_edges(rect: QRect, area: QRect, margin: int = SNAP_PX) -> QPoint:
    """Position for `rect` after a drag: stick to a screen edge when within `margin` of it, always stay on screen."""
    x, y = rect.x(), rect.y()
    if abs(x - area.left()) <= margin:
        x = area.left()
    elif abs(rect.right() - area.right()) <= margin:
        x = area.right() - rect.width() + 1
    if abs(y - area.top()) <= margin:
        y = area.top()
    elif abs(rect.bottom() - area.bottom()) <= margin:
        y = area.bottom() - rect.height() + 1
    x = max(area.left(), min(x, area.right() - rect.width() + 1))
    y = max(area.top(), min(y, area.bottom() - rect.height() + 1))
    return QPoint(x, y)


class AvatarWindow(QWidget):
    single_clicked = Signal()  # open the quick-input box
    double_clicked = Signal()  # open the chat window
    files_dropped = Signal(list)
    moved = Signal(QPoint)

    def __init__(self, skin: Skin, scale: float = 1.0, always_on_top: bool = True) -> None:
        super().__init__()
        self._skin, self._scale, self._state = skin, scale, "idle"
        self._anim: Animation = skin.animation("idle")
        self._frame = 0
        self._cache: dict[tuple[str, int, float, bool, bool], QPixmap] = {}
        self._shown = "idle"  # animation on screen: the agent state, or a short pose (walking / headpat)
        self._right = False  # skin art faces left; mirror it while moving right
        self._watching = False
        self._press: QPoint | None = None
        self._drag_offset = QPoint()
        self._dragging = False
        self._menu: QMenu | None = None
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.single_clicked)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._wander_timer = QTimer(self)
        self._wander_timer.setSingleShot(True)
        self._wander_timer.timeout.connect(self._wander_step)
        self._wander = False
        self._walk: QPropertyAnimation | None = None
        self._stroke_x: int | None = None  # headpat: last x, direction and number of direction changes while hovering the head
        self._stroke_dir = 0
        self._stroke_n = 0
        self._stroke_t = QTimer(self)  # no stroking for a moment resets the count
        self._stroke_t.setSingleShot(True)
        self._stroke_t.timeout.connect(self._stroke_reset)
        self._pat_t = QTimer(self)
        self._pat_t.setSingleShot(True)
        self._pat_t.timeout.connect(self._end_pat)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.set_always_on_top(always_on_top, show=False)
        self._apply_frame()

    # ---- configuration -----------------------------------------------------------------------------------------
    @property
    def always_on_top(self) -> bool:
        return self._on_top

    def set_always_on_top(self, on: bool, show: bool = True) -> None:
        self._on_top = on
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint if on else flags)
        if show:
            self.show()

    def set_skin(self, skin: Skin) -> None:
        self._skin, self._cache = skin, {}
        self.set_state(self._state, force=True)

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.5, min(float(scale), 3.0))
        self._cache = {}
        self._apply_frame()

    def set_label(self, name: str) -> None:
        self.setToolTip(name)
        self.setWindowTitle(name)

    def set_menu(self, menu: QMenu) -> None:
        self._menu = menu

    def set_wander(self, on: bool) -> None:
        self._wander = on
        self._schedule_wander()

    # ---- state / animation ----------------------------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str, force: bool = False) -> None:
        if state == self._state and not force:
            return
        self._state = state
        self._show(state)
        if state != "idle" and self._walk:
            self._walk.stop()
        self._schedule_wander()

    def _show(self, name: str) -> None:
        self._shown, self._frame = name, 0
        self._anim = self._skin.animation(name)
        self._apply_frame()
        self._sync_timer()

    def _sync_timer(self) -> None:
        if self.isVisible() and len(self._anim.frames) > 1:
            self._timer.start(int(1000 / self._anim.fps))
        else:
            self._timer.stop()

    def _tick(self) -> None:
        last = len(self._anim.frames) - 1
        if self._frame >= last and not self._anim.loop:
            self._timer.stop()
            return
        self._frame = (self._frame + 1) % len(self._anim.frames)
        self._apply_frame()

    def set_watching(self, on: bool) -> None:
        """Red dot in the corner while continuous screen watching is on (part of the pixmap, so it is also part of the click mask)."""
        if on != self._watching:
            self._watching, self._cache = on, {}
            self._apply_frame()

    def _pixmap(self) -> QPixmap:
        key = (self._shown, self._frame, self._scale, self._watching, self._right)
        if key not in self._cache:
            src = self._anim.frames[min(self._frame, len(self._anim.frames) - 1)]
            w = max(16, int(self._skin.size * self._scale))
            pm = src.scaled(w, w, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            if self._right:
                pm = pm.transformed(QTransform().scale(-1, 1))
            if self._watching:
                pm = pm.copy()
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setBrush(QColor(STATE["error"]))
                p.setPen(QPen(QColor(AVATAR_INK), 2))
                p.drawEllipse(QPoint(pm.width() - 13, 13), 7, 7)
                p.end()
            self._cache[key] = pm
        return self._cache[key]

    def _face(self, dx: int) -> None:
        if dx and (dx > 0) != self._right:
            self._right = dx > 0
            self._apply_frame()

    def _apply_frame(self) -> None:
        pm = self._pixmap()
        if self.size() != pm.size():
            self.resize(pm.size())
        self.setMask(pm.mask())  # transparent pixels let clicks through to the window below
        self.update()

    def paintEvent(self, _: object) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap())

    def showEvent(self, e: object) -> None:
        super().showEvent(e)  # type: ignore[arg-type]
        self._sync_timer()
        self._schedule_wander()

    def hideEvent(self, e: object) -> None:
        super().hideEvent(e)  # type: ignore[arg-type]
        self._timer.stop()
        self._wander_timer.stop()

    # ---- placement -----------------------------------------------------------------------------------------------
    def _area(self) -> QRect:
        scr = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        return scr.availableGeometry()

    def place_default(self) -> None:
        a = self._area()
        self.move(a.right() - self.width() - 24, a.bottom() - self.height() - 24)

    def keep_on_screen(self) -> None:
        """Pull the window back into a visible screen (monitor unplugged, resolution changed)."""
        if not any(s.availableGeometry().intersects(self.frameGeometry()) for s in QApplication.screens()):
            self.place_default()

    # ---- mouse: click / double-click / drag / menu ---------------------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._drag_offset = self._press - self.frameGeometry().topLeft()
            self._dragging = False
            if self._walk:
                self._walk.stop()  # stop() does not emit finished
                self._end_walk()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._press is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            if e.buttons() == Qt.MouseButton.NoButton:
                self._stroke(e.position().toPoint())
            return
        g = e.globalPosition().toPoint()
        if not self._dragging and (g - self._press).manhattanLength() >= DRAG_PX:
            self._dragging = True
            self._click_timer.stop()
        if self._dragging:
            new = g - self._drag_offset
            self._face(new.x() - self.x())
            self.move(new)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        self._press = None
        self._schedule_wander()  # press stopped any walk (no `finished` signal) and may have eaten the pending timer
        if self._dragging:
            self._dragging = False
            self.move(snap_to_edges(self.frameGeometry(), self._area()))
            self.moved.emit(self.pos())
        else:  # wait out the double-click interval so a double click does not also fire the single click
            self._click_timer.start(QApplication.doubleClickInterval())

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self.double_clicked.emit()

    def contextMenuEvent(self, e: QContextMenuEvent) -> None:
        if self._menu is not None:
            self._menu.exec(e.globalPos())

    # ---- drag and drop files / images onto the avatar ----------------------------------------------------------------------
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.files_dropped.emit(paths)

    # ---- headpat: stroke the top of the head back and forth with no button pressed ---------------------------------------
    def _stroke_reset(self) -> None:
        self._stroke_x, self._stroke_dir, self._stroke_n = None, 0, 0

    def _stroke(self, pos: QPoint) -> None:
        if self._state not in ("idle", "sleeping") or self._shown == "headpat" or pos.y() > self.height() * PAT_ZONE:
            return self._stroke_reset()
        if self._stroke_x is not None and abs(pos.x() - self._stroke_x) >= PAT_MIN_PX:
            d = 1 if pos.x() > self._stroke_x else -1
            self._stroke_n += d != self._stroke_dir and self._stroke_dir != 0
            self._stroke_dir = d
            self._stroke_x = pos.x()
            if self._stroke_n >= PAT_STROKES:
                return self._headpat()
        elif self._stroke_x is None:
            self._stroke_x = pos.x()
        self._stroke_t.start(PAT_IDLE_MS)

    def _headpat(self) -> None:
        self._stroke_reset()
        if self._walk:
            self._walk.stop()
        self._show("headpat")
        self._pat_t.start(PAT_MS)

    def _end_pat(self) -> None:
        if self._shown == "headpat":
            self._show(self._state)
        self._schedule_wander()

    # ---- optional idle wandering ----------------------------------------------------------------------------------------
    def _schedule_wander(self) -> None:
        self._wander_timer.stop()
        if self._wander and self.isVisible() and self._state == "idle":
            self._wander_timer.start(random.randint(20_000, 60_000))

    def _wander_step(self) -> None:
        if not (self._wander and self._state == "idle") or self._dragging:
            return
        a = self._area()
        target = QPoint(random.randint(a.left(), max(a.left(), a.right() - self.width())), self.y())
        self._face(target.x() - self.x())
        self._walk = QPropertyAnimation(self, b"pos", self)
        self._walk.setDuration(max(800, abs(target.x() - self.x()) * 8))
        self._walk.setEndValue(target)
        self._walk.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._walk.finished.connect(self._end_walk)
        self._show("walking")
        self._walk.start()

    def _end_walk(self) -> None:
        if self._shown == "walking":
            self._show(self._state)
        self._schedule_wander()


class SpeechBubble(QWidget):
    """Short status text next to the avatar. `sticky` bubbles (approval prompts) stay until replaced or clicked."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(260)
        self._label.setObjectName("Bubble")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)

    def say(self, text: str, near: QWidget, ms: int = 4000, sticky: bool = False) -> None:
        self._label.setText(text[:240])
        self.adjustSize()
        a = (QApplication.screenAt(near.frameGeometry().center()) or QApplication.primaryScreen()).availableGeometry()
        x = max(a.left(), min(near.x() + near.width() // 2 - self.width() // 2, a.right() - self.width()))
        y = near.y() - self.height() - 4
        self.move(x, y if y >= a.top() else near.y() + near.height() + 4)  # no room above: show below
        self.show()
        self._hide.stop()
        if not sticky:
            self._hide.start(ms)

    def mousePressEvent(self, _: QMouseEvent) -> None:
        self.clicked.emit()
        self.hide()

