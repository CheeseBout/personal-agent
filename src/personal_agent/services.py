"""Lazily built shared services (meter, embedder, RAG index, memory). Tests call reset_services()."""

from __future__ import annotations

from functools import lru_cache

from personal_agent.memory.store import MemoryStore
from personal_agent.observability.usage import Meter
from personal_agent.rag.embed import Embedder, EmbeddingUnavailable, build_embedder
from personal_agent.rag.index import RagIndex
from personal_agent.scheduler import Scheduler
from personal_agent.settings.settings import data_dir, load_settings


@lru_cache(maxsize=1)
def meter() -> Meter:
    return Meter(data_dir() / "usage.db", load_settings().pricing)


@lru_cache(maxsize=1)
def embedder() -> Embedder:
    s = load_settings()

    def guard() -> None:  # same quota protection as LLM calls: no paid call once the daily cap is hit
        if r := meter().daily_exceeded(s.budgets):
            raise EmbeddingUnavailable(f"daily usage cap reached ({r})")

    def on_usage(model: str, tokens: int) -> None:
        meter().record(model, tokens, 0)

    return build_embedder(s.rag.embedding, guard, on_usage)


@lru_cache(maxsize=1)
def rag() -> RagIndex:
    c = load_settings().rag
    return RagIndex(data_dir() / "rag.db", embedder(), c.chunk_chars, c.overlap_chars)


@lru_cache(maxsize=1)
def memory() -> MemoryStore:
    return MemoryStore(data_dir() / "memory.db", embedder(), load_settings().memory.half_life_days)


@lru_cache(maxsize=1)
def scheduler() -> Scheduler:
    return Scheduler(data_dir() / "scheduler.db", load_settings().scheduler)


def reset_services() -> None:
    for f in (meter, embedder, rag, memory, scheduler):
        f.cache_clear()
