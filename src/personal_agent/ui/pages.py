"""Management window: Memory, Documents (RAG), Tools/MCP, Schedules, Remembered approvals, Audit, Usage and Policy pages."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_agent.ui.i18n import tr

Send = Callable[[dict[str, Any]], Any]
Row = dict[str, Any]
Column = tuple[str, str, Callable[[Row], Any]]  # en, vi, getter


def _ts(v: Any) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(v))) if v else ""


class TablePage(QWidget):
    """A list the daemon sends as one event, with a button per row action and optional toolbar buttons."""

    def __init__(self, send: Send, request: Row, event: str, rows_of: Callable[[Row], list[Row]], columns: list[Column],
                 actions: list[tuple[str, str, Callable[[Row], Row]]] | None = None,
                 toolbar: list[tuple[str, str, Row]] | None = None) -> None:
        super().__init__()
        self._send, self.request, self.event_type, self._rows_of, self._cols = send, request, event, rows_of, columns
        self._actions = actions or []
        self.rows: list[Row] = []
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)
        self._toolbar_btns: list[tuple[QPushButton, str, str]] = []
        for en, vi, payload in toolbar or []:
            b = QPushButton()
            b.clicked.connect(lambda _=False, p=payload: self._send(p))
            bar.addWidget(b)
            self._toolbar_btns.append((b, en, vi))
        bar.addStretch(1)
        lay.addLayout(bar)
        self.table = QTableWidget(0, len(columns) + (1 if self._actions else 0))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.table)
        self.retranslate()

    def retranslate(self) -> None:
        self.refresh_btn.setText(tr("Refresh", "Làm mới"))
        for b, en, vi in self._toolbar_btns:
            b.setText(tr(en, vi))
        heads = [tr(en, vi) for en, vi, _ in self._cols] + ([tr("Actions", "Thao tác")] if self._actions else [])
        self.table.setHorizontalHeaderLabels(heads)

    def refresh(self) -> None:
        self._send(self.request)

    def load(self, ev: Row) -> None:
        self.rows = self._rows_of(ev)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            for c, (_, _, get) in enumerate(self._cols):
                it = QTableWidgetItem(str(get(row)))
                it.setToolTip(str(get(row)))
                self.table.setItem(r, c, it)
            if self._actions:
                box = QWidget()
                h = QHBoxLayout(box)
                h.setContentsMargins(2, 0, 2, 0)
                for en, vi, make in self._actions:
                    b = QPushButton(tr(en, vi))
                    b.clicked.connect(lambda _=False, row=row, make=make: self._send(make(row)))
                    h.addWidget(b)
                self.table.setCellWidget(r, len(self._cols), box)


class UsagePage(QWidget):
    def __init__(self, send: Send) -> None:
        super().__init__()
        self._send = send
        lay = QVBoxLayout(self)
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self.refresh)
        self.info = QLabel()
        self.tokens, self.cost = QProgressBar(), QProgressBar()
        self.days, self.models, self.tools = QTableWidget(), QTableWidget(), QTableWidget()
        for w in (self.refresh_btn, self.info, self.tokens, self.cost, self.days, self.models, self.tools):
            lay.addWidget(w)
        self.retranslate()

    @staticmethod
    def _day_heads() -> list[str]:
        return [tr(a, b) for a, b in (
            ("Day", "Ngày"), ("Tokens", "Token"), ("Cost $", "Chi phí $"), ("LLM calls", "Gọi LLM"), ("LLM error rate", "Tỷ lệ lỗi LLM"),
            ("Avg ms", "ms TB"), ("Tool calls", "Gọi công cụ"), ("Tool success", "Công cụ thành công"))]

    def retranslate(self) -> None:
        self.refresh_btn.setText(tr("Refresh", "Làm mới"))
        if self.days.columnCount() == 8:
            self.days.setHorizontalHeaderLabels(self._day_heads())

    def refresh(self) -> None:
        self._send({"type": "usage", "days": 14})

    @staticmethod
    def _fill(t: QTableWidget, heads: list[str], rows: list[list[Any]]) -> None:
        t.setColumnCount(len(heads))
        t.setHorizontalHeaderLabels(heads)
        t.setRowCount(len(rows))
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(str(v)))
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def load(self, ev: Row) -> None:
        today, caps, rep = ev.get("today", {}), ev.get("caps", {}), ev.get("report", {})
        self.info.setText(tr("Today: {tokens:,} tokens, ${cost:.4f}, {calls} calls · Langfuse: {lf}",
                             "Hôm nay: {tokens:,} token, ${cost:.4f}, {calls} lần gọi · Langfuse: {lf}",
                             tokens=today.get("tokens", 0), cost=today.get("cost_usd", 0.0), calls=today.get("calls", 0),
                             lf=tr("on", "bật") if ev.get("langfuse") else tr("off", "tắt")))
        for bar, used, cap, unit in ((self.tokens, today.get("tokens", 0), caps.get("tokens"), "tokens"),
                                     (self.cost, today.get("cost_usd", 0.0), caps.get("cost_usd"), "$")):
            bar.setRange(0, 1000)
            bar.setValue(min(1000, int(1000 * used / cap)) if cap else 0)
            bar.setFormat("-" if not cap else f"${used:,.4f} / ${cap:,.2f}" if unit == "$" else f"{used:,.0f} / {cap:,.0f} {unit}")
        def pct(v: float | None) -> str:
            return "-" if v is None else f"{v:.0%}"

        self._fill(self.days, self._day_heads(),
                   [[d["day"], d["tokens"], f"{d['cost_usd']:.4f}", d["llm_calls"], pct(d["llm_error_rate"]), d["llm_avg_ms"] or "-",
                     d["tool_calls"], pct(d["tool_success_rate"])] for d in rep.get("days", [])])
        self._fill(self.models, [tr("Model", "Mô hình"), tr("Calls", "Số lần"), tr("Errors", "Lỗi"), "ms"],
                   [[m["model"], m["calls"], m["errors"], m["avg_ms"]] for m in rep.get("models", [])])
        self._fill(self.tools, [tr("Tool", "Công cụ"), tr("Calls", "Số lần"), tr("OK", "Thành công"), "ms"],
                   [[t["tool"], t["calls"], t["ok"], t["avg_ms"]] for t in rep.get("tools", [])])


class PolicyPage(QWidget):
    """Raw YAML editor for config/policy.yaml. The daemon validates before saving and keeps a .bak copy."""

    def __init__(self, send: Send) -> None:
        super().__init__()
        self._send = send
        lay = QVBoxLayout(self)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.status = QLabel()
        row = QHBoxLayout()
        self.reload_btn, self.save_btn = QPushButton(), QPushButton()
        self.reload_btn.clicked.connect(self.refresh)
        self.save_btn.clicked.connect(lambda: self._send({"type": "policy_set", "text": self.editor.toPlainText()}))
        row.addWidget(self.reload_btn)
        row.addWidget(self.save_btn)
        row.addStretch(1)
        for w in (self.note, self.editor, self.status):
            lay.addWidget(w)
        lay.addLayout(row)
        self.retranslate()

    def retranslate(self) -> None:
        self.note.setText(tr("Safety policy (tiers, allowed folders, commands, domains). It is validated before saving, the old file is kept as "
                             "policy.yaml.bak, and changes apply immediately. Be careful: this controls what the agent may do.",
                             "Chính sách an toàn (mức rủi ro, thư mục, lệnh, tên miền). Được kiểm tra trước khi lưu, bản cũ giữ ở policy.yaml.bak, "
                             "áp dụng ngay. Hãy cẩn thận: đây là thứ quyết định agent được làm gì."))
        self.reload_btn.setText(tr("Reload", "Tải lại"))
        self.save_btn.setText(tr("Save", "Lưu"))

    def refresh(self) -> None:
        self._send({"type": "policy_get"})

    def load(self, ev: Row) -> None:
        self.editor.setPlainText(str(ev.get("text", "")))
        self.status.setText(tr("Saved.", "Đã lưu.") if ev.get("saved") else "")


class ManagerWindow(QTabWidget):
    def __init__(self, send: Send) -> None:
        super().__init__()
        self.resize(900, 560)
        self.pages: dict[str, tuple[QWidget, str]] = {}
        self.status = QLabel()
        P = TablePage
        mem = P(send, {"type": "memory_list"}, "memory", lambda e: e["items"],
                [("ID", "ID", lambda r: r["id"]), ("Kind", "Loại", lambda r: r["kind"]), ("Status", "Trạng thái",
                 lambda r: r["status"] + (" ⚠" if r.get("sensitive") else "")), ("Text", "Nội dung", lambda r: r["text"])],
                [("Approve", "Duyệt", lambda r: {"type": "memory_update", "id": r["id"], "status": "active"}),
                 ("Delete", "Xóa", lambda r: {"type": "memory_delete", "id": r["id"]})])
        rag = P(send, {"type": "rag_list"}, "rag", lambda e: e["items"],
                [("Source", "Nguồn", lambda r: r["source"]), ("Type", "Loại", lambda r: r["kind"]), ("Chunks", "Đoạn", lambda r: r["chunks"])],
                toolbar=[("Pause watching", "Tạm dừng theo dõi", {"type": "rag_pause"}), ("Resume", "Tiếp tục", {"type": "rag_resume"}),
                         ("Sync folders", "Đồng bộ thư mục", {"type": "rag_sync"})])
        mcp = P(send, {"type": "mcp_list"}, "mcp", lambda e: [{"name": n, **v} for n, v in e["servers"].items()],
                [("Server", "Máy chủ", lambda r: r["name"]), ("Enabled", "Bật", lambda r: r.get("enabled", "")), ("Status", "Trạng thái", lambda r: r.get("status", ""))],
                [("Enable", "Bật", lambda r: {"type": "mcp_set", "name": r["name"], "enabled": True}),
                 ("Disable", "Tắt", lambda r: {"type": "mcp_set", "name": r["name"], "enabled": False})],
                [("Reload MCP", "Tải lại MCP", {"type": "mcp_reload"})])
        sch = P(send, {"type": "schedules"}, "schedules", lambda e: e["items"],
                [("ID", "ID", lambda r: r["id"]), ("Enabled", "Bật", lambda r: r["enabled"]), ("Every (s)", "Mỗi (giây)", lambda r: r["every_s"]),
                 ("Last status", "Kết quả gần nhất", lambda r: r["last_status"]), ("Task", "Tác vụ", lambda r: r["prompt"])],
                [("Cancel", "Hủy", lambda r: {"type": "schedule_cancel", "id": r["id"]})])
        rules = P(send, {"type": "rules"}, "rules", lambda e: e["items"],
                  [("ID", "ID", lambda r: r["id"]), ("Tool", "Công cụ", lambda r: r["tool"]), ("Scope", "Phạm vi", lambda r: r["scope"])],
                  [("Forget", "Quên", lambda r: {"type": "forget_rule", "id": r["id"]})])
        audit = P(send, {"type": "audit", "limit": 200}, "audit", lambda e: e["items"],
                  [("Time", "Thời gian", lambda r: _ts(r["ts"])), ("Tool", "Công cụ", lambda r: r["tool"]), ("Tier", "Mức", lambda r: r["tier"]),
                   ("Decision", "Quyết định", lambda r: r["decision"]), ("Result", "Kết quả", lambda r: r["result"])])
        self.usage, self.policy = UsagePage(send), PolicyPage(send)
        specs: list[tuple[str, str, str, QWidget]] = [
            ("memory", "Memory", "Bộ nhớ", mem), ("rag", "Documents", "Tài liệu", rag), ("mcp", "Tools / MCP", "Công cụ / MCP", mcp),
            ("schedules", "Schedules", "Lịch chạy", sch), ("rules", "Remembered approvals", "Quy tắc đã nhớ", rules),
            ("audit", "Audit log", "Nhật ký kiểm toán", audit), ("usage", "Usage", "Mức dùng", self.usage), ("policy", "Policy", "Chính sách", self.policy)]
        self._tabs: list[tuple[QWidget, str, str]] = []
        for key, en, vi, w in specs:
            self.addTab(w, en)
            self.pages[key] = (w, en)
            self._tabs.append((w, en, vi))
        # (event type) -> page key; mutations re-request the list they changed
        self._route = {"memory": "memory", "rag": "rag", "mcp": "mcp", "schedules": "schedules", "rules": "rules", "audit": "audit",
                       "usage": "usage", "policy": "policy"}
        self._after = {"memory_updated": "memory", "memory_deleted": "memory", "mcp_set": "mcp", "schedule_cancel": "schedules", "rag_synced": "rag"}
        self.currentChanged.connect(lambda _i: self._refresh_current())
        self.setCornerWidget(self.status)
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("Manager", "Quản lý"))
        for i, (w, en, vi) in enumerate(self._tabs):
            self.setTabText(i, tr(en, vi))
            if hasattr(w, "retranslate"):
                w.retranslate()

    def _refresh_current(self) -> None:
        w = self.currentWidget()
        if hasattr(w, "refresh"):
            w.refresh()

    def open_page(self, key: str) -> None:
        self.setCurrentWidget(self.pages[key][0])
        self._refresh_current()
        self.show()
        self.raise_()

    def on_event(self, ev: Row) -> bool:
        """Feed a daemon event; returns True if a page consumed it."""
        t = ev.get("type", "")
        if t in self._route:
            w = self.pages[self._route[t]][0]
            w.load(ev)  # type: ignore[attr-defined]
            self.status.setText("")
            return True
        if t in self._after:
            w = self.pages[self._after[t]][0]
            w.refresh()  # type: ignore[attr-defined]
            return True
        if t == "error" and self.isVisible():
            self.status.setText(f"⚠ {ev.get('message', '')}")
            self.status.setAlignment(Qt.AlignmentFlag.AlignRight)
        return False
