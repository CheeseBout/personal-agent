"""Screen features in the UI: on-demand capture (screen / window / region -> chat attachment), the target highlight shown before
every agent action, the always-visible "screen watching" indicator, and the drag-to-select region overlay."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from personal_agent.settings.settings import ScreenCfg
from personal_agent.ui import attach
from personal_agent.ui.i18n import tr

Rect = tuple[int, int, int, int]


# ---- capture -> attachment ------------------------------------------------------------------------------------------------------
def capture_attachment(kind: str, cfg: ScreenCfg, rect: Rect | None = None) -> tuple[dict[str, Any], int, str]:
    """Capture (with privacy masking) and package as a chat attachment -> (attachment, masked_count, window_title).
    Raises capture.Blocked / capture.CaptureError with a message that is safe to show."""
    from personal_agent.perception import capture

    shot = capture.capture(kind, cfg, rect=rect)
    img = capture.downscale(shot.image, 2200)
    png = capture.to_png(img)
    name = f"screen-{time.strftime('%H%M%S')}.png"
    if len(png) > attach.MAX_BYTES:  # busy screens compress badly as PNG
        return attach.from_bytes(name.replace(".png", ".jpg"), capture.to_jpeg(img, 2200, 85), "image/jpeg"), shot.masked, shot.title
    return attach.from_bytes(name, png, "image/png"), shot.masked, shot.title


def physical_to_logical(rect: Rect) -> QRect:
    """Physical screen pixels (what the daemon uses) -> Qt logical coordinates, per monitor (DPI scaling)."""
    from personal_agent.perception import capture

    screens, mons = QGuiApplication.screens(), capture.monitor_rects()
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    for i, m in enumerate(mons):
        if m[0] <= cx < m[2] and m[1] <= cy < m[3] and i < len(screens):
            s = screens[i]
            dpr, g = s.devicePixelRatio(), s.geometry()
            return QRect(g.x() + int((rect[0] - m[0]) / dpr), g.y() + int((rect[1] - m[1]) / dpr), int((rect[2] - rect[0]) / dpr), int((rect[3] - rect[1]) / dpr))
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    return QRect(int(rect[0] / dpr), int(rect[1] / dpr), int((rect[2] - rect[0]) / dpr), int((rect[3] - rect[1]) / dpr))


def logical_to_physical(r: QRect, screen_index: int) -> Rect:
    from personal_agent.perception import capture

    screens, mons = QGuiApplication.screens(), capture.monitor_rects()
    s = screens[screen_index]
    dpr, g = s.devicePixelRatio(), s.geometry()
    ox, oy = (mons[screen_index][0], mons[screen_index][1]) if screen_index < len(mons) else (int(g.x() * dpr), int(g.y() * dpr))
    return ox + int((r.x() - g.x()) * dpr), oy + int((r.y() - g.y()) * dpr), ox + int((r.right() + 1 - g.x()) * dpr), oy + int((r.bottom() + 1 - g.y()) * dpr)


# ---- overlays ---------------------------------------------------------------------------------------------------------------------
def _overlay_flags() -> Qt.WindowType:
    return (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)


class HighlightOverlay(QWidget):
    """Outlines the element the agent is about to touch (click-through, auto-hides)."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(_overlay_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._label = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_rect(self, phys: Rect, label: str, ms: int) -> None:
        r = physical_to_logical(phys).adjusted(-6, -6, 6, 6)
        self._label = label[:90]
        self.setGeometry(r.adjusted(0, -22, 0, 0))  # room for the label above the box
        self.show()
        self.raise_()
        self.update()
        self._timer.start(max(200, ms + 250))

    def paintEvent(self, _: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = self.rect().adjusted(2, 24, -2, -2)
        p.setPen(QPen(QColor("#ff9e64"), 3))
        p.setBrush(QColor(255, 158, 100, 40))
        p.drawRoundedRect(box, 5, 5)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.setPen(QColor("#1a1b26"))
        w = p.fontMetrics().horizontalAdvance(self._label) + 14
        p.fillRect(QRect(2, 2, min(w, self.width() - 4), 20), QColor("#ff9e64"))
        p.drawText(QRect(9, 2, self.width() - 12, 20), Qt.AlignmentFlag.AlignVCenter, self._label)


class WatchIndicator(QWidget):
    """Always-visible badge while continuous screen watching runs. Click it to stop watching."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.text, self.color = "", "#f7768e"
        self.setFixedHeight(28)

    def set_state(self, state: str, minutes_left: float = 0.0, private: bool = False) -> None:
        if state == "off":
            self.hide()
            return
        self.color = "#f7768e" if state == "on" else "#e0af68"
        note = tr(" · private window skipped", " · bỏ qua cửa sổ riêng tư") if private else ""
        self.text = (tr("Watching the screen ({m:.0f} min left)", "Đang theo dõi màn hình (còn {m:.0f} phút)", m=minutes_left)
                     if state == "on" else tr("Screen watching paused", "Tạm dừng theo dõi màn hình")) + note + "  ✕"
        self.setToolTip(tr("Click to stop watching. Nothing is saved to disk.", "Bấm để dừng theo dõi. Không lưu gì vào đĩa."))
        w = self.fontMetrics().horizontalAdvance(self.text) + 40
        self.setFixedWidth(max(180, w))
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2, scr.top() + 6)
        self.show()
        self.update()

    def paintEvent(self, _: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#24283b"))
        p.setPen(QPen(QColor(self.color), 2))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)
        p.setBrush(QColor(self.color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPoint(16, 14), 5, 5)
        p.setPen(QColor("#c0caf5"))
        p.drawText(QRect(28, 0, self.width() - 32, 28), Qt.AlignmentFlag.AlignVCenter, self.text)

    def mousePressEvent(self, _: QMouseEvent) -> None:
        self.clicked.emit()


class RegionSelector(QWidget):
    """Full-screen dim layer: drag a rectangle, release to capture, Esc to cancel."""

    selected = Signal(tuple)  # physical-pixel rect (l, t, r, b)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self._a: QPoint | None = None
        self._b: QPoint | None = None
        self._screen = 0

    def start(self) -> None:
        screens = QGuiApplication.screens()
        cur = QCursor.pos()
        self._screen = next((i for i, s in enumerate(screens) if s.geometry().contains(cur)), 0)
        self._a = self._b = None
        self.setGeometry(screens[self._screen].geometry())
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _rect(self) -> QRect:
        return QRect(self._a, self._b).normalized() if self._a and self._b else QRect()

    def paintEvent(self, _: object) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        r = self._rect()
        if not r.isNull():
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(r, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor("#7aa2f7"), 2))
            p.drawRect(r)
        p.setPen(QColor("white"))
        p.drawText(self.rect().adjusted(0, 20, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   tr("Drag to select an area · Esc to cancel", "Kéo để chọn vùng · Esc để hủy"))

    def mousePressEvent(self, e: QMouseEvent) -> None:
        self._a = self._b = e.position().toPoint()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        self._b = e.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._b = e.position().toPoint()
        r = self._rect()
        self.hide()
        if r.width() < 8 or r.height() < 8:
            self.cancelled.emit()
            return
        self.selected.emit(logical_to_physical(r.translated(self.geometry().topLeft()), self._screen))

    def keyPressEvent(self, e: Any) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
            self.cancelled.emit()
