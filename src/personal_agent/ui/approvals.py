"""Approval center: every pending action as a card (what, why, risk, editable arguments, diff preview), decided per thread."""

from __future__ import annotations

import json
import time
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from personal_agent.ui.decisions import build_decisions
from personal_agent.ui.i18n import tr
from personal_agent.ui.model import Session

RISK_COLOR = {"low": "#9ece6a", "medium": "#e0af68", "high": "#ff9e64", "critical": "#f7768e"}


class DiffHighlighter(QSyntaxHighlighter):
    """Colours unified-diff lines: + green, - red, @@ blue; file headers bold."""

    def __init__(self, doc: QTextDocument) -> None:
        super().__init__(doc)
        def fmt(color: str, bold: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            return f
        self._add, self._del, self._hunk, self._head = fmt("#9ece6a"), fmt("#f7768e"), fmt("#7aa2f7"), fmt("#c0caf5", True)

    def highlightBlock(self, text: str) -> None:
        if text.startswith(("+++", "---")):
            self.setFormat(0, len(text), self._head)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self._hunk)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self._add)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self._del)


def _wrapped(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    return lbl


class ApprovalCard(QGroupBox):
    def __init__(self, a: dict[str, Any]) -> None:
        super().__init__()
        self.a = a
        lay = QVBoxLayout(self)
        risk = str(a.get("risk", ""))
        head = QLabel(f"<b>{a['tool']}</b> &nbsp; <span style='color:{RISK_COLOR.get(risk, '#c0caf5')}'>"
                      f"{a.get('tier', '')} · {tr('risk', 'rủi ro')} {risk}</span>")
        head.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(head)
        why = QLabel(str(a.get("reason", "")))
        why.setWordWrap(True)
        lay.addWidget(why)
        if a.get("tainted"):
            warn = QLabel("⚠ " + tr("This action follows untrusted content (web page, file or tool output). Check it carefully.",
                                    "Hành động này đến sau nội dung không đáng tin (web, tệp, kết quả công cụ). Hãy kiểm tra kỹ."))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#ff9e64")
            lay.addWidget(warn)
        exp = a.get("expires_at")
        if exp:
            left = max(0, int(float(exp) - time.time()))
            lay.addWidget(_wrapped(tr("Expires in {m}:{s:02d} (an unanswered request counts as a rejection)",
                                      "Hết hạn sau {m}:{s:02d} (không trả lời được tính là từ chối)", m=left // 60, s=left % 60)))
        lay.addWidget(_wrapped(tr("Arguments (editable; changing them approves the edited call)", "Tham số (sửa được; sửa nghĩa là duyệt bản đã sửa)")))
        self.args = QPlainTextEdit(json.dumps(a["args"], ensure_ascii=False, indent=2))
        self.args.setMaximumHeight(110)
        lay.addWidget(self.args)
        lay.addWidget(QLabel(tr("Preview / diff", "Xem trước / khác biệt")))
        self.preview = QPlainTextEdit(str(a.get("preview", "")))
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 9))
        self.preview.setMinimumHeight(110)
        DiffHighlighter(self.preview.document())
        lay.addWidget(self.preview)
        self.group = QButtonGroup(self)
        self.radios: dict[str, QRadioButton] = {}
        for key, label in (("approve", tr("Approve", "Duyệt")), ("remember", tr("Approve and remember for this scope", "Duyệt và nhớ cho phạm vi này")),
                           ("reject", tr("Reject", "Từ chối"))):
            r = QRadioButton(label)
            self.group.addButton(r)
            self.radios[key] = r
            lay.addWidget(r)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText(tr("Reason for rejecting (the agent will see it)", "Lý do từ chối (agent sẽ thấy)"))
        lay.addWidget(self.reason)
        self.confirm = QLineEdit()
        self.confirm.setPlaceholderText(tr("Type CONFIRM to approve a dangerous action", "Gõ CONFIRM để duyệt hành động nguy hiểm"))
        self.confirm.setVisible(a.get("tier") == "T3")
        lay.addWidget(self.confirm)

    def state(self) -> dict[str, Any]:
        choice = next((k for k, r in self.radios.items() if r.isChecked()), None)
        return {"call_id": self.a["call_id"], "tool": self.a["tool"], "tier": self.a.get("tier"), "choice": choice,
                "args_text": self.args.toPlainText(), "args": self.a["args"], "reason": self.reason.text(),
                "confirm": self.confirm.text()}


class ThreadBox(QFrame):
    decided = Signal(str, dict)

    def __init__(self, thread_id: str, items: list[dict[str, Any]]) -> None:
        super().__init__()
        self.thread_id = thread_id
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{tr('Thread', 'Luồng')}: {thread_id}</b>"))
        self.cards = [ApprovalCard(a) for a in items]
        for c in self.cards:
            lay.addWidget(c)
        self.error = QLabel()
        self.error.setStyleSheet("color:#f7768e")
        lay.addWidget(self.error)
        self.submit = QPushButton(tr("Send decisions", "Gửi quyết định"))
        self.submit.clicked.connect(self._submit)
        lay.addWidget(self.submit)

    def _submit(self) -> None:
        try:
            decisions = build_decisions([c.state() for c in self.cards])
        except ValueError as e:
            self.error.setText(str(e))
            return
        self.error.clear()
        self.submit.setEnabled(False)  # one answer per request; a fresh list arrives from the daemon
        self.decided.emit(self.thread_id, decisions)


class ApprovalCenter(QScrollArea):
    decided = Signal(str, dict)

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # cards wrap; never scroll sideways
        self.resize(720, 760)
        self._shown: tuple[str, ...] = ()
        self.refresh()

    def refresh(self) -> None:
        """Rebuild only when the set of pending calls changed, so half-filled answers are not wiped by unrelated events."""
        key = tuple(sorted(self.session.approvals))
        if key == self._shown:
            return
        self._shown = key
        body = QWidget()
        lay = QVBoxLayout(body)
        by = self.session.pending_for_thread()
        if not by:
            lay.addWidget(QLabel(tr("Nothing is waiting for your approval.", "Không có gì đang chờ bạn duyệt.")))
        for tid, items in by.items():
            box = ThreadBox(tid, items)
            box.decided.connect(self.decided)
            lay.addWidget(box)
        lay.addStretch(1)
        self.setWidget(body)
        self.setWindowTitle(tr("Approval center ({n})", "Trung tâm phê duyệt ({n})", n=len(self.session.approvals)))
