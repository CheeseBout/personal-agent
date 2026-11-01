import pytest
from langchain_core.runnables import RunnableWithFallbacks
from langchain_openai import ChatOpenAI

from personal_agent.llm import factory
from personal_agent.llm.factory import MissingSecretError, build_chat_model, get_chat_model
from personal_agent.observability.logging import redact_processor
from personal_agent.settings.settings import ModelRef, ModelSpec, Settings


def test_missing_key_raises_without_leaking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "get_secret", lambda n: None)
    with pytest.raises(MissingSecretError, match="OPENAI_API_KEY"):
        build_chat_model(ModelRef(provider="openai", model="m"))


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError):
        build_chat_model(ModelRef(provider="nope", model="m"))


def test_openrouter_uses_openai_compatible_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "get_secret", lambda n: "k")
    m = build_chat_model(ModelRef(provider="openrouter", model="x/y"))
    assert isinstance(m, ChatOpenAI) and m.openai_api_base == factory.OPENROUTER_URL


def test_fallbacks_attached_only_when_keys_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings(models={"worker": ModelSpec(
        provider="anthropic", model="a", fallbacks=[ModelRef(provider="openai", model="o")]
    )})
    monkeypatch.setattr(factory, "get_secret", lambda n: "k")
    assert isinstance(get_chat_model("worker", settings=s), RunnableWithFallbacks)
    monkeypatch.setattr(factory, "get_secret", lambda n: "k" if n.startswith("ANTHROPIC") else None)
    assert not isinstance(get_chat_model("worker", settings=s), RunnableWithFallbacks)


async def test_chain_falls_through_to_fallback_when_primary_is_out_of_credit(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    def out_of_credit(_: object) -> AIMessage:
        raise RuntimeError("Your credit balance is too low to access the Anthropic API")

    def fake_build(ref: ModelRef, spec: ModelSpec | None = None, retries: int = 3) -> RunnableLambda:  # type: ignore[type-arg]
        if ref.provider == "anthropic":
            return RunnableLambda(out_of_credit)
        if ref.provider == "google":
            raise MissingSecretError("no google key")  # unusable fallbacks are skipped, not fatal
        return RunnableLambda(lambda _: AIMessage(f"from {ref.provider}"))

    monkeypatch.setattr(factory, "build_chat_model", fake_build)
    monkeypatch.setattr(factory, "get_secret", lambda n: "k")  # keys exist; the google fallback then fails when it is first built
    s = Settings(models={"worker": ModelSpec(provider="anthropic", model="a", fallbacks=[
        ModelRef(provider="google", model="g"), ModelRef(provider="openai", model="o")])})
    assert (await get_chat_model("worker", settings=s).ainvoke("hi")).content == "from openai"


def test_log_redaction() -> None:
    out = redact_processor(None, "info", {
        "api_key": "sk-abcdefgh12345", "event": "call with Bearer abc.def", "nested": {"token": "t", "ok": "sk-zzzzzzzz99"},
    })
    flat = str(out)
    assert "abcdefgh" not in flat and "abc.def" not in flat and "zzzzzzzz" not in flat
    assert out["api_key"] == "***" and out["nested"]["token"] == "***"
