"""Windows clipboard via PowerShell (no extra dependency). Reading is T0 (and untrusted: it may hold copied web text
or even secrets, which are redacted); writing replaces what the user copied, so it needs approval."""

from __future__ import annotations

import subprocess
from typing import Any

from langchain_core.tools import tool

from personal_agent.policy.engine import ToolMeta
from personal_agent.tools import procs
from personal_agent.tools.files import workspace
from personal_agent.tools.registry import cap, register

UTF8 = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;[Console]::InputEncoding=[Text.Encoding]::UTF8;"
MAX_WRITE = 200_000


def _ps(script: str, data: bytes | None = None) -> tuple[int, bytes, bytes]:
    return procs.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", UTF8 + script], workspace(), 15, data)


@tool
def clipboard_read() -> str:
    """Read the text currently in the clipboard. Đọc văn bản đang có trong clipboard."""
    try:
        rc, out, _ = _ps("Get-Clipboard -Raw")
    except (subprocess.TimeoutExpired, OSError, procs.KillSwitchEngaged) as e:
        return f"ERROR: cannot read the clipboard: {type(e).__name__}"
    text = out.decode("utf-8", errors="replace").rstrip("\r\n")
    return cap("[UNTRUSTED CLIPBOARD CONTENT]\n" + text) if rc == 0 and text else "(the clipboard has no text)"


@tool
def clipboard_write(text: str) -> str:
    """Replace the clipboard text (needs approval). Thay nội dung clipboard (cần duyệt)."""
    if len(text) > MAX_WRITE:
        return f"ERROR: text is longer than {MAX_WRITE} characters"
    try:
        rc, _, err = _ps("Set-Clipboard -Value ([Console]::In.ReadToEnd())", text.encode("utf-8"))
    except (subprocess.TimeoutExpired, OSError, procs.KillSwitchEngaged) as e:
        return f"ERROR: cannot write the clipboard: {type(e).__name__}"
    return f"copied {len(text)} characters to the clipboard" if rc == 0 else f"ERROR: {err.decode(errors='replace')[:120]}"


def _preview(a: dict[str, Any]) -> str:
    return "Replace the clipboard with:\n" + str(a.get("text", ""))[:2000]


register(clipboard_read, ToolMeta(tier="T0", untrusted_output=True))
register(clipboard_write, ToolMeta(tier="T2", side_effects=True, risk="low", preview_fn=_preview))
