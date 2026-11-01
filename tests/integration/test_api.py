import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from starlette.websockets import WebSocketDisconnect

from fakes import TASK, FakeBrain, ScriptedModel, answer, call
from personal_agent.core.app import create_app

AUTH = {"Authorization": "Bearer test-token"}


def client(tmp: Path, brain: FakeBrain | None = None) -> TestClient:
    return TestClient(create_app(brain or FakeBrain(ScriptedModel(responses=[answer("hi")])), tmp / "c.db", "test-token"))


def hello(ws: Any) -> None:
    assert ws.receive_json()["type"] == "approvals"  # pending approvals are pushed on connect


def drain(ws: Any) -> list[dict[str, Any]]:
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] in ("final", "error", "usage", "agent_name"):
            return events


def test_rejects_missing_wrong_token_and_foreign_origin(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        for headers in ({}, {"Authorization": "Bearer nope"}, {**AUTH, "Origin": "https://evil.example"}):
            with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws", headers=headers):
                pass
        assert c.get("/health").json() == {"status": "ok"}
        assert c.get("/docs").status_code == 404


def test_task_streams_intent_plan_tools_verdict_final(env: Path, tmp_path: Path) -> None:
    (env / "n.txt").write_text("hello")
    brain = FakeBrain(ScriptedModel(responses=[call("file_read", {"path": "n.txt"}), answer("done: hello")]),
                      intents=[TASK])
    with client(tmp_path, brain) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        hello(ws)
        ws.send_json({"type": "user", "thread_id": "abc", "text": "read n.txt"})
        events = drain(ws)
    assert [e["type"] for e in events] == ["intent", "plan", "tool_call", "tool_result", "verdict", "final"]
    assert events[-1]["text"] == "done: hello" and events[-1]["used"]["tool_calls"] == 1


def test_bad_requests_get_errors_and_connection_survives(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        hello(ws)
        ws.send_json({"text": "hi", "thread_id": "../etc"})
        assert drain(ws)[-1]["code"] == "bad_request"
        ws.send_json({"text": ""})
        assert drain(ws)[-1]["code"] == "bad_request"
        ws.send_json({"text": "hi"})
        assert drain(ws)[-1]["type"] == "final"


def test_turns_never_run_concurrently(tmp_path: Path) -> None:
    import threading
    import time

    from langchain_core.messages import AIMessage

    lock, state = threading.Lock(), {"now": 0, "max": 0}

    def slow(_: Any) -> AIMessage:
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.3)
        with lock:
            state["now"] -= 1
        return AIMessage("ok")

    results: list[str] = []

    def talk(c: TestClient, tid: str) -> None:
        with c.websocket_connect("/ws", headers=AUTH) as ws:
            hello(ws)
            ws.send_json({"text": "hi", "thread_id": tid})
            results.append(drain(ws)[-1]["type"])

    with client(tmp_path, FakeBrain(RunnableLambda(slow))) as c:
        ts = [threading.Thread(target=talk, args=(c, f"t{i}")) for i in range(3)]
        [t.start() for t in ts]
        [t.join(timeout=30) for t in ts]
    assert results == ["final"] * 3 and state["max"] == 1  # serialised: daily cap stays exact


def test_usage_and_rename_commands(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        hello(ws)
        ws.send_json({"text": "hi"})
        drain(ws)
        ws.send_json({"type": "usage"})
        u = drain(ws)[-1]
        assert u["today"]["tokens"] > 0 and u["caps"]["tokens"] == 1_000_000
        ws.send_json({"type": "set_agent_name", "name": "Mimi"})
        assert drain(ws)[-1] == {"type": "agent_name", "name": "Mimi"}
        ws.send_json({"type": "set_agent_name", "name": "bad\nname"})
        assert drain(ws)[-1]["code"] == "bad_request"


def test_missing_api_key_is_reported_not_crashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("personal_agent.llm.factory.get_secret", lambda name: None)
    # the default classifier is local Ollama (keyless); pin it to a keyed provider so this test never hits a real daemon
    home = Path(os.environ["PERSONAL_AGENT_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "models:\n  classifier: {provider: anthropic, model: claude-haiku-5-5, fallbacks: []}\n", encoding="utf-8")
    app = create_app(db_path=tmp_path / "c.db", token="test-token")  # real LiveBrain
    with TestClient(app) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        hello(ws)
        ws.send_json({"text": "hi"})
        assert drain(ws)[-1]["code"] == "no_api_key"


@pytest.mark.parametrize(("msg", "code"), [
    ("Your credit balance is too low to access the Anthropic API", "quota_exhausted"),
    ("Error code: 429 rate limit", "rate_limited"),
    ("boom with secret sk-abcdefghijk", "internal"),
])
def test_provider_errors_map_to_safe_codes(tmp_path: Path, msg: str, code: str) -> None:
    def boom(_: Any) -> Any:
        raise RuntimeError(msg)

    with client(tmp_path, FakeBrain(RunnableLambda(boom))) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        hello(ws)
        ws.send_json({"text": "hi"})
        ev = drain(ws)[-1]
    assert ev["code"] == code and "sk-abcdefghijk" not in ev["message"]
