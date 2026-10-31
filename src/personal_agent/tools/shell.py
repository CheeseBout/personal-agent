"""Shell tool. Allowlisted read-only commands run unattended (as argv, no shell); anything else is T2.

The gate (graph/builder.py) enforces approval from `tier_fn` BEFORE this function runs.
Deny patterns (format, reg, rm -rf ...) are refused even when a human could approve them.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from typing import Any

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.tools import procs
from personal_agent.tools.files import workspace
from personal_agent.tools.registry import cap, policy, register


def _tier(args: dict[str, Any]) -> str:
    cmd = str(args.get("command", ""))
    pol = policy()
    if pol.check_command(cmd).allowed:
        return "T1"
    return "T3" if pol.is_hard_denied_command(cmd) else "T2"


@tool
def shell(command: str) -> str:
    """Run a PowerShell command in the workspace. Read-only basics (hostname, whoami, python --version,
    where <name>) run directly; any other command asks the user for approval first.
    Chạy lệnh PowerShell trong workspace; lệnh ngoài danh sách chỉ-đọc cần người dùng duyệt."""
    pol = policy()
    if reason := pol.is_hard_denied_command(command):
        return f"ERROR: command refused: {reason}"
    cwd = workspace()
    if not cwd.is_dir():
        return f"ERROR: workspace folder does not exist: {cwd}"
    timeout = float(pol.cfg.get("commands", {}).get("timeout_s", 30))
    if pol.check_command(command).allowed:  # unattended path: argv exec, absolute executable
        argv = shlex.split(command, posix=False)
        exe = sys.executable if argv[0] == "python" else shutil.which(argv[0])
        # An executable inside the agent-writable workspace could be a planted binary: never run it.
        if not exe or pol.check_path(exe).allowed:
            return "ERROR: command refused: executable not found or located inside the workspace"
        cmdline = [exe, *argv[1:]]
    else:  # approved by a human who saw this exact string
        cmdline = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    try:
        rc, stdout, stderr = procs.run(cmdline, cwd, timeout)
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout:.0f}s"
    except procs.KillSwitchEngaged:
        return "ERROR: the kill switch is engaged"
    except OSError as e:
        return f"ERROR: cannot run command: {e.strerror or type(e).__name__}"
    out = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
    return cap(f"exit={rc}\n{out}")


register(shell, ToolMeta(tier="T2", side_effects=True, risk="high", untrusted_output=True, tier_fn=_tier,
                         preview_fn=lambda a: f"Run in PowerShell (workspace):\n{a.get('command', '')}"))
