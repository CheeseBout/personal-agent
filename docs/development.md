# Development

## Setup and commands
```powershell
python -m pip install --user uv
python -m uv sync --group dev --extra mcp --extra ui --extra voice --extra screen --extra observability   # everything the tests need
python -m uv run pytest            # unit, integration, UI (offscreen) and failure-injection tests
python -m uv run ruff check .
python -m uv run mypy src          # strict
```
Always use `uv sync` / `uv run` (a bare `uv pip install` outside a venv installs into your global Python). Extras: `mcp`, `ui`, `voice` (edge-tts), `voice-local` (faster-whisper), `screen`, `observability` (Langfuse), `local-embeddings` (fastembed), `browser` (Playwright). Groups: `dev`, `build`.
Python 3.12+, Windows-first. Layout: `src/personal_agent/{core,graph,llm,tools,policy,memory,rag,sandbox,perception,observability,ui,voice,settings}`, `config/` (defaults, policy, default avatar), `docker/sandbox/`, `packaging/`, `scripts/`, `tests/`.

## Tests
| Suite | What | Run |
|---|---|---|
| `tests/unit`, `tests/integration` | scripted models (no network): graph paths, budgets, approvals, sandbox, policy, tools, daemon API | default |
| `tests/ui` | windows, avatar, overlays, voice, and the whole UI against a real daemon in a thread (Qt offscreen) | default |
| `tests/failure` | damaged files, exploding tools, failing tracer, no network, floods, garbage input | default |
| `tests/unit/test_security.py` | traversal / secret / command / SSRF corpora and registry-wide invariants | default |
| `-m live` | real providers, embeddings, RAG, memory, sub-agents (a few cents) | `pytest -m live -s` |
| `-m docker` | real containers (needs Docker running) | `pytest -m docker` |
| `-m desktop` | touches the real desktop (clipboard) | `pytest -m desktop` |

Real-desktop scripts (they use your keys and move your mouse: read the header first): `scripts/desktop_check.py` (UI, tray, hotkeys, avatar, voice), `scripts/screen_check.py` (capture, masking, OCR, UI Automation, control, vision), `scripts/agent_screen_e2e.py` (hotkeys + the agent clicking a button after approval).
**Evals:** `agent-eval` runs `tests/evals/scenarios.yaml` (checks: `contains_any/all`, `not_contains`, `tools_any/none`, `reply_lang`, `paused`, `max_steps`, `max_cost_usd`); evals use throw-away audit/approval/memory stores but the real usage meter. Compare changes with `--compare tests/evals/baseline.json`.
**Profiling:** `python -m uv run python scripts/profile_resources.py` (cold start, idle RSS/CPU of daemon and UI, slowest imports). Targets and measurements: [decisions.md](decisions.md).

## Packaging
`.\scripts\build.ps1` (or `-Smoke`) builds `dist\personal-agent` with PyInstaller (one folder, ~416 MB, ~10 min): `agent-core.exe`, `agent-ui.exe` (no console), `agent-chat.exe`. Config and the sandbox Dockerfile are bundled and found through `settings.app_root()`.
Everything you can change lives in `%APPDATA%\personal-agent\` (`config.yaml`, your `policy.yaml`, `mcp.yaml`, `skins\`, databases, `logs\`, optional `.env`); uninstalling keeps it. Autostart: Settings → "Start with Windows" (HKCU Run).
`scripts/smoke_packaged.py [--chat]` starts the bundled daemon on a spare port and checks `/health`, bundled config/policy, the token, and (with `--chat`, a real question) that the lazily imported provider SDKs are inside; it also starts the bundled UI and chat.
Verified: build, daemon start (~3.6 s warm), a real answered question, UI/CLI start. **Not verified:** screen control, voice, MCP and Langfuse from inside the bundle; the Inno Setup script `packaging/installer.iss` (per-user install, shortcuts, optional autostart, stops the programs on uninstall; compile with `iscc packaging\installer.iss`); code signing (unsigned builds trigger SmartScreen/antivirus warnings).

## Extending
**Add a tool.** Create `tools/my_tool.py`:
```python
from langchain_core.tools import tool
from personal_agent.policy.engine import ToolMeta
from personal_agent.tools.registry import cap, register

@tool
def word_count(path: str) -> str:
    """Count words in a workspace file. Đếm số từ trong một tệp."""   # the docstring is the model's manual
    ...
    return cap(result)                                               # always cap (also redacts secrets)

register(word_count, ToolMeta(tier="T0"))
```
then add the module to `tools/registry.py::load_tools`. Set metadata honestly: `side_effects`, `untrusted_output` (text from outside), `risk`, `attended_only`, `preview_fn` (card preview), `scope_fn` (only for low-risk "remember"). Check paths with `policy().check_path`, URLs with `tools.net.check_url`, run subprocesses via `tools.procs.run` (kill-switch aware; `procs.system_exe` for Windows tools).
The registry-wide invariants in `test_security.py` apply to your tool automatically.

**Add an MCP server.** `%APPDATA%\personal-agent\mcp.yaml` (the agent cannot write it):
```yaml
servers:
  files:
    command: npx
    args: [-y, "@modelcontextprotocol/server-filesystem", "C:/some/dir"]
    env: {TOKEN: "${MY_SECRET_NAME}"}     # read from .env / Credential Manager, never shown to the model
    tools: {read_file: T0}                # optional; everything else stays T2
  remote:
    url: http://127.0.0.1:9000/mcp
```
Tools appear as `<server>_<tool>` with untrusted output; reload from Manager → Tools / MCP.

**Add a model provider.** `llm/factory.py`: add the key name to `KEY_NAMES` and build the LangChain model in `build_chat_model` (import the SDK inside the function). Use it in `config/default.yaml` (`models`, `fallbacks`) or Settings; add a price under `pricing:`. Test with `tests/unit/test_llm_lazy.py` as a model and Settings → Models → Test.

**Avatar skins.** A skin is a folder with `manifest.json` in `config/avatar/<name>/` or `%APPDATA%\personal-agent\skins\<name>\`; it hot-reloads.
```json
{"name": "mine", "size": [160, 160], "fallback": "idle",
 "states": {"idle": {"frames": ["idle_0.png", "idle_1.png"], "fps": 4},
            "thinking": {"gif": "think.gif"},
            "success": {"frames": ["yay.png"], "fps": 1, "loop": false},
            "error": null}}
```
States: `idle, listening, thinking, working, speaking, awaiting_approval, success, error, sleeping`. A state may use `frames` (PNG/WebP), `gif`, or `{"builtin": "<state>"}`; a missing/failed state falls back to `fallback`, then `idle`, then the built-in drawing. `fps` 0.2-30; `loop: false` stops on the last frame; `size` 32-512 px square at 1×;
use transparent PNGs (clicks pass through transparent pixels); files must stay inside the skin folder; ≤ 240 frames per state. Animation runs only while the avatar is visible.

**UI strings.** `tr("English", "Tiếng Việt")` everywhere; a test fails on a `tr()` without Vietnamese. **Eval scenarios:** append to `tests/evals/scenarios.yaml`. **Sandbox image:** edit `docker/sandbox/Dockerfile` and bump `sandbox.image` in `policy.yaml`.
**RAG formats:** `rag/loaders.py` returns `Section(text, heading, page)` and registers the extension in `SUPPORTED`.

## Conventions
Work directly on the current branch (`main`); no feature branches, worktrees or PRs. Plain conventional-commit messages with **no** `Co-Authored-By` or AI-attribution lines (`.claude/settings.json` disables them); never commit secrets, `.env` or runtime data. Do not weaken safety defaults to make a demo pass.
Verify claims by running them; independent review uses `codex exec -s read-only --ephemeral` on a clean snapshot (the Codex CLI has no MCP server mode), with no secrets in the brief.

## Known gaps
- **Not verified:** several monitors / mixed DPI (screen capture and the region selector); tray balloon notifications; speech recognition quality on real speech and the silence threshold (`SPEECH_RMS`); the installer; Gemini/OpenAI fallbacks end to end (providers were unavailable when tested).
- **Not implemented:** Google Calendar / CalDAV (local `.ics` only), Node execution, OCR for scanned PDFs, a reranker; `browser_read` and email were only tested with fakes (no browser/mailbox); `fastembed` local embeddings exist but were not run.
- **Behaviour to know:** one task at a time per daemon (lock); usage is recorded when a call returns (a billed-but-dropped request is not counted); a single very long tool loop is bounded by budgets rather than compacted; `undo_change` restores the file as it was before that change; the taint flag is never cleared within a thread (start a new one);
  Vietnamese OCR needs the Windows language pack; vision calls take ~10-13 s; lip-sync is a "speaking" animation, not audio-driven; the Policy page is a YAML editor, not a form.
