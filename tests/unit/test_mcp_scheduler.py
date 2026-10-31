import asyncio
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_hitl import card, make, resume, turn

from fakes import FakeBrain, ScriptedModel, answer, call
from personal_agent import mcp_client, services
from personal_agent.scheduler import Scheduler
from personal_agent.settings.settings import SchedulerCfg, data_dir
from personal_agent.tools.registry import load_tools, meta_for
from personal_agent.tools.schedule_tools import schedule_cancel, schedule_list, schedule_task

SERVER = str(Path(__file__).parents[1] / "mcp_echo_server.py")


def write_cfg(servers: dict[str, Any]) -> None:
    (data_dir() / "mcp.yaml").write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")


@pytest.fixture(autouse=True)
def clean_mcp():  # type: ignore[no-untyped-def]
    yield
    mcp_client.unload()


# ---- MCP (real stdio server) -----------------------------------------------------------------------------------
async def test_mcp_tools_are_loaded_namespaced_and_default_to_approval(env: Path) -> None:
    write_cfg({"demo": {"command": sys.executable, "args": [SERVER], "tools": {"echo": "T0"}}})
    status = await mcp_client.load_tools()
    assert status == {"demo": "ok (3 tools)"}
    names = {t.name for t in load_tools()}
    assert {"demo_echo", "demo_add", "demo_whoami_env"} <= names
    echo, add = meta_for("demo_echo"), meta_for("demo_add")
    assert echo is not None and echo.tier == "T0" and echo.untrusted_output  # user classified it as read-only
    assert add is not None and add.tier == "T2" and add.side_effects  # everything else: needs approval
    tool = next(t for t in load_tools() if t.name == "demo_echo")
    assert "echo:hola" in str(await tool.ainvoke({"text": "hola"}))  # a real round-trip through the protocol


async def test_a_broken_server_does_not_block_the_others(env: Path) -> None:
    write_cfg({"good": {"command": sys.executable, "args": [SERVER]},
               "bad": {"command": "definitely-not-a-real-binary-xyz"},
               "off": {"command": sys.executable, "args": [SERVER], "enabled": False},
               "bad name!": {"command": "x"}})
    status = await mcp_client.load_tools()
    assert status["good"].startswith("ok") and status["bad"].startswith("error") and status["off"] == "disabled"
    assert status["bad name!"].startswith("error")
    assert meta_for("good_echo") is not None and meta_for("off_echo") is None


async def test_env_secrets_are_resolved_for_the_server_only(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_MCP_SECRET", "tok-123")
    write_cfg({"demo": {"command": sys.executable, "args": [SERVER], "env": {"TEST_TOKEN": "${MY_MCP_SECRET}"}}})
    await mcp_client.load_tools()
    out = str(await next(t for t in load_tools() if t.name == "demo_whoami_env").ainvoke({}))
    assert "tok-123" in out  # the server received it; the model/config text never contained the value


async def test_reload_unregisters_removed_servers_and_toggle_works(env: Path) -> None:
    write_cfg({"demo": {"command": sys.executable, "args": [SERVER]}})
    await mcp_client.load_tools()
    assert meta_for("demo_echo") is not None
    assert mcp_client.set_enabled("demo", False) and not mcp_client.set_enabled("nope", True)
    assert (await mcp_client.load_tools())["demo"] == "disabled" and meta_for("demo_echo") is None


async def test_mcp_tools_pass_through_the_approval_gate(env: Path, tmp_path: Path) -> None:
    write_cfg({"demo": {"command": sys.executable, "args": [SERVER]}})
    await mcp_client.load_tools()
    h = make(tmp_path, ScriptedModel(responses=[call("demo_add", {"a": 2, "b": 3}, id="m1"), answer("5")]))
    c = card(await turn(h))  # default tier T2: the user must approve an external tool
    assert c["tool"] == "demo_add" and c["tier"] == "T2" and "External MCP tool demo_add" in c["preview"]
    out = await resume(h, {"m1": {"action": "approve"}})
    assert out["final"] == "5"
    assert any("5" in str(m.content) for m in out["messages"] if m.type == "tool")


def test_the_agent_cannot_edit_the_mcp_config(env: Path) -> None:
    from personal_agent.tools.files import file_write

    assert file_write.invoke({"path": str(data_dir() / "mcp.yaml"), "content": "servers: {}"}).startswith("ERROR: access denied")


# ---- scheduler core -----------------------------------------------------------------------------------------------
def sched(tmp_path: Path, **cfg: int) -> Scheduler:
    return Scheduler(tmp_path / "s.db", SchedulerCfg(**cfg))


def test_add_validates(tmp_path: Path) -> None:
    s = sched(tmp_path, max_jobs=2, min_interval_minutes=5)
    now = time.time()
    with pytest.raises(ValueError, match="past"):
        s.add("x", now - 3600)
    with pytest.raises(ValueError, match="at least 5"):
        s.add("x", now + 60, every_minutes=1)
    with pytest.raises(ValueError, match="1-2000"):
        s.add("  ", now + 60)
    s.add("a", now + 60)
    s.add("b", now + 60)
    with pytest.raises(ValueError, match="at most 2"):
        s.add("c", now + 60)
    assert s.cancel(1) and not s.cancel(1) and len(s.jobs(only_active=True)) == 1
    s.add("c", now + 60)  # a cancelled job frees a slot


def test_one_shot_and_recurring_bookkeeping_skips_missed_slots(tmp_path: Path) -> None:
    s = sched(tmp_path)
    now = time.time()
    one = s.add("once", now + 1)
    rec = s.add("repeat", now + 1, every_minutes=10)
    job_one = next(j for j in s.jobs() if j["id"] == one)
    job_rec = next(j for j in s.jobs() if j["id"] == rec)
    s._finish(job_one, "ok", now + 5)
    s._finish(job_rec, "ok", now + 3600 + 5)  # busy/down for an hour: no catch-up storm
    after = {j["id"]: j for j in s.jobs()}
    assert after[one]["enabled"] == 0 and after[rec]["enabled"] == 1
    assert now + 3605 < after[rec]["next_run"] <= now + 3605 + 600 and after[rec]["runs"] == 1


def test_recover_runs_recent_misses_and_skips_old_ones(tmp_path: Path) -> None:
    s = sched(tmp_path, grace_minutes=60)
    now = time.time()
    s.add("recent", now + 1)
    s.add("ancient", now + 1)
    import sqlite3

    with sqlite3.connect(s.path) as db:  # pretend the daemon was down: due 10 min ago / 3 hours ago
        db.execute("UPDATE schedules SET next_run=? WHERE prompt='recent'", (now - 600,))
        db.execute("UPDATE schedules SET next_run=? WHERE prompt='ancient'", (now - 3 * 3600,))
    assert s.recover(now) == 1
    st = {j["prompt"]: j for j in s.jobs()}
    assert st["ancient"]["enabled"] == 0 and "skipped" in st["ancient"]["last_status"]
    assert st["recent"]["enabled"] == 1  # still due: it will run as soon as the loop starts


async def test_loop_runs_due_jobs_wakes_on_new_ones_and_survives_failures(tmp_path: Path) -> None:
    s = sched(tmp_path)
    ran: list[str] = []

    async def run_job(job: dict[str, Any]) -> str:
        ran.append(job["prompt"])
        if job["prompt"] == "boom":
            raise RuntimeError("job failed")
        return "ok"

    s.run_job = run_job
    s.start()
    try:
        await asyncio.sleep(0.2)  # nothing scheduled: the loop must simply be asleep (no polling)
        assert ran == []
        s.add("first", time.time() + 0.4)
        s.add("boom", time.time() + 0.5)
        s.add("later", time.time() + 0.9)  # added while the loop sleeps for an earlier job
        await asyncio.sleep(1.6)
        assert ran == ["first", "boom", "later"]
        st = {j["prompt"]: j["last_status"] for j in s.jobs()}
        assert st == {"first": "ok", "boom": "error: RuntimeError", "later": "ok"}
    finally:
        await s.stop()


# ---- scheduler tools -----------------------------------------------------------------------------------------------
def test_schedule_tools(env: Path) -> None:
    out = schedule_task.invoke({"prompt": "Summarise my inbox", "in_minutes": 30})
    assert out == "scheduled as #1" and "Summarise my inbox" in schedule_list.invoke({})
    at = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    assert schedule_task.invoke({"prompt": "daily", "at": at, "every_minutes": 1440}) == "scheduled as #2"
    assert schedule_task.invoke({"prompt": "x"}).startswith("ERROR: give exactly one")
    assert schedule_task.invoke({"prompt": "x", "in_minutes": 5, "at": at}).startswith("ERROR: give exactly one")
    assert schedule_task.invoke({"prompt": "x", "at": "not a date"}).startswith("ERROR")
    assert schedule_task.invoke({"prompt": "x", "in_minutes": 10, "every_minutes": 1}).startswith("ERROR")
    assert schedule_cancel.invoke({"schedule_id": 1}) == "cancelled" and schedule_cancel.invoke({"schedule_id": 1}).startswith("ERROR")


async def test_creating_a_schedule_requires_approval_and_shows_what_will_run(env: Path, tmp_path: Path) -> None:
    h = make(tmp_path, ScriptedModel(responses=[
        call("schedule_task", {"prompt": "Delete old logs every night", "in_minutes": 60, "every_minutes": 1440}, id="s1"), answer("ok")]))
    c = card(await turn(h))
    assert c["tool"] == "schedule_task" and c["tier"] == "T2" and c["risk"] == "high"
    assert "Delete old logs every night" in c["preview"] and "Repeats every 1440 minutes" in c["preview"]
    assert services.scheduler().jobs() == []  # nothing is scheduled until the user approves
    await resume(h, {"s1": {"action": "approve"}})
    assert len(services.scheduler().jobs()) == 1


_ = FakeBrain
