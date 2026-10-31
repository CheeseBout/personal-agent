# ADR-0002: SQLite + sqlite-vec (+FTS5) for memory and RAG

- Status: accepted (Phase 0)

## Decision
One SQLite file per store; sqlite-vec for dense vectors, SQLite FTS5 (BM25) for keyword search; hybrid via reciprocal-rank fusion.

## Why
Already need SQLite for the LangGraph checkpointer. Tiny footprint, zero server, no extra BM25 dependency (drops rank-bm25). LanceDB/Chroma are heavier and add a second storage engine.

## Consequences
sqlite-vec is pre-1.0 (0.1.9): wrap behind a VectorStore interface so it can be swapped. Verify it loads on Windows in Phase 4.
