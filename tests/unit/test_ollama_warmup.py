from personal_agent.llm import warmup
from personal_agent.settings.settings import ModelSpec, Settings


def _settings(embed_provider: str = "ollama") -> Settings:
    s = Settings()
    s.models = {
        "classifier": ModelSpec(provider="ollama", model="gemma4:e2b"),
        "vision": ModelSpec(provider="ollama", model="gemma4:e2b"),
        "fast": ModelSpec(provider="google", model="gemini-3.5-flash-lite"),
    }
    s.rag.embedding.provider = embed_provider
    s.rag.embedding.model = "bge-m3"
    return s


def test_picks_each_ollama_model_once_and_the_embedder():
    assert warmup.ollama_models(_settings()) == [("gemma4:e2b", False), ("bge-m3", True)]


def test_cloud_embedder_is_not_warmed():
    assert warmup.ollama_models(_settings("google")) == [("gemma4:e2b", False)]


def test_load_is_best_effort(monkeypatch):
    def boom(*a, **k):
        raise warmup.httpx.ConnectError("ollama is not running")

    monkeypatch.setattr(warmup.httpx, "post", boom)
    warmup._load("http://localhost:11434", "bge-m3", True)  # must not raise
