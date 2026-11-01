"""Fallback models are built only when the primary fails (a provider SDK costs 40-90 MB of RAM); keyless fallbacks are skipped."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from personal_agent.llm import factory
from personal_agent.settings.settings import ModelRef, load_settings


def boom(_: Any) -> Any:
    raise RuntimeError("provider down")


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_build(ref: ModelRef, spec: Any = None, retries: int = 3) -> Any:
        calls.append(f"{ref.provider}:{ref.model}")
        return RunnableLambda(boom) if ref.provider == "anthropic" else RunnableLambda(lambda _: AIMessage(f"from {ref.provider}"))

    monkeypatch.setattr(factory, "build_chat_model", fake_build)
    monkeypatch.setattr(factory, "get_secret", lambda n: "k" if n in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY") else None)  # no OpenAI key
    return calls


def test_only_the_primary_is_built_until_it_fails(built: list[str]) -> None:
    s = load_settings()
    m = factory.get_chat_model("planner", settings=s)  # primary anthropic; fallbacks: google, openai(no key)
    assert built == ["anthropic:claude-haiku-5-5"]
    out = m.invoke("hi")
    assert out.content == "from google"
    assert built == ["anthropic:claude-haiku-5-5", "google:gemini-3.5-flash-lite"]  # the keyless OpenAI fallback was never built


async def test_the_lazy_fallback_works_asynchronously_and_is_built_once(built: list[str]) -> None:
    m = factory.get_chat_model("planner", settings=load_settings())
    assert (await m.ainvoke("a")).content == "from google"
    assert (await m.ainvoke("b")).content == "from google"
    assert built.count("google:gemini-3.5-flash-lite") == 1


def test_a_healthy_primary_never_builds_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(factory, "build_chat_model", lambda ref, spec=None, retries=3: calls.append(ref.provider) or RunnableLambda(lambda _: AIMessage("ok")))
    monkeypatch.setattr(factory, "get_secret", lambda n: "k")
    assert factory.get_chat_model("planner", settings=load_settings()).invoke("x").content == "ok"
    assert calls == ["anthropic"]


def test_ollama_needs_no_key() -> None:
    assert factory._usable(ModelRef(provider="ollama", model="x")) and not factory._usable(ModelRef(provider="nope", model="x"))
