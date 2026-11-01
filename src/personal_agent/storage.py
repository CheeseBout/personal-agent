"""Damaged files must not stop the daemon from starting, and must never be silently destroyed.

A file that cannot be read (truncated by a crash / full disk, overwritten by something else) is renamed to `<name>.corrupt-<time>`
(or `.invalid-<time>` for config) next to the original and a fresh one is created. You keep the evidence and can try to recover it.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

import structlog

log = structlog.get_logger()


def quarantine(path: Path, kind: str = "corrupt") -> Path | None:
    """Move `path` (and SQLite's -wal / -shm companions) aside. Returns the new name, or None if the move failed."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.{kind}-{stamp}")
    n = 1
    while target.exists():  # two failures in one second must not overwrite the first evidence
        target = path.with_name(f"{path.name}.{kind}-{stamp}-{n}")
        n += 1
    try:
        path.rename(target)
        for ext in ("-wal", "-shm"):
            side = path.with_name(path.name + ext)
            if side.exists():
                side.rename(target.with_name(target.name + ext))
    except OSError as e:
        log.error("quarantine_failed", file=str(path), error=type(e).__name__)
        return None
    log.error("file_quarantined", file=str(path), moved_to=str(target), kind=kind)
    return target


def ensure_db_ok(path: Path) -> bool:
    """True if `path` is absent or a readable SQLite database; otherwise quarantine it (so the caller creates a fresh one) and return False."""
    if not path.exists() or path.stat().st_size == 0:
        return True
    try:
        with closing(sqlite3.connect(path, timeout=10)) as db:
            db.execute("SELECT count(*) FROM sqlite_master").fetchone()  # reads the header and the schema
        return True
    except sqlite3.DatabaseError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            return True  # another process holds it: that is not corruption
        quarantine(path)
        return False
