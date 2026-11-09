"""Provider factory with role-based routing and per-role fallback chain.

Provider SDKs are imported lazily so an idle daemon only pays for what it uses.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from pydantic import BaseModel

from personal_agent.settings.secrets import get_secret
from personal_agent.settings.settings import ModelRef, ModelSpec, Settings, load_settings

KEY_NAMES = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OLLAMA_URL = "http://localhost:11434"  # override with OLLAMA_HOST; local, so no API key


class MissingSecretError(RuntimeError):
    """No API key configured for a provider (message never contains a secret)."""


def build_chat_model(ref: ModelRef, spec: ModelSpec | None = None, retries: int = 3) -> BaseChatModel:
    if ref.provider != "ollama" and ref.provider not in KEY_NAMES:
        raise ValueError(f"unknown provider '{ref.provider}' (expected one of {sorted([*KEY_NAMES, 'ollama'])})")
    key = "ollama" if ref.provider == "ollama" else get_secret(KEY_NAMES[ref.provider])
    if not key:
        raise MissingSecretError(
            f"No API key for provider '{ref.provider}'. Store {KEY_NAMES[ref.provider]} in Windows "
            "Credential Manager (service 'personal-agent') or set it as an environment variable."
        )
    kw: dict[str, Any] = {"model": ref.model, "api_key": key, "max_retries": retries}
    if spec is not None:
        kw["timeout"] = spec.timeout_s
        if spec.temperature is not None:
            kw["temperature"] = spec.temperature
        if spec.max_tokens is not None:
            kw["max_tokens"] = spec.max_tokens
    if ref.provider in ("openai", "openrouter", "ollama"):
        from langchain_openai import ChatOpenAI

        if ref.provider == "openrouter":
            kw["base_url"] = OPENROUTER_URL
        elif ref.provider == "ollama":  # Ollama's OpenAI-compatible endpoint; no new dependency
            kw["base_url"] = (get_secret("OLLAMA_HOST") or OLLAMA_URL).rstrip("/") + "/v1"
            kw["max_retries"] = 0  # fail fast to the fallback if the daemon is down
            if spec is not None and spec.reasoning_effort:
                kw["reasoning_effort"] = spec.reasoning_effort
        return ChatOpenAI(**kw)
    if ref.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(**kw)
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(**kw)


def _usable(ref: ModelRef) -> bool:
    """Can this provider be called at all (its key exists, or it needs none)?"""
    # unknown provider => False without a lookup: get_secret("") can hit an unnamed Credential Manager entry
    return ref.provider == "ollama" or (ref.provider in KEY_NAMES and bool(get_secret(KEY_NAMES[ref.provider])))


def _lazy(build: Callable[[], Runnable[Any, Any]]) -> Runnable[Any, Any]:
    """A runnable that constructs the real model on first use."""
    cell: list[Runnable[Any, Any]] = []

    def get() -> Runnable[Any, Any]:
        if not cell:
            cell.append(build())
        return cell[0]

    def run(x: Any, config: RunnableConfig) -> Any:
        return get().invoke(x, config)

    async def arun(x: Any, config: RunnableConfig) -> Any:
        return await get().ainvoke(x, config)

    return RunnableLambda(run, afunc=arun)


def get_chat_model(
    role: str,
    tools: Sequence[Any] = (),
    settings: Settings | None = None,
    schema: type[BaseModel] | None = None,
) -> Runnable[Any, Any]:
    """Model for `role` with fallbacks (fallbacks lacking a key are skipped).

    Tools / structured output are applied before fallbacks are attached because
    RunnableWithFallbacks has no bind_tools. With `schema` the runnable returns
    {"raw": AIMessage, "parsed": schema|None, "parsing_error": ...} so usage stays visible.
    """
    spec = (settings or load_settings()).models[role]

    def make(ref: ModelRef, retries: int = 3) -> Runnable[Any, Any]:
        m = build_chat_model(ref, spec, retries)
        if schema is not None:
            # Forced tool-calling is unreliable on current Claude models; use native JSON-schema output.
            method = "json_schema" if ref.provider == "anthropic" else None
            return m.with_structured_output(schema, include_raw=True, **({"method": method} if method else {}))
        return m.bind_tools(list(tools)) if tools else m

    primary = make(spec)
    # Fallbacks are only BUILT when the primary actually fails: importing a provider SDK costs 40-90 MB of RAM and up to a second.
    # (A fallback whose API key is missing is still skipped up front.)

    def fallback(ref: ModelRef) -> Runnable[Any, Any]:
        return _lazy(lambda: make(ref, retries=1))  # fail fast: a hung fallback is worse than an error

    fallbacks = [fallback(ref) for ref in spec.fallbacks if _usable(ref)]
    return primary.with_fallbacks(fallbacks) if fallbacks else primary
