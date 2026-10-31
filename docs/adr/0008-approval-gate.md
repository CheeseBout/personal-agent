# ADR-0008: Approval gate before the tools node, one interrupt per batch

- Status: accepted (Phase 3)

## Decision
A `gate` node decides every tool call of an AI message (allow / ask / deny) *before* the `tools` node runs. Calls that
need a human are collected into ONE `interrupt()`; the human's decisions are written into `state["gate"]`. The `tools`
node (via `guard`) executes only calls whose plan entry says `run` (default deny) and audits every call.
Resume values are always a non-empty dict (`resume_command`), because LangGraph ignores a falsy resume value.

## Why
LangGraph re-runs the interrupted node from its start on resume. Calling `interrupt()` inside tool execution would re-run
tools that already ran (double side effects). The gate node is pure/idempotent, so re-running it is safe.

## Consequences
- Pending approvals live in the checkpoint (graph) and in `approvals.db` (listing/expiry); the API requires both to agree.
- Approving never changes the reviewed call: an edit must keep the same risk tier or it is rejected.
- Taint ("untrusted output was seen in this thread") is sticky per thread and disables remembered rules / escalates T1 side effects.
- Trust boundary: any local process holding the API token is the user. The daemon is not hardened against the token holder.
