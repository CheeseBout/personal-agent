"""File tools (scoped by policy path rules)."""

from __future__ import annotations

import os
from pathlib import Path

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.tools.registry import cap, policy, register


def workspace() -> Path:
    allow = policy().cfg.get("paths", {}).get("allow", [])
    return Path(os.path.expandvars(allow[0])) if allow else Path.cwd()


@tool
def file_read(path: str, max_chars: int = 20_000) -> str:
    """Read a UTF-8 text file. Relative paths are resolved inside the workspace folder.
    Đọc nội dung một tệp văn bản (đường dẫn tương đối tính từ thư mục workspace)."""
    p = Path(os.path.expandvars(path))
    if not p.is_absolute():
        p = workspace() / p
    d = policy().check_path(p)
    if not d.allowed:
        return f"ERROR: access denied: {d.reason}"
    try:
        if not p.is_file():
            return "ERROR: file not found"
        with p.open("rb") as f:
            data = f.read(max_chars * 4 + 1)  # bound memory on huge files
    except OSError as e:
        return f"ERROR: cannot read file: {e.strerror or type(e).__name__}"
    return cap(data.decode("utf-8", errors="replace"), min(max_chars, 20_000))


register(file_read, ToolMeta(tier="T0"))
