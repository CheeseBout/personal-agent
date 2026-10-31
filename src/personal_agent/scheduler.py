"""Scheduler: run a prompt later or on a repeat, through the same graph (so policy, approvals, budgets all apply).

One timer, no polling: the loop sleeps until the next due job or until a job is added. A scheduled run that needs
approval simply pauses like any other task; nothing is ever auto-approved.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import structlog

from personal_agent.settings.settings import SchedulerCfg

log = structlog.get_logger()
RunJob = Callable[[dict[str, Any]], Awaitable[str]]


class Scheduler:
    def __init__(self, path: Path, cfg: SchedulerCfg) -> None:
        self.path, self.cfg = path, cfg
        self.run_job: RunJob | None = None
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        with closing(self._db()) as db, db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, prompt TEXT, next_run REAL,
                   every_s INT, enabled INT, last_run REAL, last_status TEXT, runs INT, created REAL)"""
            )

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # ---- jobs ----------------------------------------------------------------------------------------------
    def add(self, prompt: str, run_at: float, every_minutes: int | None = None) -> int:
        prompt = prompt.strip()
        if not prompt or len(prompt) > 2000:
            raise ValueError("the prompt must be 1-2000 characters")
        if run_at < time.time() - 60:
            raise ValueError("the time is in the past")
        if every_minutes is not None and every_minutes < self.cfg.min_interval_minutes:
            raise ValueError(f"repeat interval must be at least {self.cfg.min_interval_minutes} minutes")
        with closing(self._db()) as db, db:
            if db.execute("SELECT COUNT(*) FROM schedules WHERE enabled=1").fetchone()[0] >= self.cfg.max_jobs:
                raise ValueError(f"at most {self.cfg.max_jobs} active schedules")
            jid = db.execute("INSERT INTO schedules (prompt,next_run,every_s,enabled,runs,created) VALUES (?,?,?,1,0,?)",
                             (prompt, run_at, every_minutes * 60 if every_minutes else None, time.time())).lastrowid
        self.wake()
        return int(jid or 0)

    def jobs(self, only_active: bool = False) -> list[dict[str, Any]]:
        with closing(self._db()) as db:
            q = "SELECT * FROM schedules" + (" WHERE enabled=1" if only_active else "") + " ORDER BY id"
            return [dict(r) for r in db.execute(q)]

    def cancel(self, jid: int) -> bool:
        with closing(self._db()) as db, db:
            return db.execute("UPDATE schedules SET enabled=0 WHERE id=? AND enabled=1", (jid,)).rowcount > 0

    def due(self, now: float) -> list[dict[str, Any]]:
        with closing(self._db()) as db:
            return [dict(r) for r in db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run", (now,))]

    def next_time(self) -> float | None:
        with closing(self._db()) as db:
            r = db.execute("SELECT MIN(next_run) FROM schedules WHERE enabled=1").fetchone()
        return r[0] if r and r[0] is not None else None

    def _finish(self, job: dict[str, Any], status: str, now: float) -> None:
        nxt, enabled = job["next_run"], 0
        if job["every_s"]:
            nxt, enabled = job["next_run"] + job["every_s"], 1
            while nxt <= now:  # skip occurrences missed while we were busy or down
                nxt += job["every_s"]
        with closing(self._db()) as db, db:
            db.execute("UPDATE schedules SET next_run=?, enabled=?, last_run=?, last_status=?, runs=runs+1 WHERE id=?",
                       (nxt, enabled, now, status[:200], job["id"]))

    def recover(self, now: float | None = None) -> int:
        """After a restart: a missed run is still executed if recent (grace), otherwise skipped to the next slot."""
        now = now or time.time()
        n = 0
        for job in self.due(now):
            if now - job["next_run"] > self.cfg.grace_minutes * 60:
                self._finish(job, "skipped: missed while the agent was not running", now)
                n += 1
        return n

    # ---- loop ------------------------------------------------------------------------------------------------
    def wake(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.recover()
        self._task = asyncio.create_task(self._run(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            nxt = self.next_time()
            timeout = None if nxt is None else max(0.0, nxt - time.time())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
                continue  # woken: a job was added; recompute the next due time
            except TimeoutError:
                pass
            for job in self.due(time.time()):
                status = "no runner"
                if self.run_job is not None:
                    try:
                        status = await self.run_job(job)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.exception("scheduled_job_failed", job=job["id"])
                        status = f"error: {type(e).__name__}"
                self._finish(job, status, time.time())
