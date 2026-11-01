"""Per-task budgets and no-progress detection."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from personal_agent.settings.settings import Budgets


def new_used() -> dict[str, Any]:
    return {"steps": 0, "tool_calls": 0, "tokens": 0, "cost": 0.0, "fails": 0, "verifies": 0,
            "started": time.time(), "sigs": [], "worker_steps": 0}


def exceeded(used: dict[str, Any], b: Budgets) -> str | None:
    checks = [
        ("steps", used["steps"] >= b.max_steps),
        ("tool_calls", used["tool_calls"] >= b.max_tool_calls),
        ("seconds", time.time() - used["started"] >= b.max_seconds),
        ("tokens", used["tokens"] >= b.max_tokens),
        ("cost", used["cost"] >= b.max_cost_usd),
        ("failures", used["fails"] >= b.max_consecutive_failures),
        ("verifications", used["verifies"] > b.max_verifications),
    ]
    return next((name for name, hit in checks if hit), None)


def signature(call: dict[str, Any]) -> str:
    return str(call["name"]) + json.dumps(call.get("args", {}), sort_keys=True, default=str)


def repeating(sigs: list[str], times: int = 3, window: int = 6) -> bool:
    """Same tool call made `times` times within the last `window` calls."""
    recent = Counter(sigs[-window:])
    return bool(recent) and max(recent.values()) >= times


def is_failure(m: ToolMessage) -> bool:
    c = m.content if isinstance(m.content, str) else str(m.content)
    return m.status == "error" or c.startswith(("ERROR", "DENIED"))


def charge(used: dict[str, Any], raw: AIMessage, cost: float) -> dict[str, Any]:
    u = dict(used)
    meta: dict[str, Any] = dict(raw.usage_metadata or {})
    u["tokens"] += int(meta.get("input_tokens", 0)) + int(meta.get("output_tokens", 0))
    u["cost"] += cost
    return u
