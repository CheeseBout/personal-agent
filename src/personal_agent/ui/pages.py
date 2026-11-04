"""Management window: Memory, Documents (RAG), Tools/MCP, Schedules, Remembered approvals, Audit, Usage and Policy pages."""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from personal_agent.rag.loaders import SUPPORTED
from personal_agent.ui.i18n import tr
from personal_agent.ui.nav import NavRail, top_bar

Send = Callable[[dict[str, Any]], Any]
Row = dict[str, Any]
Column = tuple[str, str, Callable[[Row], Any]]  # en, vi, getter


def _ts(v: Any) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(v))) if v else ""


def _confirm(title: tuple[str, str], text: tuple[str, str]) -> bool:
    yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
    return bool(QMessageBox.question(None, tr(title[0], title[1]), tr(text[0], text[1]), yes | no, no) == yes)


def edit_memory_action(row: Row) -> Row | None:
    """Ask for the new text of a memory; None (send nothing) when cancelled or unchanged. The daemon re-checks length and sensitivity."""
    text, ok = QInputDialog.getMultiLineText(None, tr("Edit memory", "Sửa bộ nhớ"), tr("Text", "Nội dung"), str(row["text"]))
    text = text.strip()
    if not ok or not text or text == row["text"]:
        return None
    if len(text) > 2000:
        QMessageBox.warning(None, tr("Edit memory", "Sửa bộ nhớ"), tr("At most 2000 characters.", "Tối đa 2000 ký tự."))
        return None
    return {"type": "memory_update", "id": row["id"], "text": text}


def approve_memory_action(row: Row) -> Row | None:
    """Approving activates the memory. A sensitive one (password, key, personal data) needs an explicit second yes."""
    if row.get("sensitive") and not _confirm(("Approve memory", "Duyệt bộ nhớ"), (
            "This memory looks sensitive (password, key, personal data). Approving lets the agent recall and use it. Approve anyway?",
            "Mục này có vẻ nhạy cảm (mật khẩu, khóa, dữ liệu cá nhân). Duyệt sẽ cho agent nhớ và dùng nó. Vẫn duyệt?")):
        return None
    return {"type": "memory_update", "id": row["id"], "status": "active"}


def delete_memory_action(row: Row) -> Row | None:
    snippet = str(row["text"])[:200]
    if not _confirm(("Delete memory", "Xóa bộ nhớ"), ("Delete this memory permanently?\n\n" + snippet, "Xóa vĩnh viễn mục này?\n\n" + snippet)):
        return None
    return {"type": "memory_delete", "id": row["id"]}


def remove_doc_action(row: Row) -> Row | None:
    if not _confirm(("Remove from index", "Xóa khỏi chỉ mục"), (
            f"Remove this document from the index?\nThe file itself is not deleted.\n\n{row['source']}",
            f"Xóa tài liệu này khỏi chỉ mục?\nTệp gốc không bị xóa.\n\n{row['source']}")):
        return None
    return {"type": "rag_remove", "source": row["source"]}


def rag_summary(report: list[str]) -> str:
    """One short line for a sync / add report: how many files were indexed, unchanged, removed or failed."""
    if not report:
        return tr("No supported files found.", "Không tìm thấy tệp được hỗ trợ.")
    n = {k: sum(1 for ln in report if ln.startswith(k)) for k in ("ingested", "updated", "unchanged", "removed", "failed")}
    done = n["ingested"] + n["updated"]
    out = ", ".join([tr(f"{done} indexed", f"{done} đã lập chỉ mục"), tr(f"{n['unchanged']} unchanged", f"{n['unchanged']} không đổi"),
                     tr(f"{n['removed']} removed", f"{n['removed']} đã gỡ")])
    if n["failed"]:
        first = next(ln for ln in report if ln.startswith("failed"))
        out = f"⚠ {tr(str(n['failed']) + ' failed', str(n['failed']) + ' lỗi')} ({first[:80]}); {out}"
    return out


_rids = itertools.count(1)  # request ids: the daemon echoes them, so replies can be matched and stale ones dropped
REPLY_TIMEOUT_MS = 15_000


def _key(v: Any) -> tuple[int, Any]:
    return (0, float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else (1, str(v).casefold())


class TablePage(QWidget):
    """A list the daemon sends as one event, with a button per row action, optional toolbar buttons, search, a facet filter and
    click-to-sort headers. A row whose action is in flight stays locked until the daemon answers that request (or the list is rebuilt)."""

    def __init__(self, send: Send, request: Row, event: str, rows_of: Callable[[Row], list[Row]], columns: list[Column],
                 actions: list[tuple[str, str, Callable[[Row], Row | None]]] | None = None,
                 toolbar: list[tuple[str, str, Row]] | None = None, *, wide: int | None = None, searchable: bool = False,
                 facet: Column | None = None, empty: tuple[str, str] = ("Nothing here yet.", "Chưa có mục nào.")) -> None:
        super().__init__()
        self._send, self.request, self.event_type, self._rows_of, self._cols = send, request, event, rows_of, columns
        self._actions, self._facet, self._empty = actions or [], facet, empty
        self.note: Callable[[str], None] = lambda _t: None  # the window shows short messages
        self.rows: list[Row] = []  # everything the daemon sent
        self._view: list[Row] = []  # what is shown: filtered and sorted
        self._sort: tuple[int, bool] | None = None
        self._latest = 0  # id of the newest list request or action; older list replies are dropped
        self._pending: dict[int, tuple[Row, int]] = {}  # request id -> (row, index of the action) still waiting for the daemon
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
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setVisible(searchable)
        self.search.textChanged.connect(lambda _t: self._render())
        self.facet_box = QComboBox()
        self.facet_box.setVisible(facet is not None)
        self.facet_box.currentIndexChanged.connect(lambda _i: self._render())
        bar.addWidget(self.search, 1)
        bar.addWidget(self.facet_box)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.table = QTableWidget(0, len(columns) + (1 if self._actions else 0))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setWordWrap(True)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if wide is None:
            head.setStretchLastSection(True)
        else:
            head.setSectionResizeMode(wide, QHeaderView.ResizeMode.Stretch)
        head.setSortIndicatorShown(True)
        head.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        head.sectionClicked.connect(self._sort_by)
        head.sectionResized.connect(lambda *_a: self.table.resizeRowsToContents())  # wrapped text needs the final column width
        lay.addWidget(self.table)
        self.empty_label = QLabel()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty_label)
        self.retranslate()
        self._render()

    def retranslate(self) -> None:
        self.refresh_btn.setText(tr("Refresh", "Làm mới"))
        for b, en, vi in self._toolbar_btns:
            b.setText(tr(en, vi))
        heads = [tr(en, vi) for en, vi, _ in self._cols] + ([tr("Actions", "Thao tác")] if self._actions else [])
        self.table.setHorizontalHeaderLabels(heads)
        self.search.setPlaceholderText(tr("Search...", "Tìm kiếm..."))
        if self._facet is not None and self.facet_box.count():
            self.facet_box.setItemText(0, tr("All", "Tất cả"))
        self._show_empty()

    def refresh(self) -> None:
        rid = self._latest = next(_rids)
        self._send({**self.request, "rid": rid})

    def settle(self, rid: Any) -> bool:
        """The daemon refused or failed request `rid`: unlock that row. True if it was ours."""
        if self._pending.pop(rid, None) is None:
            return False
        self._render()
        return True

    def _timeout(self, rid: int) -> None:
        # No answer: the outcome is unknown, so keep the row locked and ask the daemon for the truth instead of letting a second click repeat it.
        if rid in self._pending:
            self.note("⚠ " + tr("No answer from the agent yet; refreshing the list.", "Agent chưa trả lời; đang làm mới danh sách."))
            self.refresh()

    def _act(self, row: Row, idx: int) -> None:
        if not (msg := self._actions[idx][2](row)):
            return
        rid = self._latest = next(_rids)  # also supersedes any list request still in flight
        self._pending[rid] = (row, idx)
        self._render()  # locks the row (and survives later filter / sort re-renders)
        QTimer.singleShot(REPLY_TIMEOUT_MS, lambda: self._timeout(rid))
        self._send({**msg, "rid": rid})

    def load(self, ev: Row) -> bool:
        if isinstance(rid := ev.get("rid"), int) and rid < self._latest:
            return False  # an older list that crossed a newer request: showing it could bring back a deleted row
        self.rows = self._rows_of(ev)
        self._pending.clear()  # a list requested after every action is the daemon's truth
        if self._facet is not None:
            keep = self.facet_box.currentData() or ""
            vals = sorted({str(self._facet[2](r)) for r in self.rows})
            self.facet_box.blockSignals(True)
            self.facet_box.clear()
            self.facet_box.addItem(tr("All", "Tất cả"), "")
            for v in vals:
                self.facet_box.addItem(v, v)
            self.facet_box.setCurrentIndex(max(0, self.facet_box.findData(keep)))
            self.facet_box.blockSignals(False)
        self._render()
        return True

    def _sort_by(self, col: int) -> None:
        if col >= len(self._cols):
            return
        self._sort = (col, not self._sort[1] if self._sort and self._sort[0] == col else False)
        self.table.horizontalHeader().setSortIndicator(col, Qt.SortOrder.DescendingOrder if self._sort[1] else Qt.SortOrder.AscendingOrder)
        self._render()

    def _show_empty(self) -> None:
        self.empty_label.setText(tr("No matches.", "Không có kết quả phù hợp.") if self.rows else tr(self._empty[0], self._empty[1]))
        self.empty_label.setVisible(not self._view)

    def _render(self) -> None:
        view = self.rows
        if self._facet is not None and (want := self.facet_box.currentData()):
            view = [r for r in view if str(self._facet[2](r)) == want]
        if needle := self.search.text().strip().casefold():
            view = [r for r in view if any(needle in str(get(r)).casefold() for _, _, get in self._cols)]
        if self._sort:
            get = self._cols[self._sort[0]][2]
            view = sorted(view, key=lambda r: _key(get(r)), reverse=self._sort[1])
        self._view = list(view)
        self.table.setRowCount(len(self._view))
        for r, row in enumerate(self._view):
            for c, (_, _, get) in enumerate(self._cols):
                it = QTableWidgetItem(str(get(row)))
                it.setToolTip(str(get(row)))
                self.table.setItem(r, c, it)
            if self._actions:
                box = QWidget()
                h = QHBoxLayout(box)
                h.setContentsMargins(2, 0, 2, 0)
                busy = next((i for pr, i in self._pending.values() if pr is row), None)
                for i, (en, vi, _make) in enumerate(self._actions):
                    if (applies := getattr(_make, "applies", None)) and not applies(row):
                        continue  # e.g. no "Enable" on a server that is already enabled
                    b = QPushButton(tr("Working…", "Đang xử lý…") if busy == i else tr(en, vi))
                    b.setToolTip(f"{tr(en, vi)}: {self._cols[0][2](row)}")
                    b.setAccessibleName(b.toolTip())
                    b.setEnabled(busy is None)
                    b.clicked.connect(lambda _=False, row=row, i=i: self._act(row, i))
                    h.addWidget(b)
                self.table.setCellWidget(r, len(self._cols), box)
        self.table.resizeRowsToContents()
        self.table.setVisible(bool(self._view))
        self._show_empty()


class DocumentsPage(TablePage):
    """Indexed documents (RAG): add a file or folder, remove an entry, sync the watched folders, pause / resume watching, and try a search.
    Indexing jobs run on the daemon and can take a while, so their buttons stay locked until the daemon answers."""

    JOB_LIMIT_MS = 600_000  # unlock anyway if the daemon never answers (e.g. the connection dropped)

    def __init__(self, send: Send) -> None:
        self._job: int | None = None  # request id of the running add / sync
        self._search_rid: int | None = None
        self.watching = self.paused = False
        super().__init__(send, {"type": "rag_list"}, "rag", lambda e: e["items"],
                         [("Source", "Nguồn", lambda r: r["source"]), ("Type", "Loại", lambda r: r["kind"]),
                          ("Chunks", "Đoạn", lambda r: r["chunks"]), ("Updated", "Cập nhật", lambda r: _ts(r.get("updated")))],
                         [("Remove from index", "Xóa khỏi chỉ mục", remove_doc_action)], wide=0, searchable=True,
                         facet=("Type", "Loại", lambda r: r["kind"]),
                         empty=("No documents indexed yet. Use Add file / Add folder, or list folders under rag.watch in the config.",
                                "Chưa có tài liệu nào. Dùng Thêm tệp / Thêm thư mục, hoặc khai báo thư mục trong rag.watch ở cấu hình."))
        lay: QVBoxLayout = self.layout()  # type: ignore[assignment]
        row = QHBoxLayout()
        self.add_file, self.add_folder, self.sync_btn, self.pause_btn = QPushButton(), QPushButton(), QPushButton(), QPushButton()
        self.add_file.clicked.connect(self._pick_file)
        self.add_folder.clicked.connect(self._pick_folder)
        self.sync_btn.clicked.connect(self._sync)
        self.pause_btn.clicked.connect(lambda: self._send({"type": "rag_resume" if self.paused else "rag_pause"}))
        for b in (self.add_file, self.add_folder, self.sync_btn, self.pause_btn):
            row.addWidget(b)
        self.state = QLabel()
        row.addWidget(self.state, 1)
        lay.insertLayout(1, row)
        srow = QHBoxLayout()
        self.query = QLineEdit()
        self.query.returnPressed.connect(self._search)
        self.search_btn = QPushButton()
        self.search_btn.clicked.connect(self._search)
        srow.addWidget(self.query, 1)
        srow.addWidget(self.search_btn)
        lay.addLayout(srow)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(130)
        lay.addWidget(self.output)
        self.retranslate()

    def retranslate(self) -> None:
        super().retranslate()
        if not hasattr(self, "output"):  # called by TablePage.__init__ before our widgets exist
            return
        self.add_file.setText(tr("Add file…", "Thêm tệp…"))
        self.add_folder.setText(tr("Add folder…", "Thêm thư mục…"))
        self.sync_btn.setText(tr("Sync folders", "Đồng bộ thư mục"))
        self.search_btn.setText(tr("Search", "Tìm"))
        self.query.setPlaceholderText(tr("Try a question against the indexed documents…", "Thử hỏi trên các tài liệu đã lập chỉ mục…"))
        self.output.setPlaceholderText(tr("Search results and sync reports appear here.", "Kết quả tìm kiếm và báo cáo đồng bộ hiện ở đây."))
        for w, en, vi in ((self.add_file, "Index one file (md, docx, pdf, txt)", "Lập chỉ mục một tệp (md, docx, pdf, txt)"),
                          (self.add_folder, "Index every supported file in a folder", "Lập chỉ mục mọi tệp được hỗ trợ trong thư mục"),
                          (self.sync_btn, "Re-scan the watched folders; entries for deleted files are removed", "Quét lại thư mục theo dõi; mục của tệp đã xóa sẽ bị gỡ"),
                          (self.pause_btn, "Pause or resume automatic indexing of the watched folders", "Tạm dừng hoặc tiếp tục tự động lập chỉ mục thư mục theo dõi"),
                          (self.query, "Search the indexed documents", "Tìm trong tài liệu đã lập chỉ mục"),
                          (self.output, "Search results and sync reports", "Kết quả tìm kiếm và báo cáo đồng bộ")):
            w.setToolTip(tr(en, vi))
            w.setAccessibleName(tr(en, vi))
        self._sync_state()

    def _sync_state(self) -> None:
        busy = self._job is not None
        self.add_file.setEnabled(not busy)
        self.add_folder.setEnabled(not busy)
        self.sync_btn.setEnabled(not busy and self.watching)
        self.pause_btn.setEnabled(self.watching)
        self.pause_btn.setText(tr("Resume watching", "Tiếp tục theo dõi") if self.paused else tr("Pause watching", "Tạm dừng theo dõi"))
        self.search_btn.setEnabled(self._search_rid is None)
        self.state.setText(tr("Indexing… this can take a while.", "Đang lập chỉ mục… có thể mất một lúc.") if busy else
                           tr("Watching folders: paused.", "Theo dõi thư mục: đang tạm dừng.") if self.watching and self.paused else
                           tr("Watching folders: on.", "Theo dõi thư mục: đang bật.") if self.watching else
                           tr("No watched folders (set rag.watch in the config).", "Chưa theo dõi thư mục nào (đặt rag.watch trong cấu hình)."))

    def load(self, ev: Row) -> bool:
        ok = super().load(ev)
        if ok:
            self.watching, self.paused = bool(ev.get("watching")), bool(ev.get("paused"))
            self._sync_state()
        return ok

    def _start(self, msg: Row) -> None:
        rid = self._job = next(_rids)
        self._sync_state()
        self.note(tr("Indexing…", "Đang lập chỉ mục…"))
        QTimer.singleShot(self.JOB_LIMIT_MS, lambda: self.finish(rid))
        self._send({**msg, "rid": rid})

    def _pick_file(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED))
        path, _ = QFileDialog.getOpenFileName(self, tr("Add file", "Thêm tệp"), "", f"{tr('Documents', 'Tài liệu')} ({exts})")
        if path:
            self._start({"type": "rag_add", "path": path})

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("Add folder", "Thêm thư mục"))
        if path:
            self._start({"type": "rag_add", "path": path})

    def _sync(self) -> None:
        if _confirm(("Sync folders", "Đồng bộ thư mục"), (
                "Re-scan the watched folders? New and changed files are indexed, and entries for files that no longer exist are removed from the index.",
                "Quét lại các thư mục theo dõi? Tệp mới và tệp đã đổi sẽ được lập chỉ mục; mục của tệp không còn tồn tại sẽ bị gỡ khỏi chỉ mục.")):
            self._start({"type": "rag_sync"})

    def _search(self) -> None:
        if (q := self.query.text().strip()) and self._search_rid is None:
            self._search_rid = rid = next(_rids)
            self.output.setPlainText(tr("Searching…", "Đang tìm…"))
            self._sync_state()
            QTimer.singleShot(REPLY_TIMEOUT_MS * 4, lambda: self.finish(rid))
            self._send({"type": "rag_search", "query": q, "rid": rid})

    def finish(self, rid: Any) -> bool:
        """The daemon answered (or refused) request `rid`: unlock. True if it was one of ours."""
        hit = False
        if rid is not None and rid == self._job:
            self._job, hit = None, True
        if rid is not None and rid == self._search_rid:
            self._search_rid, hit = None, True
        if hit:
            self._sync_state()
        return hit

    def settle(self, rid: Any) -> bool:
        if rid is not None and rid == self._search_rid:
            self.output.clear()
        return self.finish(rid) or super().settle(rid)

    def show_report(self, ev: Row) -> None:
        self.finish(ev.get("rid"))
        if report := ev.get("report"):
            self.output.setPlainText("\n".join(map(str, report)))

    def show_results(self, ev: Row) -> None:
        if not self.finish(ev.get("rid")):
            return  # a reply to a search that was superseded or timed out
        hits = ev.get("hits") or []
        body = "\n\n".join(f"[{i}] {h['cite']}  ({h['score']})\n{h['text']}" for i, h in enumerate(hits, 1))
        head = (str(ev["note"]) + "\n\n") if ev.get("note") else ""
        self.output.setPlainText(head + (body or tr("No matching passages.", "Không có đoạn nào phù hợp.")))


def _mcp_what(row: Row) -> str:
    verb = tr("connect to", "kết nối tới") if row.get("kind") == "http" else tr("run", "chạy")
    env = row.get("env") or []
    extra = (tr("\nEnvironment variables passed: ", "\nBiến môi trường được truyền: ") + ", ".join(env)) if env else ""
    return f"{verb}:\n\n{row.get('target', '')}{extra}"


class AddServerDialog(QDialog):
    """Collects a new MCP server. The server is stored disabled; nothing runs until the user enables it and reloads."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Add MCP server", "Thêm máy chủ MCP"))
        self.name, self.kind, self.command, self.url = QLineEdit(), QComboBox(), QLineEdit(), QLineEdit()
        self.args, self.env = QPlainTextEdit(), QPlainTextEdit()
        self.kind.addItem(tr("Local command (stdio)", "Lệnh cục bộ (stdio)"), "stdio")
        self.kind.addItem(tr("Remote URL (http)", "URL từ xa (http)"), "http")
        self.args.setPlaceholderText(tr("One argument per line", "Mỗi dòng một đối số"))
        self.env.setPlaceholderText(tr("NAME=value or NAME=${SECRET_NAME}, one per line", "TEN=giá trị hoặc TEN=${TEN_BÍ_MẬT}, mỗi dòng một biến"))
        self.args.setFixedHeight(70)
        self.env.setFixedHeight(70)
        form = QFormLayout(self)
        form.addRow(tr("Name", "Tên"), self.name)
        form.addRow(tr("Type", "Loại"), self.kind)
        form.addRow(tr("Command", "Lệnh"), self.command)
        form.addRow(tr("Arguments", "Đối số"), self.args)
        form.addRow(tr("Environment", "Môi trường"), self.env)
        form.addRow("URL", self.url)
        note = QLabel(tr("The server is added disabled. Review it, enable it, then reload: only then is anything started or contacted. "
                         "Its tools will ask for your approval.",
                         "Máy chủ được thêm ở trạng thái tắt. Hãy xem lại, bật nó rồi tải lại: chỉ khi đó mới có gì được khởi chạy hay kết nối. "
                         "Các công cụ của nó sẽ hỏi bạn duyệt."))
        note.setWordWrap(True)
        form.addRow(note)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)
        self.kind.currentIndexChanged.connect(self._toggle)
        self._toggle()

    def _toggle(self) -> None:
        stdio = self.kind.currentData() == "stdio"
        for w in (self.command, self.args, self.env):
            w.setEnabled(stdio)
        self.url.setEnabled(not stdio)

    def payload(self) -> Row:
        out: Row = {"type": "mcp_add", "name": self.name.text().strip()}
        if self.kind.currentData() == "http":
            return {**out, "url": self.url.text().strip()}
        env: dict[str, str] = {}
        for line in self.env.toPlainText().splitlines():
            if line.strip():
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
        return {**out, "command": self.command.text().strip(), "args": [a for a in self.args.toPlainText().splitlines() if a.strip()], "env": env}


class McpPage(TablePage):
    """MCP servers: add / remove, enable / disable with a look at what would run, see each server's tools and tiers, and reload.
    Changes only take effect on reload, so every row says whether it is waiting for one."""

    JOB_LIMIT_MS = 180_000  # unlock anyway if the daemon never answers

    def __init__(self, send: Send) -> None:
        self._job: int | None = None
        self._reload_needed = False

        def enable(row: Row) -> Row | None:
            if not _confirm(("Enable server", "Bật máy chủ"), (
                    f"Enable '{row['name']}'? After the next reload the agent will " + _mcp_what(row) + "\n\nIts tools ask for your approval unless mcp.yaml says otherwise.",
                    f"Bật '{row['name']}'? Sau lần tải lại tiếp theo, agent sẽ " + _mcp_what(row) + "\n\nCông cụ của nó sẽ hỏi bạn duyệt trừ khi mcp.yaml quy định khác.")):
                return None
            return {"type": "mcp_set", "name": row["name"], "enabled": True}

        def disable(row: Row) -> Row | None:
            n = len(row.get("tools") or [])
            if not _confirm(("Disable server", "Tắt máy chủ"), (
                    f"Disable '{row['name']}'? Its {n} tool(s) are removed from the agent after the next reload.",
                    f"Tắt '{row['name']}'? {n} công cụ của nó sẽ bị gỡ khỏi agent sau lần tải lại tiếp theo.")):
                return None
            return {"type": "mcp_set", "name": row["name"], "enabled": False}

        def tools(row: Row) -> Row | None:
            self._show_tools(row)
            return None

        def remove(row: Row) -> Row | None:
            if not _confirm(("Remove server", "Xóa máy chủ"), (
                    f"Remove '{row['name']}' from mcp.yaml? Its tools disappear after the next reload.",
                    f"Xóa '{row['name']}' khỏi mcp.yaml? Công cụ của nó sẽ biến mất sau lần tải lại tiếp theo.")):
                return None
            return {"type": "mcp_remove", "name": row["name"]}

        enable.applies = lambda r: not r.get("enabled")  # type: ignore[attr-defined]
        disable.applies = lambda r: bool(r.get("enabled"))  # type: ignore[attr-defined]
        super().__init__(send, {"type": "mcp_list"}, "mcp", lambda e: [{"name": n, **v} for n, v in e["servers"].items()],
                         [("Server", "Máy chủ", lambda r: r["name"]),
                          ("Type", "Loại", lambda r: tr("URL", "URL") if r.get("kind") == "http" else tr("Command", "Lệnh")),
                          ("Runs / connects to", "Chạy / kết nối", lambda r: r.get("target", "")),
                          ("Enabled", "Bật", lambda r: tr("Yes", "Có") if r.get("enabled") else tr("No", "Không")),
                          ("Status", "Trạng thái", lambda r: r.get("status", "") + (tr("  ⟳ reload needed", "  ⟳ cần tải lại") if r.get("reload_needed") else "")),
                          ("Tools", "Công cụ", lambda r: len(r.get("tools") or []))],
                         [("Enable", "Bật", enable), ("Disable", "Tắt", disable), ("Tools", "Công cụ", tools), ("Remove", "Xóa", remove)],
                         wide=2, searchable=True,
                         empty=("No MCP servers yet. Use Add server…, or edit mcp.yaml in the data folder.",
                                "Chưa có máy chủ MCP nào. Dùng Thêm máy chủ…, hoặc sửa mcp.yaml trong thư mục dữ liệu."))
        lay: QVBoxLayout = self.layout()  # type: ignore[assignment]
        row = QHBoxLayout()
        self.add_btn, self.reload_btn = QPushButton(), QPushButton()
        self.add_btn.clicked.connect(self._add)
        self.reload_btn.clicked.connect(self._reload)
        row.addWidget(self.add_btn)
        row.addWidget(self.reload_btn)
        self.state = QLabel()
        row.addWidget(self.state, 1)
        lay.insertLayout(1, row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(130)
        lay.addWidget(self.output)
        self.retranslate()

    def retranslate(self) -> None:
        super().retranslate()
        if not hasattr(self, "output"):
            return
        self.add_btn.setText(tr("Add server…", "Thêm máy chủ…"))
        self.reload_btn.setText(tr("Reload MCP", "Tải lại MCP"))
        self.output.setPlaceholderText(tr("Tools of a server and reload results appear here.", "Công cụ của máy chủ và kết quả tải lại hiện ở đây."))
        for w, en, vi in ((self.add_btn, "Add an MCP server (stored disabled)", "Thêm máy chủ MCP (lưu ở trạng thái tắt)"),
                          (self.reload_btn, "Apply the changes: reconnect every enabled server", "Áp dụng thay đổi: kết nối lại mọi máy chủ đang bật"),
                          (self.output, "Server tools and reload results", "Công cụ của máy chủ và kết quả tải lại")):
            w.setToolTip(tr(en, vi))
            w.setAccessibleName(tr(en, vi))
        self._sync_state()

    def _sync_state(self) -> None:
        busy = self._job is not None
        self.add_btn.setEnabled(not busy)
        self.reload_btn.setEnabled(not busy)
        self.reload_btn.setText(tr("Reloading…", "Đang tải lại…") if busy else tr("Reload MCP", "Tải lại MCP"))
        self.state.setText(tr("Reconnecting to the servers… this can take a while.", "Đang kết nối lại các máy chủ… có thể mất một lúc.") if busy else
                           tr("⚠ Changes are not applied yet: reload MCP.", "⚠ Thay đổi chưa được áp dụng: hãy tải lại MCP.") if self._reload_needed else
                           tr("Up to date.", "Đã cập nhật."))

    def load(self, ev: Row) -> bool:
        ok = super().load(ev)
        if ok:
            self._reload_needed = bool(ev.get("reload_needed"))
            if ev.get("reloaded"):
                self.finish(ev.get("rid"))
                self.output.setPlainText("\n".join(f"{n}: {v.get('status', '')}" for n, v in ev["servers"].items()))
            self._sync_state()
        return ok

    def finish(self, rid: Any) -> bool:
        if rid is not None and rid == self._job:
            self._job = None
            self._sync_state()
            return True
        return False

    def settle(self, rid: Any) -> bool:
        return self.finish(rid) or super().settle(rid)

    def _show_tools(self, row: Row) -> None:
        lines = [f"{row['name']}: {row.get('status', '')}"]
        for t in row.get("tools") or []:
            lines.append(f"  [{t['tier']}] {t['name']}" + ("  ⚠ " + tr("runs without approval", "chạy không cần duyệt") if t["tier"] == "T0" else ""))
        for t in row.get("skipped") or []:
            lines.append(f"  ✗ {t['name']}: {t['reason']}")
        if len(lines) == 1:
            lines.append("  " + tr("No tools loaded.", "Chưa có công cụ nào được nạp."))
        self.output.setPlainText("\n".join(lines))

    def _add(self) -> None:
        dlg = AddServerDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._send({**dlg.payload(), "rid": next(_rids)})

    def _reload(self) -> None:
        names = [f"• {r['name']}: {r.get('target', '')}" for r in self.rows if r.get("enabled")]
        listing = "\n".join(names) or tr("(no enabled servers)", "(không có máy chủ nào đang bật)")
        if _confirm(("Reload MCP", "Tải lại MCP"), (
                "Reconnect every enabled server? Local commands are started and remote URLs are contacted:\n\n" + listing,
                "Kết nối lại mọi máy chủ đang bật? Các lệnh cục bộ sẽ được chạy và các URL từ xa sẽ được kết nối:\n\n" + listing)):
            rid = self._job = next(_rids)
            self._sync_state()
            QTimer.singleShot(self.JOB_LIMIT_MS, lambda: self.finish(rid))
            self._send({"type": "mcp_reload", "rid": rid})


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
        self.save_btn.setProperty("role", "primary")
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


class ManagerWindow(QWidget):
    """Sidebar navigation (collapsible with the hamburger) plus one page at a time."""

    navigate_out = Signal(str)  # a sidebar item that is not a page here: chat, approvals or settings

    def __init__(self, send: Send) -> None:
        super().__init__()
        self.resize(1080, 600)
        self.pages: dict[str, tuple[QWidget, str]] = {}
        self.status = QLabel()
        P = TablePage
        mem = P(send, {"type": "memory_list"}, "memory", lambda e: e["items"],
                [("ID", "ID", lambda r: r["id"]), ("Kind", "Loại", lambda r: r["kind"]), ("Status", "Trạng thái",
                 lambda r: r["status"] + (" ⚠" if r.get("sensitive") else "")), ("Text", "Nội dung", lambda r: r["text"])],
                [("Edit", "Sửa", edit_memory_action), ("Approve", "Duyệt", approve_memory_action), ("Delete", "Xóa", delete_memory_action)],
                wide=3, searchable=True, facet=("Status", "Trạng thái", lambda r: r["status"]),
                empty=("No memories yet. The agent saves them as you chat.", "Chưa có bộ nhớ nào. Agent sẽ lưu khi bạn trò chuyện."))
        rag = DocumentsPage(send)
        mcp = McpPage(send)
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
        self.stack = QStackedWidget()
        for key, en, vi, w in specs:
            self.stack.addWidget(w)
            self.pages[key] = (w, en)
            self._tabs.append((w, en, vi))
        self.rail = NavRail("memory")
        self.rail.navigate.connect(self._navigate)
        self.page_title = QLabel()
        bar = top_bar(self, self.page_title)
        bar.layout().addWidget(self.status)  # type: ignore[union-attr]
        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self.rail)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(bar)
        col.addWidget(self.stack, 1)
        shell.addLayout(col, 1)
        # (event type) -> page key; mutations re-request the list they changed
        self._route = {"memory": "memory", "rag": "rag", "mcp": "mcp", "schedules": "schedules", "rules": "rules", "audit": "audit",
                       "usage": "usage", "policy": "policy"}
        self._after = {"memory_updated": "memory", "memory_deleted": "memory", "mcp_set": "mcp", "schedule_cancel": "schedules", "rag_synced": "rag", "rag_added": "rag", "rag_removed": "rag", "mcp_added": "mcp", "mcp_removed": "mcp"}
        self._note = ""  # shown once, after the list that follows a change has loaded
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight)
        for w, _en, _vi in self._tabs:
            if isinstance(w, TablePage):
                w.note = self.status.setText
        self.stack.currentChanged.connect(lambda _i: self._refresh_current())
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("Manager", "Quản lý"))
        self.rail.retranslate()
        for w, _en, _vi in self._tabs:
            if hasattr(w, "retranslate"):
                w.retranslate()
        self._show_title()

    def _show_title(self) -> None:
        for w, en, vi in self._tabs:
            if w is self.stack.currentWidget():
                self.page_title.setText(tr(en, vi))

    def _refresh_current(self) -> None:
        if refresh := getattr(self.stack.currentWidget(), "refresh", None):
            refresh()

    def _navigate(self, key: str) -> None:
        if key in self.pages:
            self.open_page(key)
        else:
            self.navigate_out.emit(key)

    def open_page(self, key: str) -> None:
        self.stack.setCurrentWidget(self.pages[key][0])
        self.rail.set_current(key)
        self._show_title()
        self._refresh_current()
        self.show()
        self.raise_()

    @staticmethod
    def _result_note(ev: Row) -> str:
        t, ok = ev.get("type"), ev.get("ok")
        if t == "memory_deleted":
            return tr("Deleted.", "Đã xóa.") if ok else "⚠ " + tr("That memory no longer exists.", "Mục này không còn tồn tại.")
        if t == "memory_updated":
            if not ok:
                return "⚠ " + tr("That memory no longer exists.", "Mục này không còn tồn tại.")
            if ev.get("status") == "proposed":
                return tr("Saved. It looks sensitive, so it is waiting for your approval.", "Đã lưu. Nội dung có vẻ nhạy cảm nên đang chờ bạn duyệt.")
            return tr("Saved.", "Đã lưu.")
        if t == "mcp_set":
            return tr("Saved. Reload MCP to apply the change.", "Đã lưu. Hãy tải lại MCP để áp dụng.") if ok \
                else "⚠ " + tr("That server no longer exists.", "Máy chủ này không còn tồn tại.")
        if t == "mcp_added":
            return tr("Server added (disabled). Review it, enable it, then reload.", "Đã thêm máy chủ (đang tắt). Hãy xem lại, bật rồi tải lại.")
        if t == "mcp_removed":
            return tr("Removed. Reload MCP to apply.", "Đã xóa. Hãy tải lại MCP để áp dụng.") if ok \
                else "⚠ " + tr("That server no longer exists.", "Máy chủ này không còn tồn tại.")
        if t == "rag_removed":
            return tr("Removed from the index (the file is untouched).", "Đã xóa khỏi chỉ mục (tệp gốc giữ nguyên).") if ev.get("chunks") \
                else tr("That document was already gone from the index.", "Tài liệu này không còn trong chỉ mục.")
        if t in ("rag_synced", "rag_added"):
            if t == "rag_synced" and not ev.get("folders"):
                return "⚠ " + tr("No watched folder is available to sync.", "Không có thư mục theo dõi nào để đồng bộ.")
            return rag_summary(ev.get("report") or [])
        return ""

    def on_event(self, ev: Row) -> bool:
        """Feed a daemon event; returns True if a page consumed it."""
        t = ev.get("type", "")
        if t == "mcp" and ev.get("reloaded"):
            bad = sum(1 for v in ev["servers"].values() if str(v.get("status", "")).startswith("error"))
            self._note = ("⚠ " if bad else "") + tr(f"Reloaded: {len(ev['servers']) - bad} ok, {bad} failed.", f"Đã tải lại: {len(ev['servers']) - bad} ổn, {bad} lỗi.")
        if t in self._route:
            if self.pages[self._route[t]][0].load(ev) is not False:  # type: ignore[attr-defined]
                self.status.setText(self._note)
                self._note = ""
            return True
        if t == "rag_results":
            self.pages["rag"][0].show_results(ev)  # type: ignore[attr-defined]
            return True
        if t in self._after:
            self._note = self._result_note(ev)
            if t in ("rag_synced", "rag_added"):
                self.pages["rag"][0].show_report(ev)  # type: ignore[attr-defined]
            elif t == "rag_removed":
                self.pages["rag"][0].finish(ev.get("rid"))  # type: ignore[attr-defined]
            self.pages[self._after[t]][0].refresh()  # type: ignore[attr-defined]
            return True
        if t == "error":
            for w, _ in self.pages.values():
                if isinstance(w, TablePage):
                    w.settle(ev.get("rid"))  # only the request that failed gets its row back
            if self.isVisible():
                self.status.setText(f"⚠ {ev.get('message', '')}")
        return False
