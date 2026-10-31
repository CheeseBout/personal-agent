"""Local daemon API: token-protected WebSocket that streams graph events."""

from __future__ import annotations

import asyncio
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

from personal_agent.graph.brain import Brain
from personal_agent.graph.builder import build_graph
from personal_agent.graph.context import text_of
from personal_agent.llm.errors import classify_provider_error
from personal_agent.llm.factory import MissingSecretError
from personal_agent.observability.logging import redact, redact_obj
from personal_agent.observability.usage import Meter
from personal_agent.settings.secrets import get_api_token
from personal_agent.settings.settings import data_dir, load_settings, set_agent_name

log = structlog.get_logger()
_THREAD_ID = re.compile(r"^[\w\-]{1,64}$")
_LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$")
MAX_TEXT = 100_000


def authorized(headers: Mapping[str, str], token: str) -> bool:
    # Browsers always send Origin; a non-local one means a web page is poking the daemon.
    origin = headers.get("origin")
    if origin is not None and not _LOCAL_ORIGIN.match(origin):
        return False
    given = headers.get("authorization", "")
    return secrets.compare_digest(given.encode(), f"Bearer {token}".encode())


def create_app(brain: Brain | None = None, db_path: Path | None = None, token: str | None = None) -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = db_path or data_dir() / "checkpoints.db"
        app.state.meter = Meter(data_dir() / "usage.db", settings.pricing)
        async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
            app.state.graph = build_graph(saver, brain, settings, app.state.meter)
            app.state.token = token or get_api_token()
            # One task at a time: keeps the daily cap exact (no parallel check-then-spend race) and
            # avoids two runs interleaving on one checkpointed thread. Parallelism arrives in Phase 5.
            app.state.turn_lock = asyncio.Lock()
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
                kind = msg.get("type", "user") if isinstance(msg, dict) else None
                if kind == "usage":
                    await ws.send_json({"type": "usage", "today": app.state.meter.today(),
                                        "caps": {"tokens": settings.budgets.daily_max_tokens,
                                                 "cost_usd": settings.budgets.daily_max_cost_usd}})
                elif kind == "set_agent_name":
                    await rename(ws, msg)
                else:
                    async with app.state.turn_lock:
                        await run_turn(ws, app.state.graph, msg)
        except WebSocketDisconnect:
            return

    async def rename(ws: WebSocket, msg: dict[str, Any]) -> None:
        try:
            set_agent_name(settings, str(msg.get("name", "")))
        except ValueError as e:
            return await ws.send_json({"type": "error", "code": "bad_request", "message": str(e)})
        except OSError:
            return await ws.send_json({"type": "error", "code": "internal", "message": "could not save the name"})
        await ws.send_json({"type": "agent_name", "name": settings.agent.name})

    async def run_turn(ws: WebSocket, graph: Any, msg: Any) -> None:
        text = msg.get("text") if isinstance(msg, dict) else None
        tid = msg.get("thread_id", "default") if isinstance(msg, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            return await ws.send_json({"type": "error", "code": "bad_request", "message": "invalid text"})
        if not isinstance(tid, str) or not _THREAD_ID.match(tid):
            return await ws.send_json({"type": "error", "code": "bad_request", "message": "invalid thread_id"})
        # Budgets stop runs in code; this is only a backstop against a routing bug.
        config = {"configurable": {"thread_id": tid}, "recursion_limit": settings.budgets.max_steps * 8 + 40}
        final = ""
        try:
            async for chunk in graph.astream({"messages": [HumanMessage(text)]}, config, stream_mode="updates"):
                for node, update in chunk.items():
                    if not update:
                        continue
                    if node == "classify":
                        await ws.send_json({"type": "intent", **{k: update["intent"][k] for k in
                                            ("intent", "complexity", "needs_plan", "risk_level", "confidence")}})
                    elif node == "plan" and update.get("goal"):
                        g = update["goal"]
                        await ws.send_json({"type": "plan", "objective": g["objective"],
                                            "success_criteria": g["success_criteria"],
                                            "steps": [s["description"] for s in g["steps"]]})
                    elif node == "verify":
                        await ws.send_json({"type": "verdict", "status": update.get("verdict"),
                                            "notes": redact(update.get("notes", ""))})
                    if node in ("respond", "clarify"):
                        final = update.get("final", "")
                    for m in update.get("messages", []):
                        if isinstance(m, AIMessage):
                            for c in m.tool_calls:
                                await ws.send_json({"type": "tool_call", "name": c["name"],
                                                    "args": redact_obj(c["args"])})
                        elif isinstance(m, ToolMessage):
                            await ws.send_json({"type": "tool_result", "name": m.name, "status": m.status,
                                                "content": redact(text_of(m.content))[:2000]})
            state = await graph.aget_state(config)
            u = state.values.get("used", {})
            used = {k: u.get(k) for k in ("steps", "tool_calls", "tokens", "cost")}
            await ws.send_json({"type": "final", "text": final, "used": used,
                                "stopped": state.values.get("stop_reason") or None})
        except GraphRecursionError:
            await ws.send_json({"type": "error", "code": "budget", "message": "stopped by the recursion backstop"})
        except MissingSecretError as e:
            await ws.send_json({"type": "error", "code": "no_api_key", "message": str(e)})
        except Exception as e:  # last resort: report to the client, keep the daemon alive
            log.exception("turn_failed", thread_id=tid)
            code, message = classify_provider_error(e) or ("internal", type(e).__name__)
            await ws.send_json({"type": "error", "code": code, "message": message})

    return app
