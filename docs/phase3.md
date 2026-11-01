# Phase 3 – Safety & sandbox: report

## What exists
| Area | Implementation |
|---|---|
| Policy engine | `policy/engine.py` + `config/policy.yaml` (hot-reload): tiers T0-T3, per-call `tier_fn`, path allow/deny (UNC/ADS/trailing-dot blocked, junctions resolved), command allowlist + hard-deny patterns (normalised), domain allowlist, per-tool rate limit |
| HITL | `gate` node + one `interrupt()` per batch; approve / reject+feedback / edit args / approve-and-remember (T2 only, never when tainted); expiry (default 10 min) = rejection; waiting time is not charged to the task budget; pending approvals survive restarts and are pushed to a reconnecting UI |
| Prompt-injection defence | tool outputs are untrusted; any untrusted-output tool taints the thread; tainted => T1 side-effect tools need approval and remembered rules are ignored; approval cards carry an "untrusted content seen" flag |
| Audit | `observability/audit.py`: SQLite, UPDATE/DELETE blocked by triggers, redacted args, decision = auto / auto_rule / approved / edited / rejected / timeout / denied_policy / killed |
| Docker sandbox | `sandbox/manager.py`: lazy start, idle auto-stop (300 s), `--network none`, `--cap-drop ALL`, `no-new-privileges`, read-only rootfs, non-root, 1 CPU / 512 MB / 128 pids, ONE bind mount (the staging dir); `network=true` is a T2 approval; image built on first use from `docker/sandbox/Dockerfile` |
| Staging -> diff -> approve -> apply | `sandbox/staging.py`: copies in, diffs against originals (added / modified / deleted / conflict / blocked), per-file selection, content hash bound to what was reviewed, conflict detection, backups + `undo_change`; new files in `<workspace>/sandbox-output` are auto-approved, anything touching existing files is T2 |
| Host writes | `file_write` (T2, undoable, atomic), `shell` (allow-listed argv runs unattended; any other command is T2 via PowerShell; hard-denied patterns never run) |
| Kill switch | WS `kill` or `POST /kill` (token): flag + cancel running turns + kill tracked host subprocesses + stop container + mark pending approvals killed + settle threads (no model call, nothing executes); `release_kill` re-enables |
| No-Docker fallback | engine down => `sandbox_*` tools return a clear error; host tools stay T2 |

## Verified (actually run)
- `ruff`, `mypy --strict` (44 files), `pytest`: **155 passed, 1 skipped** (symlink creation needs privileges here), 8 deselected (live / docker).
- Real model (`pytest -m live`): a "create a file" request stops at an approval card and writes only after approval; a file containing an injection was not obeyed (the model refused by itself this time; the gate is covered by scripted tests in which the model DOES obey it).
- Real process kill: a running PowerShell is terminated by the kill switch; new starts are refused until release.
- **Docker (updated in Phase 5):** at the end of Phase 3 the engine was off, so only the generated `docker run` command (fake runner) and the lifecycle were tested. Once Docker Desktop was running, `pytest -m docker` was run for real and passed (non-root, read-only rootfs, no network, no docker socket / extra mounts, runaway command killed, changeset visible). That first real run also exposed a bug: the Dockerfile path was derived from the config directory (fixed).

## Codex review (read-only `codex exec`, 2 rounds; it could not run Python in its sandbox, so code review only)
Round 1 (13 findings). Fixed: duplicate tool-call ids (could share one approval), edit-changes-tier, approval not bound to pending rows, apply TOCTOU (container stopped + hash binding), kill not stopping host processes, undo journal trusted, policy exceptions crashing the node, unbounded staging work, weak command matching, tool-name leakage, task queue cap. Rejected with reasons: path escape via `..` in the container (it only sees /work and the manifest is outside the mount), token-only auth (that is the trust boundary), public `/health`.
Round 2. Fixed: stale pending card reused for a recycled call id, output-folder re-check at apply time, kill waits for process exit. The rest is listed under known gaps.
Found by my own tests: LangGraph ignores `Command(resume={})` (the thread stayed paused) -> `resume_command`.

## Known gaps
- Shell hard-deny is text matching on a PowerShell string (best effort); the real boundary for non-allowlisted commands is the human reading the exact command. A human can still approve something harmful.
- "Approve and remember" creates the rule before the tool runs (a failing tool leaves the rule). Rate-limit slots can be consumed twice when a gate re-runs on resume.
- Apply is not transactional across files and has small filesystem TOCTOU windows (single user, container stopped first).
- Audit/approval DBs are protected by location (the agent's own tools cannot read `%APPDATA%\personal-agent`), not by integrity checks; anyone with file access can drop the triggers.
- Kill does not recover a daemon that crashed mid-run. The taint flag is never cleared within a thread (start a new thread).
- Hotkey and tray entry points for the kill switch belong to the UI (Phase 7); today: `/kill` in `agent-chat`, the WS message, or `POST /kill`.
- `agent-chat` approval prompts were written but not exercised interactively (the API paths they use are tested).
