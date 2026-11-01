"""Langfuse tracing (optional) + the per-run config that carries every callback.

Off unless `observability.langfuse_enabled` AND both Langfuse keys exist; every failure degrades to "no tracing".
All trace payloads are masked with the same redaction used for logs and the audit log.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.callbacks import BaseCallbackHandler

from personal_agent.observability.logging import redact_obj
from personal_agent.observability.metrics import Metrics, MetricsCallback
from personal_agent.settings.secrets import get_secret
from personal_agent.settings.settings import Settings

log = structlog.get_logger()
DEFAULT_URL = "https://cloud.langfuse.com"


class Tracing:
    def __init__(self, settings: Settings, metrics: Metrics | None) -> None:
        self._cb: list[BaseCallbackHandler] = []
        self._client: Any = None
        self._public_key: str | None = None
        if metrics is not None and settings.observability.metrics_enabled:
            self._cb.append(MetricsCallback(metrics))
        if settings.observability.langfuse_enabled:
            self._init_langfuse()

    @property
    def langfuse_on(self) -> bool:
        return self._client is not None

    def _init_langfuse(self) -> None:
        pk, sk = get_secret("LANGFUSE_PUBLIC_KEY"), get_secret("LANGFUSE_SECRET_KEY")
        if not (pk and sk):
            log.warning("langfuse_disabled", reason="LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
            return
        try:
            from langfuse import Langfuse

            self._client = Langfuse(public_key=pk, secret_key=sk, mask=lambda data, **_: redact_obj(data),
                                    base_url=get_secret("LANGFUSE_BASE_URL") or get_secret("LANGFUSE_HOST") or DEFAULT_URL)
            self._public_key = pk
            log.info("langfuse_enabled")
        except Exception as e:  # noqa: BLE001 - missing extra, bad config...: the agent must still run
            log.warning("langfuse_disabled", reason=type(e).__name__)
            self._client = None

    def run_config(self, thread_id: str, recursion_limit: int, tags: tuple[str, ...] = ()) -> dict[str, Any]:
        """RunnableConfig for one graph run: thread, callbacks, and Langfuse session / tags."""
        cbs = list(self._cb)
        meta: dict[str, Any] = {}
        if self._client is not None:
            from langfuse.langchain import CallbackHandler

            cbs.append(CallbackHandler(public_key=self._public_key))
            meta = {"langfuse_session_id": thread_id, "langfuse_tags": ["personal-agent", *tags]}
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit,
                "callbacks": cbs, "metadata": meta}

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception:  # noqa: BLE001
                log.warning("langfuse_flush_failed")
