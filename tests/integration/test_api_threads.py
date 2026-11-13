from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from fakes import QUESTION, FakeBrain, ScriptedModel, answer
from personal_agent.core.app import create_app

AUTH = {"Authorization": "Bearer test-token"}


def client(tmp: Path, *replies: str) -> TestClient:
    return TestClient(create_app(FakeBrain(ScriptedModel(responses=[answer(r) for r in replies] or [answer("hi")]), intents=[QUESTION]), tmp / "c.db", "test-token"))


def ask(ws: Any, kind: str, **kw: Any) -> dict[str, Any]:
    ws.send_json({"type": kind, "rid": 1, **kw})
    while True:
        ev = ws.receive_json()
        if ev.get("rid") == 1 or ev["type"] == "error":
            return ev


def turn(ws: Any, text: str, tid: str) -> list[dict[str, Any]]:
    ws.send_json({"type": "user", "thread_id": tid, "text": text})
    out = []
    while True:
        ev = ws.receive_json()
        out.append(ev)
        if ev["type"] in ("final", "error"):
            return out


def test_turn_events_carry_their_thread_and_are_kept_as_history(tmp_path: Path) -> None:
    with client(tmp_path, "pong") as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        ws.receive_json()
        events = turn(ws, "Gửi ping giúp mình", "c-one")
        assert {e["thread_id"] for e in events} == {"c-one"}
        hist = ask(ws, "thread_history", thread_id="c-one")
        assert [(e["kind"], e["text"]) for e in hist["entries"] if e["kind"] in ("user", "agent")] == [("user", "Gửi ping giúp mình"), ("agent", "pong")]
        assert hist["more"] is False and hist["entries"][-1]["meta"]["used"]["steps"] >= 1


def test_list_rename_pin_search_export_and_delete(tmp_path: Path) -> None:
    with client(tmp_path, "one", "two") as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        ws.receive_json()
        turn(ws, "báo cáo doanh thu", "c-a")
        turn(ws, "chuyện khác hẳn", "c-b")
        items = ask(ws, "threads_list")["items"]
        assert [t["id"] for t in items] == ["c-b", "c-a"] and items[1]["title"] == "báo cáo doanh thu" and not items[0]["running"]
        assert ask(ws, "thread_rename", thread_id="c-a", title="Doanh thu")["ok"]
        assert ask(ws, "thread_pin", thread_id="c-a", pinned=True)["ok"]
        assert [(t["id"], t["title"]) for t in ask(ws, "threads_list")["items"]][0] == ("c-a", "Doanh thu")
        hits = ask(ws, "thread_search", query="bao cao")["items"]
        assert [h["thread_id"] for h in hits] == ["c-a"]
        assert "# Doanh thu" in ask(ws, "thread_export", thread_id="c-a")["markdown"]
        assert ask(ws, "thread_delete", thread_id="c-a")["removed"] >= 2
        assert [t["id"] for t in ask(ws, "threads_list")["items"]] == ["c-b"]
        assert ask(ws, "thread_search", query="doanh thu")["items"] == []


def test_new_thread_ids_and_bad_ids_are_refused(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        ws.receive_json()
        assert ask(ws, "thread_new")["thread_id"].startswith("c-")
        assert ask(ws, "thread_history", thread_id="../x")["code"] == "bad_request"
        assert ask(ws, "thread_delete", thread_id="a b")["code"] == "bad_request"


def test_history_pages_and_survives_a_restart(tmp_path: Path) -> None:
    with client(tmp_path, "r") as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        ws.receive_json()
        for i in range(3):
            turn(ws, f"q{i}", "c-p")
        page = ask(ws, "thread_history", thread_id="c-p", limit=4)
        assert page["more"] is True and len(page["entries"]) == 4
        older = ask(ws, "thread_history", thread_id="c-p", before=page["entries"][0]["id"], limit=50)
        assert older["more"] is False and older["entries"][-1]["id"] < page["entries"][0]["id"]
    with client(tmp_path) as c2, c2.websocket_connect("/ws", headers=AUTH) as ws2:  # a new daemon process, same data folder
        ws2.receive_json()
        texts = [e["text"] for e in ask(ws2, "thread_history", thread_id="c-p", limit=50)["entries"] if e["kind"] == "user"]
        assert texts == ["q0", "q1", "q2"]


def test_a_rejected_request_is_not_history(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect("/ws", headers=AUTH) as ws:
        ws.receive_json()
        ws.send_json({"type": "user", "thread_id": "c-x", "text": ""})
        assert ws.receive_json()["code"] == "bad_request"
        assert ask(ws, "thread_history", thread_id="c-x")["entries"] == []
