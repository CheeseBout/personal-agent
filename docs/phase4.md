# Phase 4 – Tools & memory: report

## Tool set (all registered with policy metadata; every call goes through the Phase 3 gate)
| Group | Tools | Tier / notes |
|---|---|---|
| Files | `file_read`, `file_list`, `file_search` | T0, output untrusted |
| | `file_write`, `file_edit`, `file_move`, `file_delete` (one file, no folders) | T2, backed up, `undo_change` |
| Shell / code | `shell` (allow-list unattended, else T2), `run_python` | `run_python` = Docker sandbox, no network (needs Docker running) |
| Web | `web_search`, `web_fetch`, `browser_read` | T0/T1; egress tools (`side_effects`) so they need approval once the thread read untrusted content; SSRF-safe |
| HTTP APIs | `http_request` | host must be listed in `policy.yaml` `http.hosts`; the model names a secret, the tool injects it; GET auto, other methods T2 |
| Email | `email_search`, `email_read` (T0, read-only mailbox, untrusted), `email_send` (T2, card shows the exact message) | IMAP/SMTP with an app password |
| Calendar | `calendar_list` (T0), `calendar_add` (T2, undoable) | local `.ics` file; RRULE expanded |
| Clipboard | `clipboard_read` (T0, untrusted, redacted), `clipboard_write` (T2) | PowerShell, UTF-8 |
| MCP | any server in `%APPDATA%\personal-agent\mcp.yaml` | namespaced `<server>_<tool>`, default T2, output untrusted, per-tool override, broken servers isolated |
| RAG | `rag_ingest`, `rag_search`, `rag_list`, `rag_remove` | md / docx / pdf / txt, citations with file + heading + page |
| Memory | `remember`, `recall`, `forget` | sensitive text => T2 approval |
| Scheduler | `schedule_task` (T2, attended-only), `schedule_list`, `schedule_cancel` | runs through the same graph/policy |

## RAG
Structure-aware chunking (never crosses a heading or page), incremental ingestion by SHA-256, one writer lock, embed-before-write so a
failed embedding leaves the old version searchable. Retrieval = **0.7 x cosine + 0.3 x coverage^2** (sqlite-vec + FTS5 with Vietnamese
diacritic folding). Embeddings: `gemini-embedding-001` @ 768 dims (config `rag.embedding`); every call is guarded by the daily cap and metered.
Folder watcher (watchdog OS events, debounced, below-normal thread priority, pausable) for folders listed in `rag.watch` that sit inside `paths.allow`.

## Memory
semantic / episodic / procedural, local SQLite (+ vectors). Retrieval at task start is **BM25 only** (no API call) and injected as information, not
instructions. After a *planned* task the cheap model proposes memories (`memory_reflect`); they become `active` only if confidence >= 0.7, not
sensitive, and the task **did not read untrusted content**; otherwise they stay `proposed` until you approve them (`/memory`, `/memory-ok N`).
Near-duplicates merge, importance decays with a 90-day half-life (`gc`), you can list / edit / approve / delete everything.

## Measured (real services, not mocks)
- Google embeddings, md + docx + pdf corpus, cross-lingual: EN question -> VI doc, VI question -> EN doc, PDF found with page 1: all ranked first.
- Agent answered "how many leave days" from the indexed Vietnamese policy (12) with the right file cited.
- Real MCP server over stdio (`tests/mcp_echo_server.py`): discovery, namespacing, tier override, env secret resolution, approval gate, failure isolation.
- Real PowerShell clipboard round trip with Vietnamese text (original clipboard restored), real `web_fetch` of example.com, loopback/metadata URLs blocked.
- Real reflection (Haiku): the episodic memory was created as `proposed` because the task read a file (by design).

## Found by running it for real (and fixed)
1. Hand-built Google `genai.Client()` was garbage-collected mid-request ("client has been closed"): the client is now kept on the embedder.
2. Reciprocal-rank fusion let one accidental shared word ("days") outrank the semantically right passage in another language: measured the real cosine
   margins (~0.08) and replaced it with the weighted score above (regression test uses the measured numbers).
3. `search` refreshed the decay clock of every hit; now only memories actually shown count as used.

## Codex review (read-only `codex exec`, 1 round; it could not run Python, so code review only)
Fixed: DNS-rebinding window (validated IP is now pinned, Host + SNI kept), `MemoryStore.update` could put sensitive text into an *active* memory,
DOCX decompression bombs / unbounded chunks, embedding cap check-then-spend race (lock; mutation-tested), unbounded `email_read`, schedules creating
schedules (`attended_only`), MCP/any tool output not redacted or capped, oversized ICS, IPv4-in-IPv6 and numeric-host forms.
Rejected with evidence: "secret leaks through a redirect Location" (the returned URL is the request URL and the whole rendered text is scrubbed,
now also for percent-encoded / base64 / JSON-escaped echoes; a test proves it).
Not changed: `mcp_set` over the WebSocket is trusted user control (the token is the trust boundary); file operations keep small check/use windows.

## Known gaps
- `browser_read` (Playwright) was **not run for real** (browser not installed): tested with a fake Playwright; it re-checks every sub-request but cannot pin Chromium's connections (DNS rebinding window remains), so prefer `web_fetch`.
- Email was tested with fake IMAP/SMTP servers only (no mailbox configured). Google Calendar / CalDAV: not implemented (local `.ics` only). Node.js execution and OCR for scanned PDFs: not implemented.
- Local embeddings (`fastembed`) code exists but was not run (optional extra). No reranker.
- `undo_change` restores the file as it was *before that change*, discarding later edits to the same file.
- Memories from any task that read a file/web page need your approval; this is deliberately conservative (stops memory poisoning) but means `/memory-ok` is part of normal use.
- OpenAI and OpenRouter keys still have no credit; Gemini returned 503 "high demand" at times (embeddings retry 3x with backoff, then report keyword-only results).
