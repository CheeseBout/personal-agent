"""Append-only audit log of every tool call (redacted args, decision, result summary)."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from personal_agent.observability.logging import redact, redact_obj


class AuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        with closing(self._db()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, thread_id TEXT, call_id TEXT,
                  tool TEXT, args TEXT, tier TEXT, decision TEXT, result TEXT);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                  BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                  BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
                """
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def log(self, thread_id: str, call_id: str, tool: str, args: dict[str, Any], tier: str,
            decision: str, result: str = "") -> None:
        # decision: auto | auto_rule | approved | edited | rejected | timeout | denied_policy | killed
        safe_args = json.dumps(redact_obj(args), ensure_ascii=False, default=str)[:2000]
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO audit (ts,thread_id,call_id,tool,args,tier,decision,result) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), thread_id, call_id, tool, safe_args, tier, decision, redact(result)[:300]))

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._db()) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [dict(r) for r in rows]
