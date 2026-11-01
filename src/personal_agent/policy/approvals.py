"""Pending approvals (survive restarts) and 'approve and remember' rules, in SQLite."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from personal_agent.observability.logging import redact, redact_obj
from personal_agent.storage import ensure_db_ok


class ApprovalStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        ensure_db_ok(path)  # a damaged file is moved aside instead of crashing the daemon
        with closing(self._db()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, call_id TEXT, tool TEXT, args TEXT,
                  tier TEXT, risk TEXT, preview TEXT, tainted INT, reason TEXT, status TEXT,
                  created REAL, expires_at REAL, decided_at REAL);
                CREATE TABLE IF NOT EXISTS rules (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT, scope TEXT, created REAL,
                  UNIQUE(tool, scope));
                """
            )

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # ---- approvals ---------------------------------------------------------------------------------
    def ensure(self, thread_id: str, call_id: str, tool: str, args: dict[str, Any], tier: str, risk: str,
               preview: str, tainted: bool, reason: str, timeout_s: float) -> dict[str, Any]:
        """Idempotent: the gate node re-runs on resume and must see the same record (same expiry)."""
        args_json = json.dumps(redact_obj(args), ensure_ascii=False, default=str)[:4000]
        with closing(self._db()) as db, db:
            row = db.execute("SELECT * FROM approvals WHERE thread_id=? AND call_id=? AND status='pending'",
                             (thread_id, call_id)).fetchone()
            if row is not None and (row["tool"], row["args"]) != (tool, args_json):
                # a stale row for a different call that reused this id: never show/inherit its card or expiry
                db.execute("UPDATE approvals SET status='superseded', decided_at=? WHERE id=?", (time.time(), row["id"]))
                row = None
            if row is None:
                now = time.time()
                db.execute(
                    "INSERT INTO approvals (thread_id,call_id,tool,args,tier,risk,preview,tainted,reason,status,created,expires_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)",
                    (thread_id, call_id, tool, args_json,
                     tier, risk, redact(preview)[:20_000], int(tainted), redact(reason), now, now + timeout_s))
                row = db.execute("SELECT * FROM approvals WHERE thread_id=? AND call_id=? AND status='pending'",
                                 (thread_id, call_id)).fetchone()
        return dict(row)

    def pending(self, thread_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM approvals WHERE status='pending'"
        a: tuple[Any, ...] = ()
        if thread_id:
            q, a = q + " AND thread_id=?", (thread_id,)
        with closing(self._db()) as db:
            return [{**dict(r), "tainted": bool(r["tainted"])} for r in db.execute(q + " ORDER BY id", a).fetchall()]

    def resolve(self, thread_id: str, call_id: str, status: str) -> None:
        with closing(self._db()) as db, db:
            db.execute("UPDATE approvals SET status=?, decided_at=? WHERE thread_id=? AND call_id=? AND status='pending'",
                       (status, time.time(), thread_id, call_id))

    def kill_all(self) -> int:
        with closing(self._db()) as db, db:
            return db.execute("UPDATE approvals SET status='killed', decided_at=? WHERE status='pending'",
                              (time.time(),)).rowcount

    # ---- remember-rules ----------------------------------------------------------------------------
    def add_rule(self, tool: str, scope: str) -> None:
        with closing(self._db()) as db, db:
            db.execute("INSERT OR IGNORE INTO rules (tool, scope, created) VALUES (?,?,?)", (tool, scope, time.time()))

    def has_rule(self, tool: str, scope: str | None) -> bool:
        if scope is None:
            return False
        with closing(self._db()) as db:
            return db.execute("SELECT 1 FROM rules WHERE tool=? AND scope=?", (tool, scope)).fetchone() is not None

    def rules(self) -> list[dict[str, Any]]:
        with closing(self._db()) as db:
            return [dict(r) for r in db.execute("SELECT id, tool, scope FROM rules ORDER BY id").fetchall()]

    def delete_rule(self, rule_id: int) -> bool:
        with closing(self._db()) as db, db:
            return db.execute("DELETE FROM rules WHERE id=?", (rule_id,)).rowcount > 0
