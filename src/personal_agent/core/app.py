"""Local daemon API: token-protected WebSocket that streams graph events."""

from __future__ import annotations

import re
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from personal_agent.graph.builder import ModelFactory, build_graph
from personal_agent.llm.factory import MissingSecretError
from personal_agent.observability.logging import redact, redact_obj
from personal_agent.settings.secrets import get_api_token
from personal_agent.settings.settings import data_dir, load_settings

log = structlog.get_logger()
_THREAD_ID = re.compile(r"^[\w\-]{1,64}$")
_LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$")
MAX_TEXT = 100_000


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def authorized(headers: Mapping[str, str], token: str) -> bool:
    # Browsers always send Origin; a non-local one means a web page is poking the daemon.
    origin = headers.get("origin")
    if origin is not None and not _LOCAL_ORIGIN.match(origin):
        return False
    given = headers.get("authorization", "")
    return secrets.compare_digest(given.encode(), f"Bearer {token}".encode())


def create_app(
    model_factory: ModelFactory | None = None, db_path: Path | None = None, token: str | None = None
) -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = db_path or data_dir() / "checkpoints.db"
        async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
            app.state.graph = build_graph(saver, model_factory, settings)
            app.state.token = token or get_api_token()
            log.info("daemon_ready")
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        if not authorized(ws.headers, app.state.token):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                msg = await ws.receive_json()
                await run_turn(ws, app.state.graph, msg)
        except WebSocketDisconnect:
            return

    async def run_turn(ws: WebSocket, graph: Any, msg: Any) -> None:
        text = msg.get("text") if isinstance(msg, dict) else None
        tid = msg.get("thread_id", "default") if isinstance(msg, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            return await ws.send_json({"type": "error", "code": "bad_request", "message": "invalid text"})
        if not isinstance(tid, str) or not _THREAD_ID.match(tid):
            return await ws.send_json({"type": "error", "code": "bad_request", "message": "invalid thread_id"})
        config = {"configurable": {"thread_id": tid}, "recursion_limit": settings.budgets.max_steps}
        final = ""
        try:
            async for chunk in graph.astream(
                {"messages": [HumanMessage(text)]}, config, stream_mode="updates"
            ):
                for update in chunk.values():
                    for m in (update or {}).get("messages", []):
                        if isinstance(m, AIMessage):
                            for c in m.tool_calls:
                                await ws.send_json({"type": "tool_call", "name": c["name"], "args": redact_obj(c["args"])})
                            if not m.tool_calls:
                                final = text_of(m.content)
                        elif isinstance(m, ToolMessage):
                            await ws.send_json({
                                "type": "tool_result", "name": m.name, "status": m.status,
                                "content": redact(text_of(m.content))[:2000],
                            })
        except GraphRecursionError:
            return await ws.send_json({
                "type": "error", "code": "budget",
                "message": f"stopped after {settings.budgets.max_steps} steps without finishing",
            })
        except MissingSecretError as e:
            return await ws.send_json({"type": "error", "code": "no_api_key", "message": str(e)})
        except Exception as e:  # last resort: report to the client, keep the daemon alive
            log.exception("turn_failed", thread_id=tid)
            return await ws.send_json({"type": "error", "code": "internal", "message": type(e).__name__})
        await ws.send_json({"type": "final", "text": final})

    return app
