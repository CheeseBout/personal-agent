"""Central tool registry: every tool is registered together with its policy metadata."""

from __future__ import annotations

from functools import lru_cache

from langchain_core.tools import BaseTool

from personal_agent.observability.logging import redact
from personal_agent.policy.engine import PolicyEngine, ToolMeta

MAX_OUTPUT_CHARS = 20_000

_TOOLS: dict[str, tuple[BaseTool, ToolMeta]] = {}


@lru_cache(maxsize=1)
def policy() -> PolicyEngine:
    return PolicyEngine()


def register(tool: BaseTool, meta: ToolMeta) -> BaseTool:
    _TOOLS[tool.name] = (tool, meta)
    return tool


def meta_for(name: str) -> ToolMeta | None:
    entry = _TOOLS.get(name)
    return entry[1] if entry else None


def load_tools() -> list[BaseTool]:
    from personal_agent.tools import (  # noqa: F401  (importing registers the tools)
        artifacts,
        calendar_tools,
        clipboard_tools,
        email_tools,
        files,
        fs_extra,
        knowledge_tools,
        python_exec,
        sandbox_tools,
        schedule_tools,
        screen_tools,
        shell,
        web,
        web_tools,
    )

    return [t for t, _ in _TOOLS.values()]


def cap(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    # shortcut: plain truncation, no artifact handle yet (Phase 2 adds offloading of big outputs)
    text = redact(text)  # never feed recognisable API keys/bearer tokens back to the model
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
