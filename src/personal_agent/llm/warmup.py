"""Preload the local Ollama models at start-up so the first request does not pay the load time.

Measured 2026-11-10: a cold gemma4:e2b took 25 s (timing out the 30 s classifier call), a cold bge-m3 7-43 s.
Best effort: Ollama off or a model missing is logged and ignored. Keeping them loaded afterwards is the
server's job (OLLAMA_KEEP_ALIVE); this only does the first load.
"""

from __future__ import annotations

import threading

import httpx
import structlog

from personal_agent.settings.secrets import get_secret
from personal_agent.settings.settings import Settings

log = structlog.get_logger()


def ollama_models(s: Settings) -> list[tuple[str, bool]]:
    """(model, is_embedding) for every Ollama model the config uses as a primary (fallbacks load on demand)."""
    chat = {m.model for m in s.models.values() if m.provider == "ollama"}
    out = [(m, False) for m in sorted(chat)]
    if s.rag.embedding.provider == "ollama":
        out.append((s.rag.embedding.model, True))
    return out


def _load(host: str, model: str, embedding: bool) -> None:
    # An empty generate / embed request loads the model without producing anything.
    path, body = ("/api/embed", {"model": model, "input": ""}) if embedding else ("/api/generate", {"model": model})
    try:
        r = httpx.post(host + path, json={**body, "keep_alive": -1}, timeout=300)
        r.raise_for_status()
        log.info("ollama_warm", model=model, seconds=round(r.elapsed.total_seconds(), 1))
    except httpx.HTTPError as e:  # Ollama not running, model not pulled...
        log.warning("ollama_warm_failed", model=model, error=type(e).__name__)


def warm_ollama(s: Settings) -> None:
    """Start one daemon thread per model and return at once; start-up is not delayed."""
    host = (get_secret("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    for model, embedding in ollama_models(s):
        threading.Thread(target=_load, args=(host, model, embedding), daemon=True, name=f"warm-{model}").start()
