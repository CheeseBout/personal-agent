"""Local daemon API: token-protected WebSocket + /kill. Turns run as background tasks so that approvals and
the kill switch can be received while a turn is running or paused."""

from __future__ import annotations

import asyncio
import base64
import os
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
from starlette.websockets import WebSocketDisconnected

from personal_agent import mcp_client, services
from personal_agent.core.admin import (
    apply_patch,
    policy_add_path,
    policy_save,
    policy_text,
    public_settings,
    save_secret,
    secrets_status,
    test_role,
)
from personal_agent.core.attachments import build_content
from personal_agent.graph.brain import Brain
from personal_agent.graph.builder import build_graph, resume_command
from personal_agent.graph.context import text_of
from personal_agent.llm.errors import classify_provider_error
from personal_agent.llm.factory import MissingSecretError
from personal_agent.observability.audit import AuditLog
from personal_agent.observability.logging import redact, redact_obj
from personal_agent.observability.metrics import Metrics
from personal_agent.observability.tracing import Tracing
from personal_agent.observability.usage import Meter
from personal_agent.perception import hooks, windows
from personal_agent.perception.watch import ScreenWatcher
from personal_agent.policy.approvals import ApprovalStore
from personal_agent.policy.killswitch import KillSwitch
from personal_agent.rag.loaders import UnsupportedDocument
from personal_agent.rag.watch import FolderWatcher
from personal_agent.settings.secrets import get_api_token
from personal_agent.settings.settings import data_dir, load_settings, set_agent_name
from personal_agent.storage import ensure_db_ok
from personal_agent.tools import procs
from personal_agent.tools.registry import policy
from personal_agent.voice import stt

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
        s.metrics = Metrics(data_dir() / "usage.db", settings.observability.metrics_keep_days)
        s.tracing = Tracing(settings, s.metrics)
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
        s.clients = set()  # connected UIs: scheduled runs and pending approvals are pushed to them
        s.mcp_status = await mcp_client.load_tools() if mcp_client.read_config()["servers"] else {}
        s.watcher = start_watcher()
        loop = asyncio.get_running_loop()
        def emit_threadsafe(ev: dict[str, Any]) -> None:  # called from worker threads (tools, the watcher)
            loop.call_soon_threadsafe(lambda: asyncio.create_task(broadcast(ev)))

        s.screen = ScreenWatcher(lambda: settings.screen, emit_threadsafe)
        s.kill.on_engage(lambda: s.screen.stop("kill switch"))  # the kill switch also ends continuous watching
        hooks.emit = emit_threadsafe
        hooks.observers = lambda: len(s.clients)
        hooks.killed = lambda: bool(s.kill.engaged or procs.stopped())
        hooks.agent_name = lambda: settings.agent.name
        hooks.latest_frame = s.screen.latest
        ckpt = db_path or data_dir() / "checkpoints.db"
        ensure_db_ok(ckpt)
        async with AsyncSqliteSaver.from_conn_string(str(ckpt)) as saver:
            s.make_graph = lambda: build_graph(saver, brain, settings, s.meter, s.audit, s.approvals, s.kill)
            s.graph = s.make_graph()
            sched = services.scheduler()
            sched.run_job = run_scheduled
            sched.start()
            log.info("daemon_ready")
            yield
            await sched.stop()
            for t in list(s.tasks):
                t.cancel()
            await asyncio.gather(*s.tasks, return_exceptions=True)
        if s.watcher:
            s.watcher.stop()
        s.tracing.flush()
        s.screen.stop("shutdown")
        hooks.reset()
        mcp_client.unload()
        sandbox().stop()

    def start_watcher() -> FolderWatcher | None:
        folders = [Path(os.path.expandvars(f)) for f in settings.rag.watch]
        ok = [f for f in folders if policy().check_path(f).allowed and f.is_dir()]
        for f in set(folders) - set(ok):
            log.warning("rag_watch_skipped", folder=str(f), reason="not an existing folder inside the allowed paths")
        if not ok:
            return None
        w = FolderWatcher(ok, lambda folder: services.rag().sync_folder(folder, lambda p: policy().check_path(p).allowed))
        w.start()
        return w

    async def broadcast(ev: dict[str, Any]) -> None:
        for send in list(app.state.clients):
            await send(ev)

    async def run_scheduled(job: dict[str, Any]) -> str:
        """Run a due schedule as a normal turn on its own thread (same policy/approvals/budgets)."""
        s = app.state
        tid = f"sched-{job['id']}"
        if s.kill.engaged:
            return "skipped: the kill switch is engaged"
        if s.approvals.pending(tid):
            return "skipped: the previous run is still waiting for your approval"
        events: list[dict[str, Any]] = []

        async def sink(ev: dict[str, Any]) -> None:
            events.append(ev)
            if ev["type"] in ("approval_request", "paused", "error"):
                await broadcast(ev)  # the user must see (and can answer) approval cards from unattended runs

        async with s.turn_lock:
            s.active_threads.add(tid)
            try:
                await run_turn({"thread_id": tid, "text": f"[scheduled task #{job['id']}] {job['prompt']}", "untrusted": True}, "user", sink)
            finally:
                s.active_threads.discard(tid)
        last = events[-1] if events else {"type": "error", "code": "no_result"}
        if last["type"] == "final":
            status = "ok: " + str(last.get("text", ""))[:120]
        elif last["type"] == "paused":
            status = "waiting for your approval"
        else:
            status = f"error: {last.get('code', last['type'])}"
        await broadcast({"type": "schedule_run", "id": job["id"], "status": status})
        return status

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
        app.state.clients.add(send)
        try:
            while True:
                try:
                    msg = await ws.receive_json()
                except (ValueError, KeyError):  # invalid JSON (JSONDecodeError is a ValueError) or a binary frame (no "text")
                    await send({"type": "error", "code": "bad_request", "message": "message is not valid JSON"})
                    continue
                try:
                    await handle(msg, send)
                except (WebSocketDisconnect, WebSocketDisconnected, asyncio.CancelledError):
                    raise
                except Exception:
                    log.exception("ws_message_failed")
                    await send({"type": "error", "code": "internal", "message": "the request could not be processed"})
        except (WebSocketDisconnect, WebSocketDisconnected):  # the 2nd one: a send failed first, then we tried to read
            return
        finally:
            app.state.clients.discard(send)
            if not app.state.clients:  # nobody left to see the 'watching' badge: never keep watching the screen unseen
                app.state.screen.stop("no UI connected")

    MANAGE = {"memory_list", "memory_update", "memory_delete", "rag_list", "rag_pause", "rag_resume", "rag_sync",
              "mcp_list", "mcp_set", "mcp_reload", "schedules", "schedule_cancel"}

    async def manage(kind: str, msg: dict[str, Any]) -> dict[str, Any]:
        """Management calls behind the Memory / RAG / MCP / Schedule pages (shown in the UI Manager window)."""
        s = app.state
        mem, rag = services.memory(), services.rag()
        if kind == "memory_list":
            return {"type": "memory", "items": mem.items(msg.get("status"), msg.get("kind"))}
        if kind == "memory_update":  # edit text and/or approve a proposal (status=active)
            mid = int(msg["id"])
            ok = await asyncio.to_thread(mem.update, mid, msg.get("text"), msg.get("status"))
            now = await asyncio.to_thread(mem.get, mid)  # the resulting status: an edit into sensitive text demotes to 'proposed'
            return {"type": "memory_updated", "ok": ok, "id": mid, "status": now["status"] if now else None}
        if kind == "memory_delete":
            mid = int(msg["id"])
            return {"type": "memory_deleted", "ok": await asyncio.to_thread(mem.delete, mid), "id": mid}
        if kind == "rag_list":
            return {"type": "rag", "items": rag.documents(), "watching": bool(s.watcher), "paused": bool(s.watcher and s.watcher.paused)}
        if kind in ("rag_pause", "rag_resume"):
            if s.watcher:
                s.watcher.paused = kind == "rag_pause"
            return {"type": "rag", "items": rag.documents(), "watching": bool(s.watcher), "paused": bool(s.watcher and s.watcher.paused)}
        if kind == "rag_sync":
            folders = [Path(os.path.expandvars(f)) for f in settings.rag.watch]
            lines: list[str] = []
            for f in folders:
                if policy().check_path(f).allowed and f.is_dir():
                    lines += await asyncio.to_thread(rag.sync_folder, f, lambda p: policy().check_path(p).allowed)
            return {"type": "rag_synced", "report": lines}
        if kind == "mcp_list":
            return {"type": "mcp", "servers": {n: {"enabled": sp.get("enabled", True), "status": s.mcp_status.get(n, "not loaded")}
                                               for n, sp in mcp_client.read_config()["servers"].items()}}
        if kind == "mcp_set":
            return {"type": "mcp_set", "ok": mcp_client.set_enabled(str(msg["name"]), bool(msg["enabled"])), "reload_needed": True}
        if kind == "mcp_reload":
            if s.tasks:
                return {"type": "error", "code": "busy", "message": "finish the running task first"}
            s.mcp_status = await mcp_client.load_tools()
            s.graph = s.make_graph()  # same checkpointer: threads (and pending approvals) continue
            return {"type": "mcp", "servers": {n: {"status": st} for n, st in s.mcp_status.items()}}
        if kind == "schedules":
            return {"type": "schedules", "items": services.scheduler().jobs()}
        return {"type": "schedule_cancel", "ok": services.scheduler().cancel(int(msg["id"]))}

    ADMIN = {"settings_get", "settings_set", "secret_set", "test_role", "policy_get", "policy_set", "policy_add_path", "transcribe", "about", "screen_mode"}

    def stt_text(audio: bytes, mime: str) -> str:
        """Transcribe audio (blocking; call from a thread). The daily cap is checked first, cloud usage is metered."""
        if r := app.state.meter.daily_exceeded(settings.budgets):
            raise ValueError(f"daily usage cap reached ({r})")
        try:
            text, tin, tout = stt.transcribe(audio, mime, settings.voice.stt)
        except stt.SttError as e:
            raise ValueError(str(e)) from e
        if tin or tout:
            app.state.meter.record(settings.voice.stt.model, tin, tout)
        return text

    async def admin(kind: str, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Settings / secrets / policy / voice calls behind the Settings, Policy and chat-microphone UI."""
        s = app.state
        if kind == "settings_get":
            return {"type": "settings", **public_settings(settings)}
        if kind == "settings_set":
            changed = apply_patch(settings, msg.get("patch") or {})
            if "models" in changed and not s.tasks:
                s.graph = s.make_graph()  # fresh LiveBrain: new model choices apply to the next turn
            return {"type": "settings", "changed": sorted(changed), "applied_now": "models" not in changed or not s.tasks,
                    **public_settings(settings)}
        if kind == "secret_set":
            save_secret(str(msg.get("name")), str(msg.get("value")))
            return {"type": "secrets", "secrets": secrets_status()}  # values are never echoed
        if kind == "test_role":
            return {"type": "test_role", "role": msg.get("role"), **await test_role(settings, str(msg.get("role")))}
        if kind == "policy_get":
            return {"type": "policy", "text": policy_text()}
        if kind in ("policy_set", "policy_add_path"):
            if kind == "policy_set":
                policy_save(str(msg.get("text")))
            else:
                policy_add_path(str(msg.get("path")))
            return {"type": "policy", "text": policy_text(), "saved": True}
        if kind == "transcribe":
            audio = base64.b64decode(str(msg.get("data", "")), validate=True)
            return {"type": "transcript", "text": await asyncio.to_thread(stt_text, audio, str(msg.get("mime", "audio/wav")))}
        if kind == "screen_mode":
            act = str(msg.get("action", "status"))
            if act not in ("on", "off", "pause", "resume", "status"):
                raise ValueError("action must be on, off, pause, resume or status")
            if act == "on" and s.kill.engaged:
                raise ValueError("the kill switch is engaged")
            before = s.screen.state
            {"on": s.screen.start, "off": s.screen.stop, "pause": s.screen.pause, "resume": s.screen.resume}.get(act, lambda: None)()
            if s.screen.state != before:
                return None  # the watcher already broadcast the change to every UI (this one included)
            status: dict[str, Any] = s.screen.status()
            return status
        if isinstance(msg.get("ui_pid"), int):
            windows.register_own_pid(msg["ui_pid"])  # the agent may never operate the UI's windows (it could approve itself)
        return {"type": "about", "pid": os.getpid(), "killed": app.state.kill.engaged}

    async def handle(msg: Any, send: Send) -> None:
        s = app.state
        kind = msg.get("type", "user") if isinstance(msg, dict) else None
        if isinstance(kind, str) and kind in ADMIN:
            try:
                reply = await admin(kind, msg)
                return await send(reply) if reply is not None else None
            except (ValueError, TypeError, KeyError, OSError) as e:
                return await send({"type": "error", "code": "bad_request", "message": str(e)[:200]})
        if kind == "kill":
            await send({"type": "killed", "threads": await do_kill()})
        elif kind == "release_kill":
            s.kill.release()
            await send({"type": "kill_released"})
        elif kind == "usage":
            days = msg.get("days", 7)
            await send({"type": "usage", "today": s.meter.today(), "report": s.metrics.report(days if isinstance(days, int) else 7),
                        "langfuse": s.tracing.langfuse_on,
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
        elif isinstance(kind, str) and kind in MANAGE:
            try:
                reply = await manage(kind, msg)
            except (ValueError, TypeError, KeyError) as e:
                reply = {"type": "error", "code": "bad_request", "message": str(e)[:200]}
            if isinstance(rid := msg.get("rid"), int) and not isinstance(rid, bool):  # lets the UI match a reply to its request (ints only: nothing else is echoed)
                reply["rid"] = rid
            await send(reply)
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
            text, files = msg.get("text") or "", msg.get("attachments")
            if not isinstance(text, str) or len(text) > MAX_TEXT or not (text.strip() or files):
                return await send({"type": "error", "code": "bad_request", "message": "invalid text"})
            if pending:  # a new request would orphan the paused tool calls
                return await send({"type": "error", "code": "approval_pending",
                                   "message": "this thread is waiting for your approval decision first", "items": pending})
            try:
                content = await asyncio.to_thread(build_content, text, files, stt_text)
            except (ValueError, OSError, UnsupportedDocument) as e:
                return await send({"type": "error", "code": "bad_request", "message": str(e)[:200]})
            graph_input = {"messages": [HumanMessage(content)]}
            if files or msg.get("untrusted"):  # attachments are untrusted text; a scheduled run is unattended: side effects need approval
                graph_input["tainted"] = True
        # Budgets stop runs in code; this is only a backstop against a routing bug.
        config = app.state.tracing.run_config(tid, settings.budgets.max_steps * 8 + 40)
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
                        "steps": [s["description"] for s in g["steps"]], "roles": [s.get("role", "main") for s in g["steps"]]})
        elif node == "worker":  # one event per finished sub-agent (compact: the full detail stays in the audit log)
            for r in update.get("wresults", []):
                await send({"type": "worker", "step": r["step"], "role": r["role"], "status": r["status"],
                            "summary": redact(r["summary"])[:300], "tokens": r["used"]["tokens"], "cost": r["used"]["cost"]})
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
