"""Long-term memory: semantic (facts/preferences), episodic (past tasks), procedural (lessons).

Everything is local SQLite. Auto-retrieval at task start uses BM25 only (free, no API call); `recall` is hybrid.
A memory is `active` (used) or `proposed` (waits for the user). Sensitive text is never auto-activated.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import structlog

from personal_agent.memory.sensitive import is_sensitive
from personal_agent.rag import hybrid
from personal_agent.rag.embed import Embedder, EmbeddingUnavailable

log = structlog.get_logger()
KINDS = ("semantic", "episodic", "procedural")


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def _jaccard(a: str, b: str) -> float:
    x, y = set(_norm(a).split()), set(_norm(b).split())
    return len(x & y) / len(x | y) if x and y else 0.0


class MemoryStore:
    def __init__(self, path: Path, embedder: Embedder | None = None, half_life_days: float = 90.0) -> None:
        self.path, self.emb, self.half_life = path, embedder, half_life_days
        self._lock = threading.Lock()
        with closing(self._db()) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, text TEXT,
                   status TEXT, confidence REAL, source TEXT, sensitive INT, created REAL, last_used REAL, hits INT)"""
            )
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            if embedder is not None:
                have = db.execute("SELECT value FROM meta WHERE key='embedding'").fetchone()
                if have and have[0] != embedder.id:  # old vectors are meaningless now; keyword search keeps working
                    log.warning("memory_embedding_changed", old=have[0], new=embedder.id)
                    db.execute("DROP TABLE IF EXISTS vec")
                db.execute("INSERT OR REPLACE INTO meta VALUES ('embedding', ?)", (embedder.id,))

    def _db(self) -> sqlite3.Connection:
        return hybrid.connect(self.path, self.emb.dim if self.emb else 8)

    # ---- write ---------------------------------------------------------------------------------------------
    def add(self, text: str, kind: str = "semantic", source: str = "user", confidence: float = 0.8,
            status: str = "active") -> dict[str, Any]:
        """Insert, or merge into a near-duplicate. Sensitive text can only be added as 'proposed'."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        text = text.strip()
        if not text or len(text) > 2000:
            raise ValueError("memory text must be 1-2000 characters")
        sens = is_sensitive(text)
        if sens and status == "active":
            status = "proposed"
        confidence = min(1.0, max(0.0, confidence))
        with self._lock, closing(self._db()) as db, db:
            for r in db.execute("SELECT id, text, confidence, status FROM memories WHERE kind=?", (kind,)):
                if _norm(r["text"]) == _norm(text) or _jaccard(r["text"], text) >= 0.8:
                    db.execute("UPDATE memories SET confidence=?, last_used=?, hits=hits+1 WHERE id=?",
                               (max(r["confidence"], confidence), time.time(), r["id"]))
                    return {"id": r["id"], "merged": True, "status": r["status"], "sensitive": sens}
            now = time.time()
            mid = db.execute(
                "INSERT INTO memories (kind,text,status,confidence,source,sensitive,created,last_used,hits) VALUES (?,?,?,?,?,?,?,?,0)",
                (kind, text, status, confidence, source[:200], int(sens), now, now)).lastrowid
            db.execute("INSERT INTO fts(rowid, text) VALUES (?,?)", (mid, text))
            if self.emb is not None:
                try:
                    db.execute("INSERT INTO vec(rowid, embedding) VALUES (?,?)", (mid, hybrid.blob(self.emb.embed([text])[0])))
                except EmbeddingUnavailable as e:  # still searchable by keyword; vector is missing
                    log.warning("memory_embed_failed", error=str(e))
        return {"id": mid, "merged": False, "status": status, "sensitive": sens}

    def update(self, mid: int, text: str | None = None, status: str | None = None) -> bool:
        with self._lock, closing(self._db()) as db, db:
            if db.execute("SELECT 1 FROM memories WHERE id=?", (mid,)).fetchone() is None:
                return False
            if text is not None:
                text = text.strip()
                if not text or len(text) > 2000:
                    raise ValueError("memory text must be 1-2000 characters")
                sens = is_sensitive(text)
                db.execute("UPDATE memories SET text=?, sensitive=? WHERE id=?", (text, int(sens), mid))
                if sens and status is None:  # an edit must not smuggle sensitive text into an ACTIVE memory
                    db.execute("UPDATE memories SET status='proposed' WHERE id=?", (mid,))
                db.execute("DELETE FROM fts WHERE rowid=?", (mid,))
                db.execute("INSERT INTO fts(rowid, text) VALUES (?,?)", (mid, text))
                db.execute("DELETE FROM vec WHERE rowid=?", (mid,))
                if self.emb is not None:
                    try:
                        db.execute("INSERT INTO vec(rowid, embedding) VALUES (?,?)", (mid, hybrid.blob(self.emb.embed([text])[0])))
                    except EmbeddingUnavailable:
                        pass
            if status is not None:
                if status not in ("active", "proposed"):
                    raise ValueError("status must be 'active' or 'proposed'")
                db.execute("UPDATE memories SET status=? WHERE id=?", (status, mid))  # the user may approve sensitive ones
        return True

    def delete(self, mid: int) -> bool:
        with self._lock, closing(self._db()) as db, db:
            db.execute("DELETE FROM fts WHERE rowid=?", (mid,))
            db.execute("DELETE FROM vec WHERE rowid=?", (mid,))
            return db.execute("DELETE FROM memories WHERE id=?", (mid,)).rowcount > 0

    # ---- read ---------------------------------------------------------------------------------------------------
    def items(self, status: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM memories WHERE 1=1"
        a: list[Any] = []
        if status:
            q, a = q + " AND status=?", [*a, status]
        if kind:
            q, a = q + " AND kind=?", [*a, kind]
        with closing(self._db()) as db:
            return [dict(r) for r in db.execute(q + " ORDER BY id", a)]

    def _weight(self, r: sqlite3.Row) -> float:
        """Confidence decayed by age since last use (half-life), so stale memories fade from retrieval."""
        age_days = max(0.0, (time.time() - r["last_used"]) / 86400)
        return float(r["confidence"]) * math.pow(0.5, age_days / self.half_life)

    def _fetch(self, db: sqlite3.Connection, ids: list[int], kind: str | None) -> list[sqlite3.Row]:
        rows = []
        for i in ids:
            r = db.execute("SELECT * FROM memories WHERE id=? AND status='active'", (i,)).fetchone()
            if r is not None and (kind is None or r["kind"] == kind):
                rows.append(r)
        return rows

    def search(self, query: str, k: int = 5, kind: str | None = None, use_vectors: bool = True,
               touch: bool = True) -> list[dict[str, Any]]:
        with closing(self._db()) as db:
            qvec: list[float] | None = None
            if use_vectors and self.emb is not None:
                try:
                    qvec = self.emb.embed([query], query=True)[0]
                except EmbeddingUnavailable:
                    pass

            def text_of(mid: int) -> str:
                r = db.execute("SELECT text FROM memories WHERE id=?", (mid,)).fetchone()
                return str(r[0]) if r else ""

            fused = hybrid.fuse(db, query, qvec, text_of, 30)
            rows = self._fetch(db, [i for i, _ in fused], kind)
            order = {i: s for i, s in fused}
            rows.sort(key=lambda r: -(order[r["id"]] * (0.5 + self._weight(r))))
            out = [dict(r) for r in rows[:k]]
        if touch:  # only memories that are actually shown count as 'used' (that is what resets their decay)
            self._touch([m["id"] for m in out])
        return out

    def for_prompt(self, query: str, k: int = 5, max_chars: int = 700) -> str:
        """Compact, local-only (BM25) memory block for the system prompt. No network, no cost."""
        hits = self.search(query, k, use_vectors=False, touch=False)
        lines, used, shown = [], 0, []
        for m in hits:
            line = f"- [{m['kind']}] {m['text'][:240]}"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            shown.append(m["id"])
            used += len(line)
        self._touch(shown)
        return "\n".join(lines)

    def _touch(self, ids: list[int]) -> None:
        if ids:
            with self._lock, closing(self._db()) as db, db:
                db.executemany("UPDATE memories SET last_used=?, hits=hits+1 WHERE id=?", [(time.time(), i) for i in ids])

    def gc(self, min_weight: float = 0.05) -> int:
        """Forget active memories whose decayed weight fell below the threshold (never touches 'proposed')."""
        with closing(self._db()) as db:
            dead = [r["id"] for r in db.execute("SELECT * FROM memories WHERE status='active'") if self._weight(r) < min_weight]
        for i in dead:
            self.delete(i)
        return len(dead)
