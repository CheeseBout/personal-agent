import sys
import time
from pathlib import Path
from typing import Any

import yaml
from test_api_hitl import app_client, connect, types, until

from fakes import FakeBrain, ScriptedModel, answer, call
from personal_agent import services
from personal_agent.settings.settings import data_dir

DONE = {"memory", "memory_updated", "memory_deleted", "rag", "rag_synced", "rag_added", "rag_removed", "rag_results", "mcp", "mcp_set", "schedules", "schedule_cancel",
        "error", "approvals", "final", "paused", "schedule_run", "killed", "kill_released"}


def ask(ws: Any, msg: dict[str, Any]) -> dict[str, Any]:
    ws.send_json(msg)
    return until(ws, DONE)[-1]


def test_memory_pages_list_edit_approve_delete(tmp_path: Path) -> None:
    m = services.memory()
    m.add("The user likes green tea", "semantic")
    pid = m.add("my password is hunter2", "semantic", status="active")["id"]  # sensitive -> proposed
    with app_client(tmp_path, answer("x")) as c:
        ws = connect(c)
        assert [x["text"] for x in ask(ws, {"type": "memory_list", "status": "active"})["items"]] == ["The user likes green tea"]
        proposed = ask(ws, {"type": "memory_list", "status": "proposed"})["items"]
        assert [x["id"] for x in proposed] == [pid] and proposed[0]["sensitive"] == 1
        edited = ask(ws, {"type": "memory_update", "id": pid, "text": "I use a password manager"})
        assert edited["ok"] is True and edited["id"] == pid
        approved = ask(ws, {"type": "memory_update", "id": pid, "status": "active"})  # the user approves it
        assert approved["ok"] is True and approved["status"] == "active"
        sensitive = ask(ws, {"type": "memory_update", "id": pid, "text": "my password is hunter3"})  # an edit into sensitive text demotes it
        assert sensitive["ok"] is True and sensitive["status"] == "proposed"
        gone = ask(ws, {"type": "memory_update", "id": 999, "text": "x"})
        assert gone["ok"] is False and gone["status"] is None
        assert ask(ws, {"type": "memory_update", "id": pid, "status": "weird"})["code"] == "bad_request"
        deleted = ask(ws, {"type": "memory_delete", "id": pid})
        assert deleted["ok"] is True and deleted["id"] == pid
        assert ask(ws, {"type": "memory_delete", "id": pid})["ok"] is False


def test_manage_replies_echo_only_an_integer_request_id(tmp_path: Path) -> None:
    mid = services.memory().add("The user likes green tea", "semantic")["id"]
    with app_client(tmp_path, answer("x")) as c:
        ws = connect(c)
        assert ask(ws, {"type": "memory_list", "rid": 7})["rid"] == 7
        assert ask(ws, {"type": "memory_update", "id": 999, "text": "x", "rid": 8})["rid"] == 8
        err = ask(ws, {"type": "memory_update", "id": mid, "status": "weird", "rid": 9})  # errors are matched to their request too
        assert err["code"] == "bad_request" and err["rid"] == 9
        for bad in ("<b>x</b>", True, 1.5, [1], None):
            assert "rid" not in ask(ws, {"type": "memory_list", "rid": bad})


def test_rag_page_lists_documents_and_reports_no_watcher(env: Path, tmp_path: Path) -> None:
    (env / "doc.md").write_text("# Doc\nsome text", encoding="utf-8")
    services.rag().ingest(env / "doc.md")
    with app_client(tmp_path, answer("x")) as c:
        ws = connect(c)
        r = ask(ws, {"type": "rag_list"})
        assert [Path(d["source"]).name for d in r["items"]] == ["doc.md"] and r["watching"] is False
        assert ask(ws, {"type": "rag_pause"})["paused"] is False  # nothing is watched, so nothing to pause


def test_rag_page_add_search_remove_and_sync_without_folders(env: Path, tmp_path: Path) -> None:
    doc = env / "note.md"
    doc.write_text("# Tea\nGreen tea is brewed at 80 degrees.", encoding="utf-8")
    with app_client(tmp_path, answer("x")) as c:
        ws = connect(c)
        added = ask(ws, {"type": "rag_add", "path": str(doc), "rid": 7})
        assert added["type"] == "rag_added" and added["rid"] == 7 and added["report"][0].startswith("ingested")
        assert ask(ws, {"type": "rag_add", "path": "relative.md"})["type"] == "error"  # only absolute paths
        assert ask(ws, {"type": "rag_add", "path": str(tmp_path / "outside.md")})["type"] == "error"  # outside the allowed paths
        found = ask(ws, {"type": "rag_search", "query": "green tea"})
        assert found["type"] == "rag_results" and "note.md" in found["hits"][0]["cite"]
        assert ask(ws, {"type": "rag_search", "query": "  "})["type"] == "error"
        assert ask(ws, {"type": "rag_sync"}) == {"type": "rag_synced", "report": [], "folders": 0}
        removed = ask(ws, {"type": "rag_remove", "source": str(doc)})
        assert removed["type"] == "rag_removed" and removed["chunks"] > 0 and doc.exists()
        assert ask(ws, {"type": "rag_remove", "source": str(doc)})["chunks"] == 0


def test_rag_watch_folders_must_be_inside_the_allowed_paths(env: Path, tmp_path: Path, monkeypatch: Any) -> None:
    from personal_agent.settings.settings import load_settings

    inside, outside = env / "docs", tmp_path / "elsewhere"
    inside.mkdir()
    outside.mkdir()
    load_settings().rag.watch = [str(inside), str(outside)]
    with app_client(tmp_path, answer("x")) as c:
        ws = connect(c)
        assert ask(ws, {"type": "rag_list"})["watching"] is True
        assert [str(f) for f in c.app.state.watcher.folders] == [str(inside)]  # the outside folder was refused
        assert ask(ws, {"type": "rag_pause"})["paused"] is True and ask(ws, {"type": "rag_resume"})["paused"] is False
        (inside / "a.md").write_text("# A\nhello watcher", encoding="utf-8")
        deadline = time.time() + 10
        while time.time() < deadline and not services.rag().documents():
            time.sleep(0.2)
        assert [Path(d["source"]).name for d in services.rag().documents()] == ["a.md"]  # indexed in the background


def test_mcp_page_toggle_and_reload(env: Path, tmp_path: Path) -> None:
    server = str(Path(__file__).parents[1] / "mcp_echo_server.py")
    (data_dir() / "mcp.yaml").write_text(yaml.safe_dump({"servers": {"demo": {"command": sys.executable, "args": [server]}}}), encoding="utf-8")
    with app_client(tmp_path, answer("x")) as c:
        ws = connect(c)
        assert ask(ws, {"type": "mcp_list"})["servers"]["demo"] == {"enabled": True, "status": "ok (3 tools)"}  # loaded at startup
        assert ask(ws, {"type": "mcp_set", "name": "demo", "enabled": False})["ok"] is True
        assert ask(ws, {"type": "mcp_set", "name": "nope", "enabled": True})["ok"] is False
        assert ask(ws, {"type": "mcp_reload"})["servers"]["demo"]["status"] == "disabled"
        assert ask(ws, {"type": "mcp_set", "name": "demo", "enabled": True})["reload_needed"] is True
        assert ask(ws, {"type": "mcp_reload"})["servers"]["demo"]["status"].startswith("ok")
        ws.send_json({"text": "still works after the graph was rebuilt", "thread_id": "t"})
        assert until(ws, DONE)[-1]["type"] == "final"


def test_schedule_page(tmp_path: Path) -> None:
    jid = services.scheduler().add("remind me", time.time() + 3600)
    with app_client(tmp_path, answer("x")) as c:
        ws = connect(c)
        assert [j["prompt"] for j in ask(ws, {"type": "schedules"})["items"]] == ["remind me"]
        assert ask(ws, {"type": "schedule_cancel", "id": jid})["ok"] is True
        assert ask(ws, {"type": "schedules"})["items"][0]["enabled"] == 0


def wait_status(jid: int, timeout: float = 15) -> str:
    end = time.time() + timeout
    while time.time() < end:
        st = next(j for j in services.scheduler().jobs() if j["id"] == jid)["last_status"]
        if st:
            return str(st)
        time.sleep(0.1)
    raise AssertionError("the scheduled job never ran")


def test_a_due_job_runs_through_the_graph_and_is_pushed_to_the_ui(tmp_path: Path) -> None:
    with app_client(tmp_path, answer("the report is ready")) as c:
        ws = connect(c)
        jid = services.scheduler().add("prepare the report", time.time() + 0.3)
        ev = until(ws, {"schedule_run"})[-1]
        assert ev["id"] == jid and ev["status"] == "ok: the report is ready"
        assert wait_status(jid).startswith("ok:")
        snap = c.app.state.graph.get_state({"configurable": {"thread_id": f"sched-{jid}"}})
        assert "[scheduled task #1] prepare the report" in str(snap.values["messages"][0].content)  # a normal turn on its own thread


def test_a_scheduled_job_never_bypasses_approval(env: Path, tmp_path: Path) -> None:
    write = call("file_write", {"path": "nightly.txt", "content": "x"}, id="w1")
    with app_client(tmp_path, write, answer("done")) as c:
        ws = connect(c)
        jid = services.scheduler().add("write a file", time.time() + 0.3)
        evs = until(ws, {"schedule_run"})
        assert "approval_request" in types(evs) and evs[-1]["status"] == "waiting for your approval"
        assert not (env / "nightly.txt").exists()  # unattended, yet nothing was written
        pending = ask(ws, {"type": "approvals"})["items"]
        assert [p["thread_id"] for p in pending] == [f"sched-{jid}"]
        ws.send_json({"type": "approve", "thread_id": f"sched-{jid}", "decisions": {"w1": {"action": "approve"}}})
        assert until(ws, {"final"})[-1]["type"] == "final" and (env / "nightly.txt").exists()


def test_scheduled_jobs_are_skipped_while_the_kill_switch_is_engaged(tmp_path: Path) -> None:
    with app_client(tmp_path, answer("should not run")) as c:
        ws = connect(c)
        assert ask(ws, {"type": "kill"})["type"] == "killed"
        jid = services.scheduler().add("do it", time.time() + 0.3)
        assert wait_status(jid) == "skipped: the kill switch is engaged"
        assert ask(ws, {"type": "release_kill"})["type"] == "kill_released"


_ = (FakeBrain, ScriptedModel)
