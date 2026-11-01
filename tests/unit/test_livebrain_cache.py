from typing import Any

from langchain_core.tools import tool

from personal_agent.graph import brain as brain_mod
from personal_agent.graph.brain import LiveBrain
from personal_agent.settings.settings import load_settings


@tool
def alpha() -> str:
    """a"""
    return "a"


@tool
def beta() -> str:
    """b"""
    return "b"


def test_models_are_cached_per_role_AND_tool_set(monkeypatch: Any) -> None:
    """Regression: the cache ignored the tools, so a 3-tool worker got the main loop's model bound to ALL tools."""
    built: list[tuple[str, list[str]]] = []

    def fake_get(role: str, tools: Any = (), settings: Any = None, schema: Any = None) -> object:
        built.append((role, sorted(t.name for t in tools)))
        return object()

    monkeypatch.setattr(brain_mod, "get_chat_model", fake_get)
    b = LiveBrain(load_settings())
    main_loop, worker = b.chat("fast", [alpha, beta]), b.chat("fast", [alpha])
    assert main_loop is not worker and built == [("fast", ["alpha", "beta"]), ("fast", ["alpha"])]
    assert b.chat("fast", [beta, alpha]) is main_loop and b.chat("fast", [alpha]) is worker  # order-insensitive, still cached
    assert len(built) == 2
