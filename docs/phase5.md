# Phase 5 – Multi-agent: report

## Flow
`plan` -> **`supervisor`** -> (`Send` x N) **`worker`** -> **`merge`** -> `supervisor` ... -> `compact` -> main loop (`agent` / `gate` / `tools` / `verify`) -> `respond`.
The planner labels each step with a `role` (`web`, `research`, `files`, `memory`, `comms`, `coder`, or `main`) and `depends_on`.
The supervisor delegates only INDEPENDENT information-gathering steps whose dependencies are also delegable (cycles are never delegated),
in waves of at most `workers.max_parallel`; a dependent step runs in the next wave and receives exactly its predecessors' summaries.
Anything else (writes, sending, scheduling, composing the answer) stays `main`: the supervisor's normal gated loop, unchanged.
With fewer than `min_delegable` (2) delegable steps nothing is delegated, so simple tasks behave exactly as in Phases 2-4.

## Safety model (the point of this phase)
| Property | How |
|---|---|
| Workers never ask for approval | each tool call is checked by the same `PolicyEngine.evaluate` as the main loop; if it says "approve", the worker STOPS and returns `needs_approval` (tool + redacted args + reason) and the supervisor handles it in its own gate/interrupt. `interrupt()`, the approval store and `resume_command` are untouched. |
| Restricted toolsets | per role in `config/default.yaml` (`workers.roles`); a tool outside the set is denied ("not available to a X worker"); T2 tools (writes, send) are simply absent. |
| Taint | parent `tainted` always applies. A worker that read untrusted content (web/file/email/doc) gets its own later side effects escalated if its role can see private data (`private_context: true`) or its step has dependencies. Whatever a worker read taints the supervisor. |
| Scoped context | a worker sees its system prompt + its step (+ summaries of declared dependencies). No history, no memories, no other steps. Roles without private data (`web`, `coder`) do not even get the user's overall goal. |
| Budgets | per worker (steps, tool calls, seconds, tokens, USD) clipped to a slice of what is LEFT of the task budget; task-wide `max_runs_per_task` and `max_total_steps`; worker tokens/cost/tool calls are charged to the task. |
| Daily cap under parallelism | every worker LLM call first **reserves** its worst-case tokens/USD (`Meter.reserve`), records real usage, then releases. Parallel branches cannot all pass the cap check and overshoot together. |
| Verification | the verifier is given the workers' FIRST-HAND tool outputs (`evidence`), not just their summaries; omitted evidence is announced. |
| Kill switch | workers check it every step; cancelling the turn cancels the `Send` branches; `merge` stops the run on `killed`. |
| Failures | a crashing worker/policy function is settled as `failed`/denied (never left `running`); its dependents are `skipped`; the others and the supervisor continue. |
| Audit | every worker tool call is logged under `<thread>#<step>`, including denials and `needs_approval`. |

## Verified (actually run)
- `ruff`, `mypy --strict` (66 files), `pytest`: **304 passed, 2 skipped** (symlink privilege; "Docker is running" skip for the engine-down test), 14 deselected (live/docker/desktop).
- Offline (scripted models): real parallelism (3 x 0.4 s workers finish in < 1.1 s, `max_running == 3`), wave ordering for dependencies, dependency summaries, scoped context
  (an earlier private message never reaches a worker), per-role toolsets, out-of-role tool denied, approval-needed stops the worker, taint rules (private vs public role, parent taint, propagation to the
  supervisor and its next write), failure isolation, step/run/parallel/token caps, atomic daily cap under parallel workers (+ mutation-tested Meter), kill switch, audit ids, plan sanitising, cycles,
  scheduled threads, API events (`plan.roles`, `worker`).
- **Real models** (Sonnet plans, Haiku workers, Google embeddings, 3 indexed documents): "answer three separate questions, look each up on its own" -> planner chose 3 `memory` sub-agents,
  all `done`, correct answer (12 days / $50 / 2 remote days). Three runs after the evidence fix: $0.057 / $0.064 / $0.097 and 37-45 s (one run before the fix: $0.146, 56 s, verifier looped 3 times). Small sample, high variance.
- **Real Docker** (the engine was started meanwhile): `pytest -m docker` passes - non-root, read-only rootfs, no network, no docker socket / extra mounts, runaway command killed, changeset visible. This closes the Phase 3 gap.

## Found by running it for real (and fixed)
1. Verifier looped: it only saw the supervisor's own tool results, so it kept answering "continue" and the supervisor redid the workers' lookups. Workers now return first-hand evidence.
2. `LiveBrain.chat` cached by role only, ignoring the tool set: a worker would have received the main loop's model bound to ALL tools. The cache key now includes the tools (regression test).
3. The Docker image build located its Dockerfile via the *config* directory, which broke whenever the config directory was relocated (found by the first real Docker run).

## Codex review (read-only `codex exec`, 1 round; it could not run Python, code review only)
Fixed: public-web workers received the user's whole request (exfiltration surface) -> they get only a self-contained step; worker model calls were outside the task step budget -> shared `max_total_steps`;
reservation released before usage was recorded (race window) -> record first; policy exceptions / crashes in a worker could fail the wave and leave the step `running` -> fail closed and settle; empty/`None`
tool-call ids -> denied; evidence truncation unannounced; findings reused on replan not labelled untrusted.
Not changed: worker cancellation only loses in-memory reservations (they die with the process anyway; `finally` releases them otherwise).

## Known gaps
- A `web` worker still holds its (public) step text: a prompt-injected page could make it put that text into a search query or URL. It holds no files, memory or documents, and gets no overall goal, but the planner decides what the step says (the prompt tells it to keep steps public and self-contained).
- The sub-agent summaries and evidence are LLM-written/untrusted text that reaches the supervisor and the verifier; they are labelled and the supervisor stays gated, but a persuasive summary can still mislead the model.
- `desktop` / screen sub-agent: not available yet (Phase 8). `coder` needs Docker; its second sandbox run after the first output may be escalated by taint when the role is strict (it is configured `private_context: false`).
- Parallel workers share one machine/API: Anthropic rate limits (429) hit a worker as a model error; it is reported as `error`, not retried beyond the client's own retries.
- Turns are still serialised by the daemon lock; parallelism is inside one task only.
