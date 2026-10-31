"""run_python: execute Python code in the Docker sandbox (no network, files staged in /work). Node.js: not included."""

from __future__ import annotations

import uuid

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.sandbox.manager import SandboxUnavailable
from personal_agent.tools.registry import cap, register
from personal_agent.tools.sandbox_tools import sandbox, staging

SCRATCH = ".scratch"  # script files live here and are excluded from the reviewable changeset
MAX_CODE = 100_000


@tool
def run_python(code: str, timeout_s: int = 60) -> str:
    """Run Python 3 code inside the isolated sandbox (cwd /work, files from sandbox_stage, no network). Output is captured.
    Chạy mã Python trong sandbox cô lập (không có mạng); đọc/ghi tệp trong /work."""
    if len(code) > MAX_CODE:
        return f"ERROR: code is longer than {MAX_CODE} characters"
    d = staging().work / SCRATCH
    d.mkdir(exist_ok=True)
    script = d / f"{uuid.uuid4().hex[:8]}.py"
    script.write_text(code, encoding="utf-8")
    try:
        rc, out = sandbox().exec(f"python {SCRATCH}/{script.name}", timeout_s)
    except SandboxUnavailable as e:
        return f"ERROR: sandbox unavailable: {e}"
    finally:
        script.unlink(missing_ok=True)
    return cap(f"exit={rc}\n{out}")


register(run_python, ToolMeta(tier="T1", side_effects=True, needs_sandbox=True, untrusted_output=True,
                              preview_fn=lambda a: f"Run in the sandbox (no network):\n{str(a.get('code', ''))[:3000]}"))
