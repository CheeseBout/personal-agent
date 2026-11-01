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
| 8 | **Attached documents / screenshots / voice notes did not taint the task** (a document could carry injected instructions and a following T1 action would auto-run) - Codex | High | a message with attachments starts the task tainted |
| 9 | **Scheduled (unattended) runs started untainted** - Codex round 2 | High | scheduled turns start tainted |
| 10 | **`ui_act` `type` with only a window could type into a focused password field** - Codex | High | the focused control is queried (UI Automation) before any typing: password => refused; unknown => refused unless a named, checked field was clicked; the front window is re-verified right before the characters/keys go out |
| 11 | **Redaction only knew key formats**: a random API token or a custom-named secret passed through - Codex | High | every secret the process reads (`get_secret`, the API token, MCP env references, HTTP header secrets) is registered and masked by exact value everywhere `redact()` runs |
| 12 | **Approval previews/reasons were stored and returned unredacted** - Codex | High | redacted when stored and again when returned |
| 13 | A missing policy file raised outside the guarded reload; policy saves used a fixed temp name - Codex | Medium | last good rules kept; unique temp file, atomic backup |
| 14 | Malformed JSON / binary WebSocket frames dropped the client; any unexpected exception in a handler could too - Codex | Medium | `bad_request` and a final per-message boundary that returns a generic `internal` error and logs the details server-side |
| 15 | `system_exe` fell back to a bare executable name; `file_read` trusted a path check made before opening - Codex | Low | no fallback; the opened handle's final path is verified (Windows `GetFinalPathNameByHandle`) |

Earlier phases already fixed their own review findings (see each `docs/phaseN.md`: e.g. Phase 8 found a click landing on a covering window, typing into the wrong front window, OCR parser crash).

## Independent review (Codex, `codex exec -s read-only` on a clean `git archive` snapshot)
Two rounds (ADR-0006: the Codex CLI has no MCP server mode). Round 1 returned 6 confirmed findings and 2 hypotheses (rows 8, 10-15 above); all were fixed with a test each. Round 2 re-checked every fix
and returned "partial" on each with concrete gaps (rows 9, 11-14 extended) plus new findings; the ones judged real were fixed, the rest are accepted below. A third round was not run.
Codex could not run the Windows tests or check DPI / junction races (read-only sandbox); those criteria stayed "cannot tell".

## Static analysis
- bandit (`-r src`): 0 high, 2 medium, 14 low. Both mediums are false positives (`/tmp` is inside the Docker container; a SQL string built only from constants). Lows are subprocess usage with argument lists (no shell) and an unrelated UI label.
- pip-audit (locked dependencies, all extras): no known vulnerabilities on 2026-11-01.

## Residual risks (accepted, documented)
- **Check/use gaps on the write side.** Reads verify the final path of the opened handle; writes, moves, deletes and undo check the path and then act by path. Exploiting that needs someone able to swap a
  folder for a junction inside the workspace at that exact moment (the agent cannot create junctions without an approved shell command). DOCX/ZIP size checks have the same shape. Not fixed.
- **Memory is trusted once it is active.** Recalled memories go into the prompt without tainting the task. Active memories are either created by you or approved by you (proposals from a tainted task are never
  auto-activated), so the control is your approval of memory proposals, not a runtime filter.
- **Short secrets (< 8 characters) are not masked by value** (they would mask ordinary words); format-based masking still applies.
- **LangGraph checkpoints and the conversation history are stored raw on your disk** (local, in the denied data folder). Langfuse payloads are masked before export.
- **Keystrokes after a verified focus**: the front window and focused control are checked immediately before sending, but Windows offers no atomic "check then send"; a focus change inside that few-millisecond gap is theoretically possible.
- **Prompt injection cannot be eliminated by filtering text.** The barrier is that actions need approval and the card says when untrusted content preceded them. A user who approves blindly can still be steered.
- Command deny patterns are text matching on a normalised string; the real boundary for anything not allow-listed is the human reading the card.
- The privacy blocklist is name based and cannot know apps you did not list; browsers expose only the active tab title.
- A local process running as you can read your `.env`/Credential Manager entries, or the token, like any user-level program. The daemon does not defend against malware already running as the same user.
- The Docker sandbox protects the host only as well as Docker does (no network, read-only root, dropped capabilities, one mount).
- Windows blocks synthetic input into elevated windows; the agent cannot use those, and nothing here tries to bypass that.
