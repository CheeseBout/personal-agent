"""Local daemon API: token-protected WebSocket + /kill. Turns run as background tasks so that approvals and
the kill switch can be received while a turn is running or paused."""

from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError

from personal_agent.graph.brain import Brain
from personal_agent.graph.builder import build_graph, resume_command
from personal_agent.graph.context import text_of
from personal_agent.llm.errors import classify_provider_error
from personal_agent.llm.factory import MissingSecretError
from personal_agent.observability.audit import AuditLog
from personal_agent.observability.logging import redact, redact_obj
from personal_agent.observability.usage import Meter
from personal_agent.policy.approvals import ApprovalStore
from personal_agent.policy.killswitch import KillSwitch
from personal_agent.settings.secrets import get_api_token
from personal_agent.settings.settings import data_dir, load_settings, set_agent_name
from personal_agent.tools import procs

log = structlog.get_logger()
_THREAD_ID = re.compile(r"^[\w\-]{1,64}$")
_LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?$")
MAX_TEXT = 100_000
MAX_QUEUED_TURNS = 8
Send = Callable[[dict[str, Any]], Awaitable[None]]


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
        from personal_agent.tools.sandbox_tools import sandbox  # lazy: no Docker work unless used

        s = app.state
        s.meter = Meter(data_dir() / "usage.db", settings.pricing)
        s.audit = AuditLog(data_dir() / "audit.db")
        s.approvals = ApprovalStore(data_dir() / "approvals.db")
        s.kill = KillSwitch()
        s.kill.on_engage(procs.kill_all)  # host subprocesses first: they are the dangerous ones
        s.kill.on_engage(sandbox().stop)
        s.kill.on_engage(s.approvals.kill_all)
        s.kill.on_release(procs.reset)
        s.tasks = set()  # running/queued turn tasks
        s.active_threads = set()
        s.turn_lock = asyncio.Lock()  # one task at a time: exact daily cap, no interleaving on a thread
        s.token = token or get_api_token()
        async with AsyncSqliteSaver.from_conn_string(str(db_path or data_dir() / "checkpoints.db")) as saver:
            s.graph = build_graph(saver, brain, settings, s.meter, s.audit, s.approvals, s.kill)
            log.info("daemon_ready")
            yield
            for t in list(s.tasks):
                t.cancel()
            await asyncio.gather(*s.tasks, return_exceptions=True)
        sandbox().stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def settle(thread_id: str) -> None:
        """Bring a thread to rest after a kill: with the switch engaged nothing executes and no model is called."""
        cfg = {"configurable": {"thread_id": thread_id}}
        try:
            st = await app.state.graph.aget_state(cfg)
            if st.next:
                paused = any(t.interrupts for t in st.tasks)
                await app.state.graph.ainvoke(resume_command({}) if paused else None, cfg)
        except Exception:
            log.exception("settle_failed", thread_id=thread_id)

    async def do_kill() -> int:
        s = app.state
        threads = set(s.active_threads) | {p["thread_id"] for p in s.approvals.pending()}
        s.kill.engage()  # flag + stop sandbox + mark pending approvals killed
        for t in list(s.tasks):
            t.cancel()
        await asyncio.gather(*s.tasks, return_exceptions=True)
        for tid in threads:
            await settle(tid)
        s.active_threads.clear()
        return len(threads)

    @app.post("/kill")
    async def kill_http(request: Request) -> dict[str, Any]:
        if not authorized(request.headers, app.state.token):
            raise HTTPException(status_code=401)
        return {"killed": True, "threads": await do_kill()}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        if not authorized(ws.headers, app.state.token):
            await ws.close(code=4401)
            return
        await ws.accept()

        async def send(ev: dict[str, Any]) -> None:
            try:
                await ws.send_json(ev)
            except Exception:  # noqa: BLE001  the UI may be closed; the task must keep running
                log.debug("client_gone")

        await send({"type": "approvals", "items": app.state.approvals.pending()})  # pending ones survive UI restarts
        try:
            while True:
                msg = await ws.receive_json()
                await handle(msg, send)
        except WebSocketDisconnect:
            return

    async def handle(msg: Any, send: Send) -> None:
        s = app.state
        kind = msg.get("type", "user") if isinstance(msg, dict) else None
        if kind == "kill":
            await send({"type": "killed", "threads": await do_kill()})
        elif kind == "release_kill":
            s.kill.release()
            await send({"type": "kill_released"})
        elif kind == "usage":
            await send({"type": "usage", "today": s.meter.today(),
                        "caps": {"tokens": settings.budgets.daily_max_tokens, "cost_usd": settings.budgets.daily_max_cost_usd}})
        elif kind == "audit":
            await send({"type": "audit", "items": s.audit.tail(int(msg.get("limit", 50)))})
        elif kind == "approvals":
            await send({"type": "approvals", "items": s.approvals.pending(msg.get("thread_id"))})
        elif kind == "rules":
            await send({"type": "rules", "items": s.approvals.rules()})
        elif kind == "forget_rule":
            ok = s.approvals.delete_rule(int(msg.get("id", -1)))
            await send({"type": "rules", "items": s.approvals.rules(), "deleted": ok})
        elif kind == "set_agent_name":
            try:
                set_agent_name(settings, str(msg.get("name", "")))
            except ValueError as e:
                return await send({"type": "error", "code": "bad_request", "message": str(e)})
            except OSError:
                return await send({"type": "error", "code": "internal", "message": "could not save the name"})
            await send({"type": "agent_name", "name": settings.agent.name})
        elif kind in ("user", "approve"):
            if len(s.tasks) >= MAX_QUEUED_TURNS:
                return await send({"type": "error", "code": "busy", "message": "too many queued requests"})
            t = asyncio.create_task(guarded_turn(msg, kind, send))
            s.tasks.add(t)
            t.add_done_callback(s.tasks.discard)
        else:
            await send({"type": "error", "code": "bad_request", "message": "unknown message type"})

    async def guarded_turn(msg: dict[str, Any], kind: str, send: Send) -> None:
        tid = msg.get("thread_id", "default")
        try:
            async with app.state.turn_lock:
                app.state.active_threads.add(tid)
                await run_turn(msg, kind, send)
        except asyncio.CancelledError:
            await send({"type": "error", "code": "killed", "message": "stopped by the kill switch"})
        finally:
            app.state.active_threads.discard(tid)

    async def run_turn(msg: dict[str, Any], kind: str, send: Send) -> None:
        tid = msg.get("thread_id", "default")
        if not isinstance(tid, str) or not _THREAD_ID.match(tid):
            return await send({"type": "error", "code": "bad_request", "message": "invalid thread_id"})
        pending = app.state.approvals.pending(tid)
        if kind == "approve":
            decisions = msg.get("decisions")
            if not pending or not isinstance(decisions, dict):
                return await send({"type": "error", "code": "bad_request",
                                   "message": "nothing is pending for this thread, or 'decisions' is missing"})
            if set(decisions) - {p["call_id"] for p in pending}:  # decisions only for calls actually waiting
                return await send({"type": "error", "code": "bad_request", "message": "decision for an unknown call id"})
            st = await app.state.graph.aget_state({"configurable": {"thread_id": tid}})
            if not any(t.interrupts for t in st.tasks):  # the DB row and the checkpoint must agree
                return await send({"type": "error", "code": "bad_request", "message": "this thread is not waiting for approval"})
            graph_input: Any = resume_command(decisions)
        else:
            text = msg.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
                return await send({"type": "error", "code": "bad_request", "message": "invalid text"})
            if pending:  # a new request would orphan the paused tool calls
                return await send({"type": "error", "code": "approval_pending",
                                   "message": "this thread is waiting for your approval decision first", "items": pending})
            graph_input = {"messages": [HumanMessage(text)]}
        # Budgets stop runs in code; this is only a backstop against a routing bug.
        config = {"configurable": {"thread_id": tid}, "recursion_limit": settings.budgets.max_steps * 8 + 40}
        final, paused = "", False
        try:
            async for chunk in app.state.graph.astream(graph_input, config, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        paused = True
                        for it in update:
                            await send({"type": "approval_request", **redact_obj(it.value)})
                        continue
                    if not update:
                        continue
                    await emit(node, update, send)
                    if node in ("respond", "clarify"):
                        final = update.get("final", "")
            if paused:
                return await send({"type": "paused", "thread_id": tid, "pending": len(app.state.approvals.pending(tid))})
            st = await app.state.graph.aget_state(config)
            u = st.values.get("used", {})
            await send({"type": "final", "text": final, "stopped": st.values.get("stop_reason") or None,
                        "used": {k: u.get(k) for k in ("steps", "tool_calls", "tokens", "cost")}})
        except GraphRecursionError:
            await send({"type": "error", "code": "budget", "message": "stopped by the recursion backstop"})
        except MissingSecretError as e:
            await send({"type": "error", "code": "no_api_key", "message": str(e)})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # last resort: report to the client, keep the daemon alive
            log.exception("turn_failed", thread_id=tid)
            code, message = classify_provider_error(e) or ("internal", type(e).__name__)
            await send({"type": "error", "code": code, "message": message})

    async def emit(node: str, update: dict[str, Any], send: Send) -> None:
        if node == "classify" and update.get("intent"):
            await send({"type": "intent", **{k: update["intent"][k] for k in
                                             ("intent", "complexity", "needs_plan", "risk_level", "confidence")}})
        elif node == "plan" and update.get("goal"):
            g = update["goal"]
            await send({"type": "plan", "objective": g["objective"], "success_criteria": g["success_criteria"],
                        "steps": [s["description"] for s in g["steps"]]})
        elif node == "verify":
            await send({"type": "verdict", "status": update.get("verdict"), "notes": redact(update.get("notes", ""))})
        if node == "gate":
            return  # decisions are surfaced via approval requests and tool results
        for m in update.get("messages", []):
            if isinstance(m, AIMessage):
                for c in m.tool_calls:
                    await send({"type": "tool_call", "name": redact(str(c["name"]))[:64], "args": redact_obj(c["args"])})
            elif isinstance(m, ToolMessage):
                await send({"type": "tool_result", "name": m.name, "status": m.status,
                            "content": redact(text_of(m.content))[:2000]})

    return app
