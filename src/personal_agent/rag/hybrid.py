"""SQLite helpers shared by RAG and memory: sqlite-vec (dense), FTS5 (BM25) and score fusion.

sqlite-vec is pre-1.0 (ADR-0002): everything vector-specific is confined to this file.

Fusion: score = 0.7 * cosine + 0.3 * coverage^2 (coverage = share of the query's content words found in the passage).
Plain reciprocal-rank fusion was tried first and, measured with real Gemini embeddings, let ONE accidental shared word
("days") outrank the semantically right passage in another language (the cross-lingual cosine margin is only ~0.08).
Squaring the coverage makes a lone shared word nearly irrelevant while an exact identifier (coverage 1) still wins.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Callable
from pathlib import Path

import sqlite_vec

W_DENSE, W_LEX = 0.7, 0.3
_STOP = {
    "the", "and", "for", "are", "was", "were", "with", "from", "that", "this", "have", "has", "had", "you", "your", "not",
    "but", "can", "how", "what", "when", "where", "who", "which", "does", "did", "get", "got", "per", "its", "our",
    "their", "they", "them", "she", "him", "her", "his", "out", "all", "any", "too", "very", "much", "many", "more",
    "most", "one", "two",
    # Vietnamese, diacritics folded (terms() folds text before filtering)
    "va", "cua", "co", "cac", "nhung", "mot", "duoc", "cho", "khong", "nay", "khi", "nao", "bao", "nhieu", "gi", "nhu",
    "theo", "trong", "voi", "la", "toi", "ban", "minh", "thi", "ma", "de",
}


def connect(path: Path, dim: int) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec USING vec0(embedding float[{dim}])")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, tokenize='unicode61 remove_diacritics 2')")
    return db


def blob(v: list[float]) -> bytes:
    return bytes(sqlite_vec.serialize_float32(v))


def fold(text: str) -> str:
    """Lower-case and strip diacritics (Vietnamese: 'Nghỉ phép' -> 'nghi phep'), like the FTS tokenizer does."""
    s = unicodedata.normalize("NFKD", text.lower().replace("đ", "d"))
    return "".join(c for c in s if not unicodedata.combining(c))


def terms(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for t in re.findall(r"\w+", fold(text)):
        if len(t) > 2 and t not in _STOP:
            seen[t] = None
    return list(seen)


def fts_query(text: str) -> str | None:
    """Quote every token so user text can never be parsed as FTS5 syntax; OR them for recall."""
    toks = [t for t in re.findall(r"\w+", text.lower()) if len(t) > 1][:24]
    return " OR ".join(f'"{t}"' for t in toks) or None


def dense(db: sqlite3.Connection, qvec: list[float], k: int) -> dict[int, float]:
    """rowid -> cosine similarity (vectors are L2-normalised, so cos = 1 - d^2 / 2)."""
    rows = db.execute("SELECT rowid, distance FROM vec WHERE embedding MATCH ? AND k = ? ORDER BY distance", (blob(qvec), k)).fetchall()
    return {r[0]: 1.0 - float(r[1]) ** 2 / 2.0 for r in rows}


def sparse(db: sqlite3.Connection, text: str, k: int) -> list[int]:
    q = fts_query(text)
    if q is None:
        return []
    rows = db.execute("SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?", (q, k)).fetchall()
    return [r[0] for r in rows]


def fuse(db: sqlite3.Connection, query: str, qvec: list[float] | None, text_of: Callable[[int], str],
         k: int = 50) -> list[tuple[int, float]]:
    """Candidates from both retrievers, best first. Without a query vector (embeddings down) only coverage counts."""
    cos = dense(db, qvec, k) if qvec is not None else {}
    cand = list(dict.fromkeys([*cos, *sparse(db, query, k)]))
    qt = terms(query)
    out: list[tuple[int, float]] = []
    for rid in cand:
        if qvec is not None and rid not in cos:  # came only from BM25: still needs its cosine
            row = db.execute("SELECT vec_distance_L2(embedding, ?) FROM vec WHERE rowid=?", (blob(qvec), rid)).fetchone()
            cos[rid] = 1.0 - float(row[0]) ** 2 / 2.0 if row else 0.0
        words = set(terms(text_of(rid)))
        cover = sum(t in words for t in qt) / len(qt) if qt else 0.0
        # squared: one accidental shared word among five (0.2 -> 0.04) must not outweigh a real semantic gap, while a
        # passage containing every query word (1.0) keeps the full bonus
        out.append((rid, W_DENSE * cos.get(rid, 0.0) + W_LEX * cover**2 if qvec is not None else cover))
    return sorted(out, key=lambda kv: -kv[1])
