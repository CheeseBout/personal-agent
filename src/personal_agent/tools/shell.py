"""Shell tool: allowlisted commands only, executed as argv (no shell) until HITL exists (Phase 3)."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.tools.files import workspace
from personal_agent.tools.registry import cap, policy, register


@tool
def shell(command: str) -> str:
    """Run an allowlisted read-only command (e.g. hostname, whoami, python --version) in the workspace.
    Chạy lệnh chỉ-đọc nằm trong danh sách cho phép; lệnh khác bị từ chối."""
    pol = policy()
    d = pol.check_command(command)
    if not d.allowed:
        return f"ERROR: command refused: {d.reason}"
    cwd = workspace()
    if not cwd.is_dir():
        return f"ERROR: workspace folder does not exist: {cwd}"
    timeout = float(pol.cfg.get("commands", {}).get("timeout_s", 30))
    argv = shlex.split(command, posix=False)
    exe = sys.executable if argv[0] == "python" else shutil.which(argv[0])
    # An executable inside the agent-writable workspace could be a planted binary: never run it.
    if not exe or pol.check_path(exe).allowed:
        return "ERROR: command refused: executable not found or located inside the workspace"
    try:
        r = subprocess.run(
            [exe, *argv[1:]], cwd=cwd, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout:.0f}s"
    except OSError as e:
        return f"ERROR: cannot run command: {e.strerror or type(e).__name__}"
    out = r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace")
    return cap(f"exit={r.returncode}\n{out}")


register(shell, ToolMeta(tier="T1", side_effects=False))
