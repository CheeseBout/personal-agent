"""Provider factory with role-based routing and per-role fallback chain.

Provider SDKs are imported lazily so an idle daemon only pays for what it uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from personal_agent.settings.secrets import get_secret
from personal_agent.settings.settings import ModelRef, ModelSpec, Settings, load_settings

KEY_NAMES = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
OPENROUTER_URL = "https://openrouter.ai/api/v1"


class MissingSecretError(RuntimeError):
    """No API key configured for a provider (message never contains a secret)."""


def build_chat_model(ref: ModelRef, spec: ModelSpec | None = None) -> BaseChatModel:
    if ref.provider not in KEY_NAMES:
        raise ValueError(f"unknown provider '{ref.provider}' (expected one of {sorted(KEY_NAMES)})")
    key = get_secret(KEY_NAMES[ref.provider])
    if not key:
        raise MissingSecretError(
            f"No API key for provider '{ref.provider}'. Store {KEY_NAMES[ref.provider]} in Windows "
            "Credential Manager (service 'personal-agent') or set it as an environment variable."
        )
    kw: dict[str, Any] = {"model": ref.model, "api_key": key, "max_retries": 3}
    if spec is not None:
        kw["timeout"] = spec.timeout_s
        if spec.temperature is not None:
            kw["temperature"] = spec.temperature
        if spec.max_tokens is not None:
            kw["max_tokens"] = spec.max_tokens
    if ref.provider in ("openai", "openrouter"):
        from langchain_openai import ChatOpenAI

        if ref.provider == "openrouter":
            kw["base_url"] = OPENROUTER_URL
        return ChatOpenAI(**kw)
    if ref.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(**kw)
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(**kw)


def get_chat_model(
    role: str, tools: Sequence[Any] = (), settings: Settings | None = None
) -> Runnable[Any, Any]:
    """Model for `role`, tools bound, with fallbacks (fallbacks lacking a key are skipped).

    Tools are bound before fallbacks are attached because RunnableWithFallbacks has no bind_tools.
    """
    spec = (settings or load_settings()).models[role]

    def make(ref: ModelRef) -> Runnable[Any, Any]:
        m = build_chat_model(ref, spec)
        return m.bind_tools(list(tools)) if tools else m

    primary = make(spec)
    fallbacks: list[Runnable[Any, Any]] = []
    for ref in spec.fallbacks:
        try:
            fallbacks.append(make(ref))
        except MissingSecretError:
            continue
    return primary.with_fallbacks(fallbacks) if fallbacks else primary
