# Security

Design goal: a capable agent that cannot do anything consequential without you, and cannot be talked into it by text it reads. The rules below are enforced in code (the model is never the barrier) and each is covered by tests.

## Threat model
| Threat | Defence |
|---|---|
| **Prompt injection** (web page, file, email, clipboard, attachment, OCR/screen text, tool or MCP output) | untrusted outputs and attachments *taint* the task; once tainted, every side-effecting tool needs approval and remembered rules are ignored; scheduled runs start tainted; vision/OCR text is marked untrusted; the approval card says "untrusted content seen" |
| Agent reads or writes outside your folders | realpath + allow/deny lists; UNC, `\\?\`, device names, alternate streams, drive-relative paths, `..`, trailing dots/spaces, junctions and `%VAR%` are all denied; `file_read` verifies the final path of the handle it opened |
| Destructive or privileged commands | the unattended allow-list is anchored and refuses chaining/redirection; deny patterns (normalised against backtick/caret/quote tricks) apply even with approval; everything else is a T2 card showing the exact string |
| Agent approves its own actions or edits its own rules | its own UI windows can never be controlled (process ids registered); policy, config, MCP list, databases and audit log live in a denied folder; the settings API is a whitelist; secrets are write-only |
| SSRF | public addresses only (loopback, private, link-local/metadata, CGNAT, IPv4-mapped IPv6 refused); the validated IP is the one connected to (no DNS rebinding); redirects are re-validated |
| Secret leakage | keys only in Credential Manager / git-ignored `.env`; one redaction path for logs, traces, audit, approvals, tool output and WebSocket events; every secret the process reads (including the API token and MCP/HTTP-injected values) is masked **by value**, plus known key formats; the settings API never returns values |
| Local API abuse | loopback only, per-install token (constant-time compare), foreign `Origin` rejected, size limits, bounded queue; malformed JSON/binary frames and handler exceptions return an error event without dropping the connection |
| Supply chain | exact pins + lockfile; `pip-audit` over the locked set (all extras) was clean at review time |
| Damaged state | corrupt databases/config are moved aside, never deleted; a broken `policy.yaml` edit keeps the last good rules and never fails open |

## Policy tiers (`config/policy.yaml`, hot-reloaded)
**T0** read-only → automatic. **T1** reversible and contained → automatic unless the task is tainted (and the tool has side effects). **T2** changes the real world → human approval. **T3** dangerous (system settings, registry, formatting, mass delete, payments) → denied; with explicit enabling it needs a typed `CONFIRM`.
Tools without metadata are T3. MCP tools default to T2. Per-tool rate limits apply. Only scoped file edits can be "approved and remembered"; high-risk tools always ask again; `ui_act` and `schedule_task` never run unattended.

## Approvals
One `interrupt()` per batch of calls; each decision is bound to the exact call (an edit must keep the same risk tier). Approvals are stored on disk, expire after 10 minutes (= rejection), and the waiting time is not charged to the task. Previews and reasons are redacted when stored and again when returned.
The audit log (SQLite, UPDATE/DELETE blocked by triggers, redacted args) records the decision: auto / auto_rule / approved / edited / rejected / timeout / denied_policy / killed.

## Sandbox
Docker, lazily started: no network unless you approve, all capabilities dropped, no-new-privileges, read-only root, non-root, resource limits, a single mount (staging). Changes reach your files only through a reviewed diff, per file, with content-hash binding, conflict detection, backups and undo.

## Screen and input
- **Privacy:** windows matching `screen.block_titles` / `block_processes` (password managers, banking and sign-in pages, wallets, in English and Vietnamese) are blacked out of every capture, refused when targeted, and shown as "(private window)" in listings. Frames are dropped before hashing in watch mode while one is in front. Password-field values are never read.
- **Control:** refused for shells and system tools (cmd, PowerShell, Terminal, regedit, Task Manager, Settings, MMC...) and for the agent's own windows; Win-key and Ctrl+Alt+Del combos refused; ≤ 8 validated steps per approval; each step re-checks its target window (also for coordinate clicks), refuses a target that is covered or not in front, and before typing queries the focused control — password field or unknown ⇒ nothing is typed.
  The target is outlined first; a connected UI is required; moving the mouse or the kill switch stops the run. UI Automation is preferred over the mouse.
- Continuous watching is opt-in, in memory only, ends by itself (timer, kill switch, last UI disconnecting) and always shows a badge.

## Review process and results
Static: bandit (0 high, 2 medium that are false positives, 14 low: subprocess with argument lists), ruff security rules, `pip-audit` (clean). Dynamic: corpus and invariant tests (`tests/unit/test_security.py`: 23 escape paths, 15 chaining attempts, 23 denied commands, 16 unsafe URLs, 10 secret formats, registry-wide
invariants such as "no side-effecting tool is ever auto-allowed after untrusted content"), failure-injection tests (`tests/failure/`), and real-desktop checks. Independent review: Codex (`codex exec -s read-only` on a clean `git archive` snapshot, no secrets in briefs), several rounds over the project's life; it cannot run Windows-specific code in its sandbox.
Representative findings, all fixed with a test: attachments and scheduled runs did not taint the task; typing into a focused password field; secrets without a recognisable format passing redaction; unredacted approval previews; a clicked target covered by another window; keystrokes sent after a focus change; a truncated `policy.yaml` replacing the rules;
shell allow-list hijack by planted executables; DNS-rebinding window; workers receiving the whole user request; approval/kill-switch races; damaged files stopping start-up; oversized messages dropping the socket.

## Residual risks (accepted)
- Injection cannot be removed by filtering text: the barrier is your approval. Approving blindly can still be steered; shell deny patterns are best-effort text matching.
- Write-side check/use gaps: reads verify the opened handle, but writes/moves/deletes/undo check the path then act by path (exploiting it needs a junction swap at that instant; the agent cannot create junctions without an approved command). Archive-size checks for DOCX have the same shape.
- Active memories go into the prompt as trusted (they were created or approved by you). Secrets shorter than 8 characters are not masked by value. LangGraph checkpoints and history are stored raw in the denied data folder.
- The privacy blocklist is name-based and cannot know apps you did not list. Windows blocks input into elevated windows; nothing tries to bypass that.
- Any process running as you can read your `.env`, Credential Manager entries and the API token; the daemon does not defend against malware already running as you. The Docker sandbox is as strong as Docker.
- `browser_read` (Playwright) cannot pin the browser's own connections (DNS-rebinding window); prefer `web_fetch`.
