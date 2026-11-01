# Phase 6 - Observability and evals

## What was added
| Piece | Where | What it does |
|---|---|---|
| Langfuse tracing | `observability/tracing.py` | One LangChain callback per graph run: every node, LLM call and tool call (including parallel sub-agents) becomes a trace under session = thread id, tagged `personal-agent`. Payloads go through `redact_obj` (Langfuse `mask`). Flushed on shutdown. |
| Local metrics | `observability/metrics.py` | One SQLite row per LLM / tool call (latency, ok) in `usage.db`, joined with the token/cost table. Works with Langfuse off. Pruned after `observability.metrics_keep_days`. |
| Usage page | WebSocket `{"type":"usage","days":7}` and `/usage` in `agent-chat` | Per day: tokens, cost, LLM calls, error rate, avg latency, tool calls, tool success rate. The reply also carries per-model and per-tool totals and a `langfuse` flag for the future UI page. |
| Eval harness | `personal_agent/evals.py`, `tests/evals/scenarios.yaml`, `tests/evals/baseline.json` | `agent-eval` runs 10 vi + en scenarios through the real graph and records pass rate, steps, tokens, cost, seconds per scenario. |

## Langfuse setup
Put `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` in `.env` and install the extra:
`python -m uv sync --extra observability`. `observability.langfuse_enabled` is `true` in `config/default.yaml`, but it does nothing
until both keys exist; if the extra is missing or Langfuse is unreachable the agent logs `langfuse_disabled` and runs normally.
Set it to `false` to switch it off entirely. The `/usage` output in `agent-chat` shows `langfuse: on|off`.

## Evals
```
python -m uv run agent-eval                        # all scenarios (about $0.01 with the default models)
python -m uv run agent-eval --lang vi --only vi-fact,vi-greeting
python -m uv run agent-eval --out run.json --compare tests/evals/baseline.json   # diff pass rate / steps / cost
```
Checks per scenario (all optional): `contains_any`, `contains_all`, `not_contains`, `tools_any`, `tools_none`, `reply_lang`, `paused`,
`max_steps`, `max_cost_usd`. The exit code is 0 only if everything passes. Evals use throw-away audit / approvals / memory stores but the
real usage meter, so their spend counts against the daily cap. Approvals are never granted: a scenario that needs one ends `paused`.
Eval traces carry tags `eval`, the scenario id and the language.

## Findings from running it
- Baseline on 2026-11-01 (classifier / planner / critic = Haiku 5.5, workers = gemini-3.5-flash-lite): 10/10, about $0.0098 total.
- **`gemma4:e4b` is not usable as the classifier here.** Measured: 13-65 s per classification and JSON missing required fields, so every
  turn waited out the timeout before falling back to Haiku (33-68 s per scenario vs 2-5 s after switching back). The classifier is Haiku
  again; the config file says how to retry it. `bge-m3` embeddings via Ollama work.
- An eval case can be wrong, not the agent: `vi-arithmetic` first failed `reply_lang: vi` because the correct answer was the bare number `1200`.

## Limits
- Scenarios are small and non-deterministic (real models): a single run can flake; compare against the baseline rather than trusting one failure.
- The language check is the agent's own cheap detector (diacritics / a few unaccented words), so unaccented Vietnamese can read as English.
- Traces are not tagged with the intent (known only after the classify node). Tokens/cost in Langfuse use Langfuse's own pricing; the daily cap uses `pricing:` in `config/default.yaml`.
- Local metrics count a failed primary model attempt as an error even if the fallback then succeeded; that is intended (it is the provider error rate).
- Redaction is best-effort for known credential formats (same as logs). Masking was unit-tested; it was not verified by reading a trace back from the Langfuse server.
- No Usage *UI* yet: that is Phase 7; this phase provides the data and the CLI view.
