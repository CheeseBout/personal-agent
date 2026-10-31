"""Model access for graph nodes, injectable for tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from personal_agent.llm.factory import get_chat_model
from personal_agent.settings.settings import Settings


class Brain(Protocol):
    def chat(self, role: str, tools: Sequence[BaseTool] = ()) -> Runnable[Any, Any]: ...
    def structured(self, role: str, schema: type[BaseModel]) -> Runnable[Any, Any]: ...
    def model_name(self, role: str) -> str: ...


class LiveBrain:
    """Builds provider models lazily (the daemon starts without keys) and caches them."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._cache: dict[tuple[str, str], Runnable[Any, Any]] = {}

    def chat(self, role: str, tools: Sequence[BaseTool] = ()) -> Runnable[Any, Any]:
        k = (role, "chat")
        if k not in self._cache:
            self._cache[k] = get_chat_model(role, tools, self._s)
        return self._cache[k]

    def structured(self, role: str, schema: type[BaseModel]) -> Runnable[Any, Any]:
        k = (role, schema.__name__)
        if k not in self._cache:
            self._cache[k] = get_chat_model(role, settings=self._s, schema=schema)
        return self._cache[k]

    def model_name(self, role: str) -> str:
        return self._s.models[role].model
