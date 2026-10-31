"""Scheduling tools. Creating a schedule is T2: it makes the agent act later without you watching."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from langchain_core.tools import tool

from personal_agent import services
from personal_agent.policy.engine import ToolMeta
from personal_agent.tools.registry import register


def _when(a: dict[str, Any]) -> float:
    if a.get("in_minutes") is not None:
        return time.time() + float(a["in_minutes"]) * 60
    return datetime.fromisoformat(str(a["at"])).astimezone().timestamp()  # naive = local time


def _preview(a: dict[str, Any]) -> str:
    try:
        when = datetime.fromtimestamp(_when(a)).strftime("%Y-%m-%d %H:%M")
    except (KeyError, ValueError, TypeError):
        when = "(invalid time)"
    rep = f"\nRepeats every {a['every_minutes']} minutes" if a.get("every_minutes") else ""
    return f"SCHEDULE a task for {when}{rep}\nIt will run unattended through the same safety rules (writes still need your approval).\n\nPrompt:\n{a.get('prompt', '')}"


@tool
def schedule_task(prompt: str, in_minutes: int | None = None, at: str | None = None, every_minutes: int | None = None) -> str:
    """Schedule a prompt to run later (in_minutes, or at = ISO local date-time), optionally repeating every N minutes.
    Needs approval. Hẹn giờ chạy một yêu cầu (một lần hoặc lặp lại); cần người dùng duyệt."""
    if (in_minutes is None) == (at is None):
        return "ERROR: give exactly one of in_minutes or at"
    try:
        jid = services.scheduler().add(prompt, _when({"in_minutes": in_minutes, "at": at}), every_minutes)
    except (ValueError, TypeError) as e:
        return f"ERROR: {e}"
    return f"scheduled as #{jid}"


@tool
def schedule_list() -> str:
    """List active scheduled tasks. Liệt kê các tác vụ đã hẹn giờ."""
    rows = services.scheduler().jobs(only_active=True)
    return "\n".join(
        f"#{r['id']} next {datetime.fromtimestamp(r['next_run']):%Y-%m-%d %H:%M}"
        + (f", every {r['every_s'] // 60} min" if r["every_s"] else "") + f" | last: {r['last_status'] or '-'} | {r['prompt'][:80]}"
        for r in rows) or "no scheduled tasks"


@tool
def schedule_cancel(schedule_id: int) -> str:
    """Cancel a scheduled task. Huỷ một tác vụ đã hẹn giờ."""
    return "cancelled" if services.scheduler().cancel(schedule_id) else "ERROR: no such active schedule"


register(schedule_task, ToolMeta(tier="T2", side_effects=True, risk="high", preview_fn=_preview, attended_only=True))
register(schedule_list, ToolMeta(tier="T0", untrusted_output=True))
register(schedule_cancel, ToolMeta(tier="T1", side_effects=True))
