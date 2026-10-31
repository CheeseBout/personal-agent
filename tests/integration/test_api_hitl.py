import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from fakes import FakeBrain, ScriptedModel, answer, call
from personal_agent.core.app import create_app

AUTH = {"Authorization": "Bearer test-token"}
WRITE = call("file_write", {"path": "out.txt", "content": "hello"}, id="w1")
END = {"final", "error", "paused", "usage", "audit", "rules", "approvals", "killed", "kill_released"}


@contextmanager
def app_client(tmp: Path, *responses: Any, brain: FakeBrain | None = None) -> Iterator[TestClient]:
    """TestClient whose WebSockets are closed BEFORE the app shuts down (otherwise shutdown waits forever)."""
    c = TestClient(create_app(brain or FakeBrain(ScriptedModel(responses=list(responses))), tmp / "c.db", "test-token"))
    c.open_ws = []  # type: ignore[attr-defined]
    with c:
        try:
            yield c
        finally:
            for ctx in reversed(c.open_ws):  # type: ignore[attr-defined]
                ctx.__exit__(None, None, None)


def until(ws: Any, stop: set[str] = END) -> list[dict[str, Any]]:
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] in stop:
            return events


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def connect(c: TestClient) -> Any:
    ctx = c.websocket_connect("/ws", headers=AUTH)
    ws = ctx.__enter__()
    c.open_ws.append(ctx)  # type: ignore[attr-defined]
    first = ws.receive_json()
    assert first["type"] == "approvals"
    ws.first = first
    return ws


def test_full_approval_flow_over_the_wire(env: Path, tmp_path: Path) -> None:
    with app_client(tmp_path, WRITE, answer("done")) as c:
        ws = connect(c)
        ws.send_json({"text": "write it", "thread_id": "th"})
        ev = until(ws)
        assert types(ev) == ["intent", "tool_call", "approval_request", "paused"]
        req = ev[2]["approvals"][0]
        assert req["tool"] == "file_write" and req["tier"] == "T2" and "NEW FILE" in req["preview"] and req["thread_id"] == "th"
        assert not (env / "out.txt").exists()

        ws.send_json({"type": "approvals"})
        assert len(until(ws)[-1]["items"]) == 1
        ws.send_json({"text": "also do this", "thread_id": "th"})  # must not orphan the paused call
        assert until(ws)[-1]["code"] == "approval_pending"

        ws.send_json({"type": "approve", "thread_id": "th", "decisions": {"w1": {"action": "approve", "remember": True}}})
        final = until(ws)
        assert final[-1]["type"] == "final" and final[-1]["text"] == "done" and (env / "out.txt").read_text() == "hello"
        ws.send_json({"type": "audit"})
        rows = until(ws)[-1]["items"]
        assert rows[0]["tool"] == "file_write" and rows[0]["decision"] == "approved"
        ws.send_json({"type": "rules"})
        rules = until(ws)[-1]["items"]
        assert len(rules) == 1 and rules[0]["tool"] == "file_write"
        ws.send_json({"type": "forget_rule", "id": rules[0]["id"]})
        assert until(ws)[-1]["items"] == []


def test_approve_with_nothing_pending_is_an_error(tmp_path: Path) -> None:
    with app_client(tmp_path, answer("x")) as c:
        ws = connect(c)
        ws.send_json({"type": "approve", "thread_id": "th", "decisions": {"w1": {"action": "approve"}}})
        assert until(ws)[-1]["code"] == "bad_request"


def test_a_paused_thread_does_not_block_other_threads(env: Path, tmp_path: Path) -> None:
    with app_client(tmp_path, WRITE, answer("other done")) as c:
        ws = connect(c)
        ws.send_json({"text": "a", "thread_id": "A"})
        assert types(until(ws))[-1] == "paused"
        ws.send_json({"text": "b", "thread_id": "B"})
        assert until(ws)[-1]["type"] == "final"


def test_pending_approval_survives_a_daemon_restart(env: Path, tmp_path: Path) -> None:
    with app_client(tmp_path, WRITE, answer("never used")) as c1:
        ws = connect(c1)
        ws.send_json({"text": "write it", "thread_id": "th"})
        assert types(until(ws))[-1] == "paused"
    # --- daemon restarted: new process state, same data on disk ---
    with app_client(tmp_path, answer("finished after restart")) as c2:
        ws = connect(c2)
        assert len(ws.first["items"]) == 1 and ws.first["items"][0]["tool"] == "file_write"  # UI sees it on reconnect
        ws.send_json({"type": "approve", "thread_id": "th", "decisions": {"w1": {"action": "approve"}}})
        final = until(ws)[-1]
        assert final["text"] == "finished after restart" and (env / "out.txt").read_text() == "hello"


def test_kill_switch_cancels_a_running_turn_and_blocks_new_work(tmp_path: Path) -> None:
    started = threading.Event()

    def slow(_: Any) -> AIMessage:
        started.set()
        time.sleep(3)
        return AIMessage("too late")

    with app_client(tmp_path, brain=FakeBrain(RunnableLambda(slow))) as c:
        ws = connect(c)
        ws.send_json({"text": "long job", "thread_id": "th"})
        assert started.wait(5)
        t0 = time.time()
        ws.send_json({"type": "kill"})
        seen = types(until(ws, {"killed"}))
        assert "killed" in seen and time.time() - t0 < 2.5  # did not wait for the 3 s model call
        ws.send_json({"text": "anything", "thread_id": "th2"})
        final = until(ws)[-1]
        assert final["stopped"] == "killed"  # engaged switch: no model call, no tool
        ws.send_json({"type": "release_kill"})
        assert until(ws)[-1]["type"] == "kill_released"


def test_kill_while_paused_rejects_the_pending_call(env: Path, tmp_path: Path) -> None:
    with app_client(tmp_path, WRITE, answer("x")) as c:
        ws = connect(c)
        ws.send_json({"text": "write it", "thread_id": "th"})
        assert types(until(ws))[-1] == "paused"
        ws.send_json({"type": "kill"})
        assert until(ws)[-1]["threads"] == 1
        ws.send_json({"type": "approvals"})
        assert until(ws)[-1]["items"] == [] and not (env / "out.txt").exists()
        ws.send_json({"type": "release_kill"})
        until(ws)
        ws.send_json({"text": "hello again", "thread_id": "th"})  # the thread is usable again
        assert until(ws)[-1]["type"] == "final"


def test_http_kill_endpoint_needs_the_token(tmp_path: Path) -> None:
    with app_client(tmp_path, answer("x")) as c:
        assert c.post("/kill").status_code == 401
        assert c.post("/kill", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.post("/kill", headers={**AUTH, "Origin": "https://evil.example"}).status_code == 401
        r = c.post("/kill", headers=AUTH)
        assert r.status_code == 200 and r.json()["killed"] is True


def test_decisions_must_match_calls_that_are_actually_waiting(env: Path, tmp_path: Path) -> None:
    with app_client(tmp_path, WRITE, answer("done")) as c:
        ws = connect(c)
        ws.send_json({"text": "write it", "thread_id": "th"})
        assert types(until(ws))[-1] == "paused"
        ws.send_json({"type": "approve", "thread_id": "th", "decisions": {"someone-else": {"action": "approve"}}})
        err = until(ws)[-1]
        assert err["code"] == "bad_request" and "unknown call id" in err["message"] and not (env / "out.txt").exists()
        ws.send_json({"type": "approve", "thread_id": "th", "decisions": {"w1": {"action": "approve"}}})  # the real one still works
        assert until(ws)[-1]["type"] == "final"
        ws.send_json({"type": "approve", "thread_id": "th", "decisions": {"w1": {"action": "approve"}}})  # replay
        assert until(ws)[-1]["code"] == "bad_request"
