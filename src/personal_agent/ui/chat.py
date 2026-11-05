"""Chat window (markdown, attachments, pasted images, microphone), the Plan/Progress panel and the avatar's quick-input box."""

from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from personal_agent.ui import attach
from personal_agent.ui.i18n import tr
from personal_agent.ui.model import Entry, Session

IDLE_CLOSE_MS = 5000  # the quick box closes itself after this long if nothing is typed
MARK = {"todo": "☐", "done": "✔", "failed": "✖", "blocked": "–"}


class Composer(QPlainTextEdit):
    """Enter sends, Shift+Enter inserts a newline, pasted images become attachments."""

    send = Signal()
    image_pasted = Signal(QImage)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(e)

    def insertFromMimeData(self, source: QMimeData) -> None:
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.image_pasted.emit(img)
                return
        super().insertFromMimeData(source)


def render_markdown(session: Session) -> str:
    """The whole transcript as one markdown document (QTextBrowser renders it)."""
    parts: list[str] = []
    for e in session.transcript:
        if e.kind == "user":
            files = "".join(f"\n\n[{n}]" for n in e.meta.get("attachments", []))
            parts.append(f"**{tr('You', 'Bạn')}**\n\n{e.text}{files}")
        elif e.kind == "agent":
            u = e.meta.get("used") or {}
            foot = f"\n\n<sub>{u.get('steps')} steps · {u.get('tokens')} tokens · ${float(u.get('cost') or 0):.4f}</sub>" if u else ""
            parts.append(f"**{session.agent_name}**\n\n{e.text}{foot}")
        elif e.kind == "error":
            parts.append(f"**{e.text}**")
        else:
            parts.append(f"> _{e.text.replace(chr(10), ' ')}_")
    return "\n\n".join(parts)


class PlanPanel(QWidget):
    """Goal, success criteria, steps with status and budget meters."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setProperty("role", "title")
        self.objective = QLabel()
        self.objective.setWordWrap(True)
        self.criteria = QListWidget()
        self.steps = QListWidget()
        self.task_used = QLabel()
        self.tokens = QProgressBar()
        self.cost = QProgressBar()
        for w in (self.tokens, self.cost):
            w.setTextVisible(True)
        self.labels = [QLabel(), QLabel(), QLabel()]
        lay.addWidget(self.title)
        lay.addWidget(self.objective)
        lay.addWidget(self.labels[0])
        lay.addWidget(self.criteria, 1)
        lay.addWidget(self.labels[1])
        lay.addWidget(self.steps, 2)
        lay.addWidget(self.task_used)
        lay.addWidget(self.labels[2])
        lay.addWidget(self.tokens)
        lay.addWidget(self.cost)
        self.retranslate()

    def retranslate(self) -> None:
        self.title.setText(tr("Plan and progress", "Kế hoạch và tiến độ"))
        self.labels[0].setText(tr("Success criteria", "Tiêu chí thành công"))
        self.labels[1].setText(tr("Steps", "Các bước"))
        self.labels[2].setText(tr("Today's budget", "Ngân sách hôm nay"))

    def show_session(self, s: Session) -> None:
        self.objective.setText(s.plan["objective"] if s.plan else tr("(no plan: a simple question needs none)", "(chưa có kế hoạch)"))
        self.criteria.clear()
        self.criteria.addItems([f"• {c}" for c in (s.plan or {}).get("criteria", [])])
        self.steps.clear()
        self.steps.addItems([f"{MARK.get(x['status'], '☐')} [{x['role']}] {x['text']}" for x in s.steps])
        u = s.used
        self.task_used.setText(tr("This task: {steps} steps, {tools} tool calls, {tokens} tokens, ${cost:.4f}",
                                  "Tác vụ này: {steps} bước, {tools} lần gọi công cụ, {tokens} token, ${cost:.4f}",
                                  steps=u.get("steps") or 0, tools=u.get("tool_calls") or 0, tokens=u.get("tokens") or 0,
                                  cost=float(u.get("cost") or 0)) if u else "")
        today, caps = (s.usage.get("today") or {}), (s.usage.get("caps") or {})
        self._meter(self.tokens, today.get("tokens", 0), caps.get("tokens"), "tokens")
        self._meter(self.cost, today.get("cost_usd", 0.0), caps.get("cost_usd"), "$")

    @staticmethod
    def _meter(bar: QProgressBar, used: float, cap: float | None, unit: str) -> None:
        if not cap:
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat("-")
            return
        bar.setRange(0, 1000)
        bar.setValue(min(1000, int(1000 * used / cap)))
        bar.setFormat(f"${used:,.4f} / ${cap:,.2f}" if unit == "$" else f"{used:,.0f} / {cap:,.0f} {unit}")


class ChatWindow(QWidget):
    """The chat page: transcript, composer and the plan panel. It lives inside the main window, which owns the sidebar."""

    submitted = Signal(str, list)  # text, attachments
    record_toggled = Signal(bool)
    stop_requested = Signal()
    quit_requested = Signal()
    resume_requested = Signal()

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self._files: list[dict[str, Any]] = []
        self._note = ""  # a message about the last attachment (too big, too many) that must stay visible
        self._recording = False
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.composer = Composer()
        self.composer.setFixedHeight(72)
        self.composer.send.connect(self._send)
        self.composer.image_pasted.connect(self._paste_image)
        self.chips = QLabel()
        self.chips.setProperty("role", "chip")
        self.chips.setTextFormat(Qt.TextFormat.RichText)
        self.chips.linkActivated.connect(self._remove_file)
        self.status = QLabel()
        self.attach_btn, self.mic_btn, self.send_btn, self.stop_btn, self.quit_btn = (QPushButton() for _ in range(5))
        self.quit_btn.clicked.connect(self.quit_requested)
        QShortcut(QKeySequence("Ctrl+Q"), self, self.quit_requested.emit)
        self.attach_btn.clicked.connect(self._pick_files)
        self.mic_btn.setCheckable(True)
        self.mic_btn.toggled.connect(self._mic)
        self.send_btn.setProperty("role", "primary")
        self.send_btn.clicked.connect(self._send)
        self.stop_btn.setProperty("role", "danger")
        self.stop_btn.clicked.connect(self.stop_requested)
        row = QHBoxLayout()
        for b in (self.attach_btn, self.mic_btn, self.stop_btn, self.send_btn, self.quit_btn):
            row.addWidget(b)
        row.insertWidget(3, self.status, 1)
        self.paused = QFrame()  # shown while the kill switch is engaged: the agent refuses every request until resumed
        self.paused.setObjectName("Paused")
        pl = QHBoxLayout(self.paused)
        self.paused_text, self.resume_btn = QLabel(), QPushButton()
        self.paused_text.setWordWrap(True)
        self.resume_btn.clicked.connect(self.resume_requested)
        pl.addWidget(self.paused_text, 1)
        pl.addWidget(self.resume_btn)
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.addWidget(self.paused)
        lay.addWidget(self.browser, 1)
        lay.addWidget(self.chips)
        lay.addWidget(self.composer)
        lay.addLayout(row)
        self.plan = PlanPanel()
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.plan)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)
        self.setAcceptDrops(True)
        self.retranslate()
        self.refresh()

    def retranslate(self) -> None:
        self.attach_btn.setText(tr("Attach", "Đính kèm"))
        self.mic_btn.setText(tr("Talk", "Nói"))
        self.stop_btn.setText(tr("Stop all", "Dừng tất cả"))
        self.send_btn.setText(tr("Send", "Gửi"))
        self.quit_btn.setText(tr("Quit", "Thoát"))
        self.paused_text.setText(tr("The agent is paused (kill switch). It will refuse every request until you resume it.",
                                    "Agent đang tạm dừng (công tắc khẩn). Agent sẽ từ chối mọi yêu cầu cho tới khi bạn cho chạy lại."))
        self.resume_btn.setText(tr("Resume agent", "Cho agent chạy lại"))
        self.quit_btn.setToolTip(tr("Close the app (Ctrl+Q). The background agent keeps running.", "Đóng ứng dụng (Ctrl+Q). Agent nền vẫn chạy."))
        self.composer.setPlaceholderText(tr("Message… (Enter sends, Shift+Enter = new line; paste or drop images and files)",
                                            "Tin nhắn… (Enter để gửi, Shift+Enter xuống dòng; dán hoặc kéo thả ảnh, tệp)"))
        self.plan.retranslate()

    # ---- rendering ------------------------------------------------------------------------------------------------
    def refresh(self, changed: set[str] | None = None) -> None:
        ch = changed if changed is not None else {"transcript", "plan", "busy", "name"}
        if "name" in ch:
            self.retranslate()
        if "transcript" in ch:
            self.browser.setMarkdown(render_markdown(self.session))
            sb = self.browser.verticalScrollBar()
            sb.setValue(sb.maximum())
        if ch & {"plan", "usage", "transcript"}:
            self.plan.show_session(self.session)
        if ch & {"killed", "name"} or changed is None:
            self.paused.setVisible(self.session.killed)
        if ch & {"busy", "approvals"}:
            n = len(self.session.approvals)
            self.status.setText(self._note or (tr("Waiting for your approval ({n})", "Đang chờ bạn duyệt ({n})", n=n) if n
                                               else tr("Working…", "Đang xử lý…") if self.session.busy else ""))
            self.send_btn.setEnabled(not n)
        self.chips.setText("  ".join(f"{html.escape(f['name'])} <a href=\"rm:{i}\" style=\"text-decoration:none\">✕</a>"
                                     for i, f in enumerate(self._files)))

    # ---- user input -------------------------------------------------------------------------------------------------
    def _room_for(self, a: dict[str, Any]) -> bool:
        used = sum(len(f["data"]) * 3 // 4 for f in self._files)
        if len(self._files) >= 4 or used + len(a["data"]) * 3 // 4 > attach.MAX_TOTAL:
            self._note = tr("At most 4 files and 15 MB per message.", "Tối đa 4 tệp và 15 MB mỗi tin nhắn.")
            return False
        return True

    def add_files(self, paths: list[str]) -> None:
        for p in paths:
            try:
                a = attach.from_path(p)
            except (ValueError, OSError) as e:
                self._note = str(e)
                continue
            if self._room_for(a):
                self._files.append(a)
        self.refresh({"busy"})

    def _remove_file(self, link: str) -> None:
        i = int(link.removeprefix("rm:"))
        if 0 <= i < len(self._files):
            del self._files[i]
            self._note = ""
        self.refresh({"busy"})

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Attach files", "Đính kèm tệp"))
        self.add_files(paths)

    def _paste_image(self, img: QImage) -> None:
        try:
            a = attach.from_image(img, f"pasted-{len(self._files) + 1}.png")
        except ValueError as e:
            self._note = str(e)
        else:
            if self._room_for(a):
                self._files.append(a)
        self.refresh({"busy"})

    def _mic(self, on: bool) -> None:
        self._recording = on
        self.mic_btn.setText(tr("Stop", "Dừng") if on else tr("Talk", "Nói"))
        self.record_toggled.emit(on)

    def add_attachment(self, a: dict[str, Any]) -> None:
        """A finished recording arrives here; it is sent with the next message (or on its own)."""
        self._files.append(a)
        self.refresh({"busy"})

    def _send(self) -> None:
        text = self.composer.toPlainText().strip()
        if (not text and not self._files) or self.session.approvals:
            return
        files, self._files, self._note = self._files, [], ""
        self.composer.clear()
        self.submitted.emit(text, files)
        self.refresh({"busy"})

    def dragEnterEvent(self, e: Any) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: Any) -> None:
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])


class QuickInput(QWidget):
    """Small always-on-top box opened by clicking the avatar."""

    submitted = Signal(str, list)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # only the text box is visible
        self._files: list[dict[str, Any]] = []
        self._idle = QTimer(self)  # closes the box when nothing was typed
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_CLOSE_MS)
        self._idle.timeout.connect(self.hide)
        self.edit = Composer()
        self.edit.setFixedHeight(56)
        self.edit.send.connect(self._send)
        self.edit.textChanged.connect(self._idle.stop)
        self.hint = QLabel()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.hint)
        lay.addWidget(self.edit)
        self.setObjectName("QuickInput")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedWidth(320)
        self.retranslate()

    def retranslate(self) -> None:
        self.edit.setPlaceholderText(tr("Ask anything… (Enter to send, Esc to close)", "Hỏi gì cũng được… (Enter gửi, Esc đóng)"))

    def open_at(self, near: QWidget, files: list[str] | None = None) -> None:
        for p in files or []:
            try:
                self._files.append(attach.from_path(p))
            except (ValueError, OSError) as e:
                self.hint.setText(str(e))
        if self._files:
            self.hint.setText("  ".join(f"{f['name']}" for f in self._files))
        self.hint.setVisible(bool(self._files) or bool(self.hint.text()))
        self.adjustSize()
        self.move(near.x() - self.width() + near.width(), near.y() - self.height() - 6 if near.y() > self.height() + 10 else near.y() + near.height() + 6)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()
        if self.edit.toPlainText() or self._files:
            self._idle.stop()
        else:
            self._idle.start()

    def close_if_empty(self) -> None:
        """Hide the box unless the user has started something (typed text or attached a file)."""
        if self.isVisible() and not self.edit.toPlainText().strip() and not self._files:
            self.hide()

    def hideEvent(self, e: Any) -> None:
        self._idle.stop()
        super().hideEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def _send(self) -> None:
        text = self.edit.toPlainText().strip()
        if not text and not self._files:
            return
        files, self._files = self._files, []
        self.edit.clear()
        self.hint.clear()
        self.hide()
        self.submitted.emit(text, files)


def entry_text(e: Entry) -> str:  # small helper for notifications
    return e.text[:120]
