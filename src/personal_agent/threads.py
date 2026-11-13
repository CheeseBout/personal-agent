"""Chat history the user sees: one row per conversation and one per message, searchable.

This is what the UI shows and exports. It is separate from the agent's own context (the LangGraph checkpoint), which is
summarised when it grows: history here is never summarised, and deleting a thread here also drops its checkpoint.
Text is redacted before it is stored, and attachments are stored by name only.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from personal_agent.observability.logging import redact
from personal_agent.rag.hybrid import fold
from personal_agent.storage import ensure_db_ok

KINDS = ("user", "agent", "event", "error", "note")
MAX_ENTRY = 20_000  # characters kept per message; the full answer is in the checkpoint while it lives there
PAGE = 200
_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL, pinned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
  text TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}', flag TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS entries_thread ON entries (thread_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(folded, tokenize='unicode61');
"""


def new_id() -> str:
    return "c-" + secrets.token_hex(4)


def title_from(text: str) -> str:
    """A short title from the first message: first line, collapsed, cut at a word boundary."""
    line = re.sub(r"\s+", " ", (text.strip().splitlines() or [""])[0]).strip()
    if len(line) <= 50:
        return line
    cut = line[:50].rsplit(" ", 1)[0] or line[:50]
    return cut.rstrip(" ,.;:") + "…"


def _query(q: str) -> str | None:
    words = re.findall(r"\w+", fold(q))
    return " ".join(f'"{w}"*' for w in words) or None


class ThreadStore:
    def __init__(self, path: Path) -> None:
        ensure_db_ok(path)
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._last = 0.0  # timestamps never repeat: the clock is coarse on Windows, and "newest first" must be stable
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- writing ----
    def add(self, thread_id: str, kind: str, text: str, meta: dict[str, Any] | None = None, flag: str = "") -> int:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        text = redact(text)[:MAX_ENTRY]
        with self._lock, self._db:
            now = self._last = max(time.time(), self._last + 1e-6)
            self._db.execute("INSERT OR IGNORE INTO threads (id, title, created, updated) VALUES (?, '', ?, ?)", (thread_id, now, now))
            cur = self._db.execute("INSERT INTO entries (thread_id, ts, kind, text, meta, flag) VALUES (?, ?, ?, ?, ?, ?)",
                                   (thread_id, now, kind, text, json.dumps(meta or {}, ensure_ascii=False), flag))
            rowid = int(cur.lastrowid or 0)
            self._db.execute("INSERT INTO entries_fts (rowid, folded) VALUES (?, ?)", (rowid, fold(text)))
            self._db.execute("UPDATE threads SET updated = ? WHERE id = ?", (now, thread_id))
            if kind == "user":  # the first message names the conversation, until the user renames it
                self._db.execute("UPDATE threads SET title = ? WHERE id = ? AND title = ''", (title_from(text), thread_id))
        return rowid

    def rename(self, thread_id: str, title: str) -> bool:
        title = re.sub(r"\s+", " ", title).strip()[:80]
        if not title:
            raise ValueError("a title cannot be empty")
        with self._lock, self._db:
            return self._db.execute("UPDATE threads SET title = ? WHERE id = ?", (title, thread_id)).rowcount > 0

    def pin(self, thread_id: str, pinned: bool) -> bool:
        with self._lock, self._db:
            return self._db.execute("UPDATE threads SET pinned = ? WHERE id = ?", (int(pinned), thread_id)).rowcount > 0

    def delete(self, thread_id: str) -> int:
        """Remove the conversation and every message in it. Returns how many messages went."""
        with self._lock, self._db:
            ids = [r[0] for r in self._db.execute("SELECT id FROM entries WHERE thread_id = ?", (thread_id,))]
            self._db.executemany("DELETE FROM entries_fts WHERE rowid = ?", [(i,) for i in ids])
            self._db.execute("DELETE FROM entries WHERE thread_id = ?", (thread_id,))
            self._db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        return len(ids)

    # ---- reading ----
    def exists(self, thread_id: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM threads WHERE id = ?", (thread_id,)).fetchone() is not None

    def threads(self, *, scheduled: bool = False) -> list[dict[str, Any]]:
        """Newest first, pinned on top. Scheduled runs (`sched-*`) are hidden unless asked for."""
        with self._lock:
            rows = self._db.execute(
                "SELECT t.id, t.title, t.created, t.updated, t.pinned, (SELECT count(*) FROM entries e WHERE e.thread_id = t.id) AS n "
                "FROM threads t ORDER BY t.pinned DESC, t.updated DESC").fetchall()
        out = [{"id": r["id"], "title": r["title"], "created": r["created"], "updated": r["updated"], "pinned": bool(r["pinned"]), "count": r["n"]}
               for r in rows]
        return out if scheduled else [t for t in out if not t["id"].startswith("sched-")]

    def history(self, thread_id: str, before: int | None = None, limit: int = PAGE) -> dict[str, Any]:
        """The newest `limit` messages (or the `limit` before id `before`), oldest first; `more` says whether older ones exist."""
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._db.execute("SELECT id, ts, kind, text, meta FROM entries WHERE thread_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
                                    (thread_id, before if before is not None else 2**62, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        return {"entries": [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "text": r["text"], "meta": json.loads(r["meta"])} for r in rows], "more": more}

    def search(self, q: str, flag: str = "", limit: int = 30) -> list[dict[str, Any]]:
        """Messages matching every word (accents and case ignored), newest first. `flag` ('approval' or 'attach') narrows to those messages."""
        match, limit = _query(q), max(1, min(int(limit), 100))
        if match is None and not flag:
            return []
        sql = ("SELECT e.id, e.thread_id, t.title, e.ts, e.kind, e.text FROM entries e JOIN threads t ON t.id = e.thread_id "
               + ("JOIN entries_fts f ON f.rowid = e.id " if match else "") + "WHERE 1=1 " + ("AND f.folded MATCH ? " if match else "")
               + ("AND e.flag = ? " if flag else "") + "AND e.thread_id NOT LIKE 'sched-%' ORDER BY e.id DESC LIMIT ?")
        args: list[Any] = ([match] if match else []) + ([flag] if flag else []) + [limit]
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [{"id": r["id"], "thread_id": r["thread_id"], "title": r["title"], "ts": r["ts"], "kind": r["kind"], "text": _snippet(r["text"], q)} for r in rows]

    def export_markdown(self, thread_id: str) -> str:
        with self._lock:
            t = self._db.execute("SELECT title, created FROM threads WHERE id = ?", (thread_id,)).fetchone()
            rows = self._db.execute("SELECT ts, kind, text FROM entries WHERE thread_id = ? ORDER BY id", (thread_id,)).fetchall()
        if t is None:
            raise ValueError("no such conversation")
        out = [f"# {t['title'] or thread_id}", "", f"Started {time.strftime('%Y-%m-%d %H:%M', time.localtime(t['created']))}", ""]
        for r in rows:
            stamp = time.strftime("%H:%M", time.localtime(r["ts"]))
            if r["kind"] == "user":
                out += [f"**You** ({stamp})", "", r["text"], ""]
            elif r["kind"] == "agent":
                out += [f"**Agent** ({stamp})", "", r["text"], ""]
            else:  # tool calls, errors and notes stay out of the way
                out += [f"> {r['text'].replace(chr(10), ' ')[:300]}", ""]
        return "\n".join(out)


def entry_for(ev: dict[str, Any]) -> tuple[str, str, dict[str, Any], str] | None:
    """What a daemon event looks like in the history: (kind, text, meta, flag), or None for events that are not history.
    The wording mirrors the UI's session reducer, so a reloaded conversation reads like the live one."""
    t = ev.get("type")
    if t == "intent":
        return "event", f"intent: {ev.get('intent')} ({int(float(ev.get('confidence') or 0) * 100 + 0.5)}%)", {}, ""
    if t == "plan":
        return "event", f"plan: {ev.get('objective', '')}", {}, ""
    if t == "worker":
        return "event", f"[{ev.get('role')}] {ev.get('status')}: {ev.get('summary', '')}", {}, ""
    if t == "tool_call":
        return "event", f"tool: {ev.get('name')} {json.dumps(ev.get('args') or {}, ensure_ascii=False, separators=(',', ':'), default=str)[:160]}", {}, ""
    if t == "tool_result":
        return "event", f"  -> {ev.get('status')}: {str(ev.get('content', ''))[:200]}", {}, ""
    if t == "verdict":
        return "event", f"verify: {ev.get('status')} {ev.get('notes', '')}".strip(), {}, ""
    if t == "approval_request":
        tools = ", ".join(str(a.get("tool")) for a in ev.get("approvals", []) if isinstance(a, dict)) or "?"
        return "event", f"approval requested: {tools}", {"approval": True}, "approval"
    if t == "final":
        return "agent", str(ev.get("text", "")) + (f" (stopped: {ev['stopped']})" if ev.get("stopped") else ""), {"used": ev.get("used") or {}}, ""
    if t == "error":
        return "error", f"{ev.get('code')}: {ev.get('message')}", {}, ""
    if t == "compacted":
        return "note", "Earlier messages were summarised to keep the conversation within the model's context. They stay here in full.", {}, ""
    return None


def _snippet(text: str, q: str, width: int = 140) -> str:
    flat = re.sub(r"\s+", " ", text)
    words = re.findall(r"\w+", fold(q))
    at = min((i for w in words if (i := fold(flat).find(w)) >= 0), default=0)
    start = max(0, at - 40)
    return ("…" if start else "") + flat[start:start + width] + ("…" if start + width < len(flat) else "")
