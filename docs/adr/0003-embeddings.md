# ADR-0003: Cloud embeddings by default, local fastembed optional

- Status: accepted (Phase 0)

## Decision
Default to a cloud multilingual embedding API (e.g. OpenAI text-embedding-3-small); optional local multilingual-e5 via fastembed (extra 'local-embeddings').

## Why
Zero idle RAM and good vi/en quality. A local ONNX model costs hundreds of MB when loaded.

## Consequences
Documents leave the machine for embedding unless the user switches to local; surfaced in Settings (privacy).
