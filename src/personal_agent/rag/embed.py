"""Embedding providers. Default: Google gemini-embedding-001 (multilingual, vi + en). Every call is guarded by
the daily token cap and recorded in the usage meter, like LLM calls."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any, Protocol

from personal_agent.settings.secrets import get_secret
from personal_agent.settings.settings import EmbeddingCfg

BATCH = 64
_LOCK = threading.Lock()


class EmbeddingUnavailable(RuntimeError):
    """No usable embedding provider right now (no key, outage, daily cap)."""


class Embedder(Protocol):
    id: str
    dim: int

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]: ...


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _retry(fn: Callable[[], Any], tries: int = 3) -> Any:
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            transient = any(s in str(e) for s in ("503", "429", "UNAVAILABLE", "timed out", "Timeout"))
            if i == tries - 1 or not transient:
                raise EmbeddingUnavailable(f"{type(e).__name__}: {str(e)[:160]}") from e
            time.sleep(2**i)
    raise AssertionError("unreachable")


class _Metered:
    """Shared guard/usage plumbing (daily cap checked BEFORE the paid call; usage recorded after)."""

    def __init__(self, cfg: EmbeddingCfg, guard: Callable[[], None] | None, on_usage: Callable[[str, int], None] | None) -> None:
        self.cfg, self.dim = cfg, cfg.dim
        self.id = f"{cfg.provider}:{cfg.model}:{cfg.dim}"
        self._guard, self._usage = guard, on_usage

    def _account(self, texts: list[str]) -> None:
        if self._usage:
            self._usage(self.cfg.model, sum(len(t) for t in texts) // 4)  # ~4 chars/token estimate

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            with _LOCK:  # check-cap -> paid call -> record is one step, so the watcher and tools cannot both slip past the cap
                if self._guard:
                    self._guard()
                out += [_normalise(v) for v in _retry(partial(self._call, batch, query))]
                self._account(batch)
        return out

    def _call(self, batch: list[str], query: bool) -> list[list[float]]:
        raise NotImplementedError


class GoogleEmbedder(_Metered):
    _client: Any = None

    def _call(self, batch: list[str], query: bool) -> list[list[float]]:
        from google import genai
        from google.genai import types

        key = get_secret("GOOGLE_API_KEY")
        if not key:
            raise EmbeddingUnavailable("GOOGLE_API_KEY is not set")
        if self._client is None:
            # keep a reference: a temporary Client is garbage-collected (and its HTTP session closed) mid-request
            self._client = genai.Client(api_key=key)
        r = self._client.models.embed_content(
            model=self.cfg.model, contents=batch,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
                                            output_dimensionality=self.dim))
        return [list(e.values or []) for e in (r.embeddings or [])]


class OpenAIEmbedder(_Metered):
    def _call(self, batch: list[str], query: bool) -> list[list[float]]:
        from openai import OpenAI

        key = get_secret("OPENAI_API_KEY")
        if not key:
            raise EmbeddingUnavailable("OPENAI_API_KEY is not set")
        r = OpenAI(api_key=key).embeddings.create(model=self.cfg.model, input=batch, dimensions=self.dim)
        return [d.embedding for d in r.data]


class OllamaEmbedder(_Metered):
    """Local Ollama (e.g. bge-m3, dim 1024). Free: no usage recorded. Needs the Ollama daemon running."""

    def _call(self, batch: list[str], query: bool) -> list[list[float]]:
        import httpx

        host = (get_secret("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        r = httpx.post(f"{host}/api/embed", json={"model": self.cfg.model, "input": batch}, timeout=120)
        r.raise_for_status()
        vecs: list[list[float]] = r.json()["embeddings"]
        if vecs and len(vecs[0]) != self.dim:
            raise EmbeddingUnavailable(f"{self.cfg.model} returned dim {len(vecs[0])}, config says {self.dim}")
        return vecs

    def _account(self, texts: list[str]) -> None:
        return  # local: free


class LocalEmbedder(_Metered):
    """fastembed (ONNX) - optional extra `local-embeddings`; the model is downloaded on first use."""

    _model: Any = None

    def _call(self, batch: list[str], query: bool) -> list[list[float]]:
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise EmbeddingUnavailable("install the 'local-embeddings' extra to use local embeddings") from e
        if self._model is None:
            self._model = TextEmbedding(self.cfg.model)
        return [list(map(float, v)) for v in self._model.embed(batch)]

    def _account(self, texts: list[str]) -> None:
        return  # local: free


def build_embedder(cfg: EmbeddingCfg, guard: Callable[[], None] | None = None,
                   on_usage: Callable[[str, int], None] | None = None) -> Embedder:
    kinds = {"google": GoogleEmbedder, "openai": OpenAIEmbedder, "ollama": OllamaEmbedder, "local": LocalEmbedder}
    if cfg.provider not in kinds:
        raise ValueError(f"unknown embedding provider '{cfg.provider}'")
    return kinds[cfg.provider](cfg, guard, on_usage)
