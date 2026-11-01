# Phase 9 - Hardening

## What was done
| Area | Result | Where |
|---|---|---|
| Resource profiling | `scripts/profile_resources.py` measures start-up, idle RSS/CPU of daemon and UI and the heaviest imports. Fallback models and `icalendar` / `sqlite_vec` / `imagehash` are imported on demand: daemon after the first chat 206 -> 183 MB, idle 109 -> 106 MB. Targets re-set from measurements (the original ones were not reachable with PySide6 + LangGraph) | `docs/adr/0011-resource-targets.md` |
| Security review | threat model, bandit (0 high), pip-audit (clean), 81 security tests (path-traversal corpus, secret formats, tool-registry invariants, shell bypass attempts, SSRF, planted binaries), independent Codex review | `docs/security.md`, `tests/unit/test_security.py` |
| Failure injection | 24 tests: broken config/policy/databases, a tool that explodes, a tracer that raises, unreachable Langfuse, no internet, provider outage codes, message floods, garbage input. 14 of them failed first (daemon would not start with a damaged file) and drove the fixes | `tests/failure/`, `storage.py` |
| Packaging | PyInstaller one-folder bundle (daemon, UI, chat), frozen-aware paths, user-writable policy copy, build and smoke scripts, Inno Setup script | `packaging/`, `scripts/build.ps1`, `docs/packaging.md` |
| Docs | architecture diagram (Mermaid), how-to (tool / MCP server / provider / skin / language / eval / sandbox image), README quick start | `docs/architecture.md`, `docs/howto.md`, `README.md` |

## Bugs found and fixed
1. A damaged `config.yaml` or any SQLite file (checkpoints, usage, audit, approvals, memory, RAG, scheduler) stopped the daemon from starting. Now the file is renamed `*.corrupt-<time>` / `*.invalid-<time>`
   (kept for you) and a fresh one is created; the shipped defaults are used for a broken override. A port outside 1-65535 is rejected.
2. An empty or truncated `policy.yaml` replaced the safety rules. Now the required keys must be present, otherwise the last good rules stay in force (and the engine fails closed if it never had any).
3. Three or more large attachments closed the WebSocket (frame limit 16 MB) and the message vanished. The limit is 32 MB, attachments are capped at 4 files / 15 MB in both daemon and UI, and the UI keeps the error visible
   (it used to be wiped by the next refresh).
4. `taskkill` and `powershell` were started by bare name: absolute System32 paths now. An `assert` in the worker loop became an explicit check.
5. The policy editor could not save when the app sits in a read-only folder: edits go to `%APPDATA%\personal-agent\policy.yaml` and take effect immediately.
6. A flaky Phase 6 test (Windows clock granularity) and the UI client's slow first reconnect (1 s -> 0.3 s) were fixed.

## Verified
- Full suite: **617 passed**, 2 skipped, `ruff` clean, `mypy --strict` clean (100 files).
- Packaged daemon: `/health` in 3.7-4.3 s, finds bundled config/policy, rejects a wrong token, answers a real question with real models; packaged UI and chat start (`scripts/smoke_packaged.py --chat`).
- Idle: no polling loops (scheduler sleeps to the next job; screen watcher only while on); idle CPU 0.0 %.

## Not met or not verified
- **Memory and start-up targets** from Phase 0 (daemon <= 100 MB: 106 MB; UI <= 60 MB: ~100 MB offscreen / ~170 MB on the desktop; combined < 150 MB; cold start <= 3 s: 4.0 s) are missed; revised targets and reasons are in ADR-0011.
- The Inno Setup installer was not compiled; screen control, voice, MCP and Langfuse were not run from inside the bundle; code signing is not set up (`docs/packaging.md`).
- Docker-based sandbox tests need a running Docker (`-m docker`) and were not part of this run.
- Several monitors / mixed DPI (Phase 8) are still untested.

## Independent review (Codex, read-only, clean snapshot, two rounds)
Run as `codex exec -s read-only --ephemeral --skip-git-repo-check` against a `git archive` snapshot (no `.env`, no runtime data); briefs contain no secrets (ADR-0006: the CLI has no MCP mode).
- **Round 1:** criteria A-G: 5 fail, 2 cannot-tell. Confirmed findings: attachments did not taint the task (High), typing into a focused password field without a named element (High), redaction limited to known
  formats (High), unredacted approval previews (High), a vanished policy file bypassing the last-good logic (Medium), malformed WebSocket JSON dropping the client (Medium). Hypotheses: file TOCTOU, bare `system_exe` fallback.
  All fixed, each with a test.
- **Round 2:** every fix re-checked ("partial" with gaps) and new findings: scheduled runs start untainted (High), secrets with custom names not registered, approval rows redacted only at write time,
  no final exception boundary per WebSocket message, fixed policy temp-file name, keystroke path not re-checking the front window. Fixed. Not fixed and documented as residual risk: write-side
  check/use gaps (junction swaps), DOCX archive check/use gap, active memory treated as trusted, short secrets, raw local checkpoints (`docs/security.md`).
- Not covered by Codex: anything needing Windows execution (DPI, junction races, real input), because the sandbox is read-only. A third round was not run.

## Test count
602 passed before the review, 617 after the fixes (`python -m uv run pytest`); the 14 `-m live/docker/desktop` tests are opt-in.
