"""RAG index: incremental (hash-based) ingestion, hybrid retrieval (dense + BM25 fused with RRF), citations."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from personal_agent.rag import hybrid
from personal_agent.rag.chunk import chunk
from personal_agent.rag.embed import Embedder, EmbeddingUnavailable
from personal_agent.rag.loaders import load
from personal_agent.storage import ensure_db_ok

log = structlog.get_logger()
MAX_CHUNKS = 5000


@dataclass
class Hit:
    text: str
    source: str
    heading: str
    page: int | None
    score: float

    @property
    def cite(self) -> str:
        loc = ", ".join(x for x in (f"p.{self.page}" if self.page else "", self.heading) if x)
        return f"{Path(self.source).name}" + (f" ({loc})" if loc else "")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class RagIndex:
    def __init__(self, path: Path, embedder: Embedder, chunk_chars: int = 900, overlap: int = 120) -> None:
        ensure_db_ok(path)
        self.path, self.emb, self.size, self.overlap = path, embedder, chunk_chars, overlap
        self._lock = threading.Lock()  # one writer at a time (watcher thread + tools)
        with closing(self._db()) as db, db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY, source TEXT UNIQUE, kind TEXT, sha TEXT,
                     chunks INT, updated REAL);
                   CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, doc_id INT, ord INT, text TEXT,
                     heading TEXT, page INT);
                   CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"""
            )
            have = db.execute("SELECT value FROM meta WHERE key='embedding'").fetchone()
            if have and have[0] != embedder.id:  # embedding model changed: old vectors are meaningless
                log.warning("rag_embedding_changed", old=have[0], new=embedder.id)
                db.execute("DELETE FROM docs")
                db.execute("DELETE FROM chunks")
                db.execute("DROP TABLE IF EXISTS vec")
                db.execute("DROP TABLE IF EXISTS fts")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('embedding', ?)", (embedder.id,))

    def _db(self) -> sqlite3.Connection:
        return hybrid.connect(self.path, self.emb.dim)

    # ---- ingest ------------------------------------------------------------------------------------------
    def ingest(self, path: Path) -> str:
        real = Path(os.path.realpath(path))
        sha = _sha(real)
        with closing(self._db()) as db:
            row = db.execute("SELECT sha, chunks FROM docs WHERE source=?", (str(real),)).fetchone()
        if row and row["sha"] == sha:
            return f"unchanged: {real.name} ({row['chunks']} chunks)"
        chunks = chunk(load(real), self.size, self.overlap)
        if not chunks:
            raise ValueError("the document has no text")
        if len(chunks) > MAX_CHUNKS:
            raise ValueError(f"the document would produce more than {MAX_CHUNKS} chunks")
        vecs = self.emb.embed([c.indexed for c in chunks])  # embed BEFORE touching the index: failure leaves it intact
        with self._lock, closing(self._db()) as db, db:
            self._delete(db, str(real))
            cur = db.execute("INSERT INTO docs (source,kind,sha,chunks,updated) VALUES (?,?,?,?,?)",
                             (str(real), real.suffix.lower().lstrip("."), sha, len(chunks), time.time()))
            for i, (c, v) in enumerate(zip(chunks, vecs, strict=True)):
                cid = db.execute("INSERT INTO chunks (doc_id,ord,text,heading,page) VALUES (?,?,?,?,?)",
                                 (cur.lastrowid, i, c.text, c.heading, c.page)).lastrowid
                db.execute("INSERT INTO vec(rowid, embedding) VALUES (?,?)", (cid, hybrid.blob(v)))
                db.execute("INSERT INTO fts(rowid, text) VALUES (?,?)", (cid, c.indexed))
        return f"{'updated' if row else 'ingested'}: {real.name} ({len(chunks)} chunks)"

    @staticmethod
    def _delete(db: sqlite3.Connection, source: str) -> int:
        ids = [r[0] for r in db.execute("SELECT c.id FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE d.source=?", (source,))]
        for cid in ids:
            db.execute("DELETE FROM vec WHERE rowid=?", (cid,))
            db.execute("DELETE FROM fts WHERE rowid=?", (cid,))
        db.execute("DELETE FROM chunks WHERE doc_id IN (SELECT id FROM docs WHERE source=?)", (source,))
        db.execute("DELETE FROM docs WHERE source=?", (source,))
        return len(ids)

    def remove(self, source: str) -> int:
        with self._lock, closing(self._db()) as db, db:
            return self._delete(db, str(Path(os.path.realpath(source))))

    def documents(self) -> list[dict[str, Any]]:
        with closing(self._db()) as db:
            return [dict(r) for r in db.execute("SELECT source, kind, chunks, updated FROM docs ORDER BY source")]

    # ---- search ------------------------------------------------------------------------------------------
    def search(self, query: str, k: int = 5, source: str | None = None) -> tuple[list[Hit], str]:
        """Hybrid search. Returns (hits, note); falls back to BM25-only (with a note) if embeddings are unavailable."""
        note = ""
        with closing(self._db()) as db:
            qvec: list[float] | None = None
            try:
                qvec = self.emb.embed([query], query=True)[0]
            except EmbeddingUnavailable as e:
                note = f"(keyword-only results: embeddings unavailable - {e})"

            def text_of(cid: int) -> str:
                r = db.execute("SELECT heading, text FROM chunks WHERE id=?", (cid,)).fetchone()
                return f"{r['heading']} {r['text']}" if r else ""

            only: list[int] | None = None
            if source:  # match by full path or file name, then filter inside the retrievers
                srcs = [d["source"] for d in self.documents() if d["source"] == source or Path(d["source"]).name.lower() == source.lower()]
                only = [r[0] for s in srcs for r in db.execute(
                    "SELECT c.id FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE d.source=?", (s,))]
            fused = hybrid.fuse(db, query, qvec, text_of, 50, only)
            hits: list[Hit] = []
            for cid, score in fused:
                r = db.execute("SELECT c.text, c.heading, c.page, d.source FROM chunks c JOIN docs d ON d.id=c.doc_id "
                               "WHERE c.id=?", (cid,)).fetchone()
                if r is None:
                    continue
                hits.append(Hit(r["text"], r["source"], r["heading"], r["page"], score))
                if len(hits) >= k:
                    break
        return hits, note

    # ---- folders (incremental re-index) ----------------------------------------------------------------------------
    def sync_folder(self, folder: Path, allowed: Any = None) -> list[str]:
        """Ingest new/changed supported files, drop vanished ones. `allowed(path)->bool` re-checks policy per file."""
        from personal_agent.rag.loaders import SUPPORTED

        seen, report = set(), []
        for f in sorted(folder.rglob("*")):
            if f.suffix.lower() not in SUPPORTED or not f.is_file() or f.is_symlink():
                continue
            if allowed and not allowed(f):
                continue
            seen.add(str(Path(os.path.realpath(f))))
            try:
                report.append(self.ingest(f))
            except (ValueError, OSError, EmbeddingUnavailable) as e:
                report.append(f"failed: {f.name}: {e}")
        root = str(Path(os.path.realpath(folder)))
        for d in self.documents():
            if d["source"].startswith(root + os.sep) and d["source"] not in seen:
                self.remove(d["source"])
                report.append(f"removed (file is gone): {Path(d['source']).name}")
        return report
