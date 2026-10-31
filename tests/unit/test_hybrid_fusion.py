"""Regression tests for the score fusion, using the cosine margins measured with real Gemini embeddings."""

import math
from pathlib import Path

from personal_agent.rag import hybrid

TEXTS = {
    1: "Chính sách nghỉ phép nhân viên được nghỉ 12 ngày mỗi năm",      # the semantically right passage (other language)
    2: "Expense policy receipts above fifty dollars filed within days",  # shares ONE word ("days") with the English query
    3: "Internal note invoice code PAPAYA42 for the audit",              # weak semantics, but an exact identifier
}


def unit(cos: float) -> list[float]:
    return [cos, math.sqrt(1 - cos * cos), 0.0]  # cosine with the query vector [1, 0, 0] is exactly `cos`


def build(tmp_path: Path):  # type: ignore[no-untyped-def]
    db = hybrid.connect(tmp_path / "h.db", 3)
    for rid, cos in ((1, 0.652), (2, 0.567), (3, 0.30)):  # measured: right passage 0.652 vs accidental one 0.567
        db.execute("INSERT INTO vec(rowid, embedding) VALUES (?,?)", (rid, hybrid.blob(unit(cos))))
        db.execute("INSERT INTO fts(rowid, text) VALUES (?,?)", (rid, TEXTS[rid]))
    return db


def order(db, query: str, qvec):  # type: ignore[no-untyped-def]
    return [rid for rid, _ in hybrid.fuse(db, query, qvec, TEXTS.__getitem__)]


def test_one_accidental_shared_word_does_not_beat_the_semantic_match(tmp_path: Path) -> None:
    db = build(tmp_path)
    # English query, Vietnamese answer: only "days" overlaps with passage 2
    assert order(db, "How many paid vacation days do employees get per year?", [1.0, 0.0, 0.0])[0] == 1


def test_an_exact_identifier_still_wins_over_better_embeddings(tmp_path: Path) -> None:
    db = build(tmp_path)
    assert order(db, "papaya42", [1.0, 0.0, 0.0])[0] == 3  # coverage 1.0 earns the full lexical bonus


def test_without_embeddings_ranking_falls_back_to_lexical_coverage(tmp_path: Path) -> None:
    db = build(tmp_path)
    assert order(db, "audit invoice papaya42", None)[0] == 3


def test_term_folding_handles_vietnamese_and_stopwords() -> None:
    assert hybrid.terms("Nghỉ phép bao nhiêu ngày, và của tôi?") == ["nghi", "phep", "ngay"]
    assert hybrid.fold("Đường Hồng Phúc") == "duong hong phuc"
    assert hybrid.terms("the AND for") == []
    # every token is quoted (1-char tokens dropped), so user text can never become FTS5 syntax
    assert hybrid.fts_query('x" OR y) NEAR(') == '"or" OR "near"'
