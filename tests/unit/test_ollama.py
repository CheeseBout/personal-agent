"""Local Ollama provider (chat via the OpenAI-compatible endpoint, embeddings via /api/embed)."""

from __future__ import annotations

import pytest

from personal_agent.llm.factory import build_chat_model
from personal_agent.rag.embed import EmbeddingUnavailable, OllamaEmbedder, build_embedder
from personal_agent.settings.settings import EmbeddingCfg, ModelRef, load_settings


def test_ollama_chat_needs_no_key_and_points_at_local_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    m = build_chat_model(ModelRef(provider="ollama", model="gemma4:e4b"))
    assert str(m.openai_api_base).startswith("http://localhost:11434/v1")  # type: ignore[attr-defined]


def test_default_config_never_routes_to_sonnet_and_embeds_locally() -> None:
    s = load_settings()
    refs = [r for m in s.models.values() for r in (m, *m.fallbacks)]
    assert not any("sonnet" in r.model for r in refs)
    assert (s.rag.embedding.provider, s.rag.embedding.model) == ("ollama", "bge-m3")


def test_ollama_embedder_normalises_and_checks_dim(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = EmbeddingCfg(provider="ollama", model="bge-m3", dim=3)
    emb = build_embedder(cfg)
    assert isinstance(emb, OllamaEmbedder)

    class R:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict[str, list[list[float]]]:
            return {"embeddings": [[3.0, 4.0, 0.0]]}

    monkeypatch.setattr("httpx.post", lambda *a, **k: R())
    assert emb.embed(["x"])[0] == pytest.approx([0.6, 0.8, 0.0])
    with pytest.raises(EmbeddingUnavailable, match="dim"):
        build_embedder(EmbeddingCfg(provider="ollama", model="bge-m3", dim=5)).embed(["x"])
