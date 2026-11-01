# Security review (Phase 9)

Scope: the whole repository at the end of Phase 9. Method: static scan (bandit, ruff `S` rules), dependency audit (pip-audit over the locked set incl. all extras),
a written threat model, tests that encode each rule as a corpus or an invariant (`tests/unit/test_security.py`, `tests/failure/`), reading the risky modules, and an
independent read-only review by Codex (section at the end).

## Threat model
| Threat | Defence | Evidence |
|---|---|---|
| Prompt injection (web page, file, email, clipboard, OCR/screen text, tool output) steers the agent | untrusted outputs taint the task; with taint every side-effecting tool needs a human; vision/OCR text marked untrusted; approvals show a warning; no tool is auto-allowed after taint | `test_after_untrusted_content_nothing_with_side_effects_runs_unasked`, `test_tools_that_read_outside_text_are_marked_untrusted` |
| Agent reads or writes outside the workspace (path traversal) | realpath + allow/deny lists; UNC, `\\?\`, device names, ADS, drive-relative, trailing dot/space, `..`, junctions, `%VAR%` all denied; the file is opened by the checked real path | 23-path corpus, junction test, write/delete refusal |
| Agent runs destructive or privileged commands | unattended allowlist is anchored and refuses chaining/redirection; deny patterns (even with approval) normalised against backtick/caret/quote tricks; everything else is a T2 card showing the exact string | 15 chaining attempts, 23 denied commands |
| Agent approves its own actions or edits its own rules | approval UI windows are uncontrollable (process ids registered); policy/config/MCP/db files sit in a denied folder; settings API is a whitelist; secrets are write-only | `test_the_agents_own_windows_are_never_controllable`, settings API tests |
| SSRF / reaching internal services | public addresses only (loopback, private, link-local/cloud metadata, CGNAT, IPv4-mapped IPv6 refused); the checked IP is the one connected to (DNS rebinding); redirects re-validated | 16 URL cases |
| Secret leakage (logs, traces, audit, tool output, WebSocket, UI) | one redaction path for known key formats plus masking of sensitive keys; keys only in Credential Manager / git-ignored `.env`; settings API never returns values | 10 formats x 3 sinks, audit/approval store test |
| Local API abuse (other local process, web page) | loopback only, per-install token (constant-time compare), foreign `Origin` rejected, message and attachment size limits, bounded turn queue, garbage messages return errors | WS tests, flood test, limits tests |
| Screen / input abuse | privacy blocklist masks or refuses; no control of shells, system tools, the agent's own UI; password fields; Win-key/Ctrl+Alt+Del refused; target must be uncovered and in front; mouse-move and kill-switch abort | `docs/phase8.md` |
| Supply chain | exact pins + lockfile; pip-audit clean at review time | see below |
| Damaged state | corrupt databases/config are moved aside, never deleted; a half-written policy keeps the last good rules | `tests/failure/` |

## Findings and fixes in this phase
| # | Finding | Severity | Fix |
|---|---|---|---|
| 1 | Daemon would not start with a damaged config or SQLite file (a typo or a crash left it dead) | Medium (availability) | `storage.py`: quarantine + fresh start; `load_settings` falls back to defaults |
| 2 | An empty / truncated `policy.yaml` was accepted and replaced the rules | Medium | required keys validated; last good rules kept; fail closed if none |
| 3 | 3+ large attachments closed the WebSocket (1009) and lost the message | Low | frame limit raised, 15 MB total cap enforced in daemon and UI |
| 4 | `taskkill` / `powershell` started by bare name (current-directory / PATH lookup) | Low | absolute System32 paths (`procs.system_exe`) |
| 5 | `assert` used for control flow in the worker loop (disappears under `python -O`) | Low | explicit check |
| 6 | Edited policy could not be saved when the app is installed in a read-only folder | Low | policy edits go to `%APPDATA%\personal-agent\policy.yaml`, shipped copy stays the default |
| 7 | Attachment error messages were wiped immediately by a UI refresh | Low (UX) | message kept until the next send |

Earlier phases already fixed their own review findings (see each `docs/phaseN.md`: e.g. Phase 8 found a click landing on a covering window, typing into the wrong front window, OCR parser crash).

## Static analysis
- bandit (`-r src`): 0 high, 2 medium, 14 low. Both mediums are false positives (`/tmp` is inside the Docker container; a SQL string built only from constants). Lows are subprocess usage with argument lists (no shell) and an unrelated UI label.
- pip-audit (locked dependencies, all extras): no known vulnerabilities on 2026-11-01.

## Residual risks (accepted, documented)
- **Prompt injection cannot be eliminated by filtering text.** The barrier is that actions need approval and the card says when untrusted content preceded them. A user who approves blindly can still be steered.
- Command deny patterns are text matching on a normalised string; the real boundary for anything not allow-listed is the human reading the card.
- The privacy blocklist is name based and cannot know apps you did not list; browsers expose only the active tab title.
- A local process running as you can read your `.env`/Credential Manager entries, or the token, like any user-level program. The daemon does not defend against malware already running as the same user.
- The Docker sandbox protects the host only as well as Docker does (no network, read-only root, dropped capabilities, one mount).
- Windows blocks synthetic input into elevated windows; the agent cannot use those, and nothing here tries to bypass that.
