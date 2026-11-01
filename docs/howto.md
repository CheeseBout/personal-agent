# How to extend it

## Add a tool
1. Create `src/personal_agent/tools/my_tool.py`:
   ```python
   from langchain_core.tools import tool
   from personal_agent.policy.engine import ToolMeta
   from personal_agent.tools.registry import cap, register

   @tool
   def word_count(path: str) -> str:
       """Count words in a workspace file. Đếm số từ trong một tệp.   <- the docstring is the model's manual: be clear, bilingual-friendly"""
       ...
       return cap(result)          # always cap output (also redacts secrets)

   register(word_count, ToolMeta(tier="T0"))   # T0 read-only | T1 reversible, contained | T2 changes the real world (asks you)
   ```
2. Add the module to the import list in `tools/registry.py::load_tools`.
3. Choose the metadata honestly: `side_effects=True` for anything that changes state, `untrusted_output=True` if the result can contain text from outside (web, files, mail),
   `risk`, `attended_only=True` if it must never run from a schedule, `preview_fn` to show what will happen on the approval card, `scope_fn` only for low-risk "approve and remember".
4. Check paths with `policy().check_path(...)`, URLs with `tools.net.check_url`, and run subprocesses through `tools.procs.run` (so the kill switch can stop them; use `procs.system_exe`).
5. Tests: the registry-wide invariants in `tests/unit/test_security.py` apply to your tool automatically (side effects are never T0 and never auto-run after untrusted content); add its own tests.

## Add an MCP server
Edit `%APPDATA%\personal-agent\mcp.yaml` (the agent cannot write it), or use the Manager -> Tools / MCP page to enable/disable:
```yaml
servers:
  files:
    command: npx
    args: [-y, "@modelcontextprotocol/server-filesystem", "C:/some/dir"]
    env: {TOKEN: "${MY_SECRET_NAME}"}     # read from .env / Credential Manager, never shown to the model
    tools: {read_file: T0}                # optional; everything else stays T2 (asks you)
  remote:
    url: http://127.0.0.1:9000/mcp
```
Reload from the Manager page. Tools appear as `<server>_<tool>`, outputs are untrusted. Needs `uv sync --extra mcp`.

## Add a model provider
1. `llm/factory.py`: add the key name to `KEY_NAMES` and construct the LangChain chat model in `build_chat_model` (import the SDK inside the function: provider SDKs cost 40-90 MB).
2. Use it in `config/default.yaml` (`models:` roles and `fallbacks`) or in Settings -> Models; add a price under `pricing:` (unknown models are billed pessimistically).
3. Test with `tests/unit/test_llm_lazy.py` as a model; try it with the Test button (`test_role`) and `agent-eval`.

## Add an avatar skin
`docs/skins.md`.

## Add a UI language string
Use `tr("English", "Tiếng Việt")` everywhere; a test fails if a `tr()` call has no Vietnamese text.

## Add an eval scenario
Append to `tests/evals/scenarios.yaml` (checks: `contains_any`, `tools_none`, `reply_lang`, `paused`, `max_steps`, `max_cost_usd`...), then `agent-eval --only <id> --compare tests/evals/baseline.json`.

## Extend the sandbox image
Edit `docker/sandbox/Dockerfile` (`RUN pip install ...`), bump `sandbox.image` in `config/policy.yaml`; the new image is built on first use.

## Add a document format to RAG
`rag/loaders.py`: return `Section(text, heading, page)` items and add the extension to `SUPPORTED`.
