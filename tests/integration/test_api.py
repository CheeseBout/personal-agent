from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from starlette.websockets import WebSocketDisconnect

from fakes import ScriptedModel, call
from personal_agent.core.app import create_app

AUTH = {"Authorization": "Bearer test-token"}


def client(tmp: Path, *responses: Any) -> TestClient:
    return TestClient(create_app(lambda tools: ScriptedModel(responses=list(responses)), tmp / "c.db", "test-token"))


def drain(ws: Any) -> list[dict[str, Any]]:
    events = []
    while True:
        ev = ws.receive_json()
        events.append(ev)
        if ev["type"] in ("final", "error"):
            return events


def test_rejects_missing_wrong_token_and_foreign_origin(tmp_path: Path) -> None:
    with client(tmp_path, AIMessage("x")) as c:
        for headers in ({}, {"Authorization": "Bearer nope"}, {**AUTH, "Origin": "https://evil.example"}):
            with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws", headers=headers):
                pass
        assert c.get("/health").json() == {"status": "ok"}
        assert c.get("/docs").status_code == 404


def test_roundtrip_streams_tool_events_and_final(env: Path, tmp_path: Path) -> None:
    (env / "n.txt").write_text("hello")
    with client(tmp_path, call("file_read", {"path": "n.txt"}), AIMessage("done: hello")) as c:
        with c.websocket_connect("/ws", headers=AUTH) as ws:
            ws.send_json({"type": "user", "thread_id": "abc", "text": "read n.txt"})
            kinds = [e["type"] for e in drain(ws)]
        assert kinds == ["tool_call", "tool_result", "final"]


def test_bad_requests_get_errors_and_connection_survives(tmp_path: Path) -> None:
    with client(tmp_path, AIMessage("ok")) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        ws.send_json({"text": "hi", "thread_id": "../etc"})
        assert drain(ws)[-1]["code"] == "bad_request"
        ws.send_json({"text": ""})
        assert drain(ws)[-1]["code"] == "bad_request"
        ws.send_json({"text": "hi"})
        assert drain(ws)[-1]["type"] == "final"


def test_missing_api_key_is_reported_not_crashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("personal_agent.llm.factory.get_secret", lambda name: None)
    app = create_app(db_path=tmp_path / "c.db", token="test-token")  # real model factory
    with TestClient(app) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        ws.send_json({"text": "hi"})
        assert drain(ws)[-1]["code"] == "no_api_key"
