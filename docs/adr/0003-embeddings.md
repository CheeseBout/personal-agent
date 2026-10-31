# ADR-0003: Cloud embeddings by default, local fastembed optional

- Status: accepted (Phase 0)

## Decision
Default to a cloud multilingual embedding API (e.g. OpenAI text-embedding-3-small); optional local multilingual-e5 via fastembed (extra 'local-embeddings').

## Why
Zero idle RAM and good vi/en quality. A local ONNX model costs hundreds of MB when loaded.

## Update (Phase 4)
The default is Google `gemini-embedding-001` (768 dims), not OpenAI: the OpenAI key available had no credit, and Gemini measured well
for Vietnamese <-> English retrieval. Provider/model/dim live in `rag.embedding`; changing them drops the old vectors automatically
(the index stores which model made them). OpenAI and local (fastembed) providers are implemented behind the same interface.

## Consequences
Documents leave the machine for embedding unless the user switches to local; surfaced in Settings (privacy).
