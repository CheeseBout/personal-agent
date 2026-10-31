# Phase 2 – Brain: report

## Flow
`ingest` (vi/en detect, reset per-task counters) → `classify` (Haiku, structured `Intent`) →
- low confidence + clarifying question → `clarify` (asks ONE question, ends turn)
- chat/question, no plan needed, low risk → **fast path**: Haiku worker only (no planner, no verifier)
- otherwise → `plan` (Sonnet, structured `Goal` with checkable success criteria) → worker loop
  (`compact` → `agent` → `tools` → `account` …) → `verify` (Sonnet critic, structured `Verdict`) → `respond`.

`respond` never claims success on its own: a planned task ends "done" only when the verifier marks every
success criterion met (a `done` with an unmet/empty criteria list is downgraded to `continue` in code).

## Quota protection (what exists, what it cannot do)
| Mechanism | Where |
|---|---|
| Daily token + USD cap, persisted in SQLite, checked **before every LLM call**; blocks everything when hit | `observability/usage.py`, `builder.halt/ingest` |
| Per-task caps: steps, tool calls, seconds, tokens, USD, consecutive failures, replans, verifications | `graph/limits.py`, `config/default.yaml` |
| Cheap model (Haiku 5.5, ~20x cheaper than Sonnet) for classifier / fast path / summariser; Sonnet only for planner, worker on planned tasks, verifier | `config/default.yaml` |
| `max_tokens` per role; big tool outputs stored as artifacts (digest in context) and old turns summarised | config, `graph/context.py` |
| No-progress detection (same tool call 3x in last 6): one forced replan per allowance, then stop | `builder.account` |
| Provider failure (e.g. "credit balance too low") → fallback chain (Google, OpenAI), failing fast; else clear `quota_exhausted` error | `llm/factory.py`, `llm/errors.py` |
| Real prices from the official pricing page; unknown models priced pessimistically, never free | `config/default.yaml`, `Meter.cost` |

**Limits (be aware):**
- Nothing can *guarantee* an Anthropic key never runs out: there is no balance API, and anything else using the same
  key (other apps, the console) is invisible to this daemon. Set a spend limit in the Anthropic Console as the real backstop.
- Caps are checked before a call, so a cap can be overshot by the size of one call chain (observed: 3,353 vs cap 2,500).
- Daily totals only include what this agent recorded (its own `usage.db`).
- Fallbacks are best-effort: on 2026-10-31 the OpenAI and OpenRouter keys had no credit and Gemini returned 503 "high demand".

## Verified (run)
- `ruff`, `mypy --strict` (36 files), `pytest`: 72 offline tests (scripted brain): fast path vs plan path, verifier downgrade, blocked,
  replan limit, clarify, no-progress (one forced replan then stop), step/failure/token budgets, daily token + cost caps (zero model calls),
  vi/en language rule, live rename in both languages, name-injection rejection, mission block pinned only on planned tasks, compaction
  without orphaned tool results, artifact offload + `artifact_read`, provider-error mapping, fallback chain falls through.
- Live (`pytest -m live`, real Anthropic): structured output (Haiku) returns parsed + usage; "who are you" in vi and en answers as "Mimi";
  planned task (plan → `file_read` → verifier) completed for 6.5k tokens ≈ **$0.017**; chat turn ≈ **$0.0003–0.0007**.
- Real daemon: rename, vi chat, `usage`, and daily cap stopping further calls.

## Codex review (read-only `codex exec`, 1 round; fixes were not re-reviewed by Codex)
Accepted and fixed: `done` now needs one met result per goal criterion (matched by position); classifier is behind the budget/daily gate;
turns are serialised by a lock (keeps the daily cap exact and avoids same-thread races; mutation-checked); compaction never summarises the
user's current request; verifier feedback is labelled as non-user automated text; rename write errors are sanitised.
Rejected with evidence: replans unbounded (tests assert exactly 1 + `max_replans` plans and `replans == 2`; `plan` resets `verdict`);
compaction producing orphaned tool results (cut always backs up to a group boundary; unit-tested); fast path lacking a verifier (by design, PROMPT 5: trivial chat bypasses planning).
Codex could not run the tests in its sandbox (`uv` not on its PATH): review by reading only.

## Known gaps
- One task at a time per daemon (lock). Usage is recorded only when a call returns, so a request billed but dropped by the network is not counted.
- Fallback usage is priced by the response's `model_name`; if a provider omits it the primary model's price is used. Cache-read tokens are priced as normal input (over-estimates, safe side).
- A single very long tool loop cannot be compacted (the current request is protected); it is bounded by the per-task token/step caps and artifact offloading instead.
- `memory_reflect` not built (long-term memory is Phase 4). Plan is streamed to the client but not yet approved by the user (HITL is Phase 3).
- Gemini/OpenAI fallback unverified live end-to-end (providers unavailable at test time); the fall-through logic is tested offline.
- Anthropic prompt caching not used (system prompt is below the minimum cacheable size); token estimate for compaction is a chars/3 heuristic.
- Parallel steps / sub-agents: Phase 5. Language detection is heuristic (diacritics / a few unaccented words).
