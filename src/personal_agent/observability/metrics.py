"""Local metrics (works with Langfuse off): one SQLite row per LLM / tool call, plus a per-day report.

A single LangChain callback sees every model and tool call of every node and sub-agent worker, because callbacks
propagate through the run config. Tokens and cost stay in `usage` (see usage.py); this adds latency, errors and
tool success so the "Usage" page can show error rate and tool success rate.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


class Metrics:
    def __init__(self, path: Path, keep_days: int = 90) -> None:
        self._path = path
        with closing(self._db()) as db, db:
            # same DDL as Meter: the report joins both, whichever is created first
            db.execute("CREATE TABLE IF NOT EXISTS usage (ts REAL, day TEXT, model TEXT, in_tok INT, out_tok INT, cost REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS events (ts REAL, day TEXT, kind TEXT, name TEXT, ok INT, ms REAL)")
            db.execute("DELETE FROM events WHERE ts < ?", (time.time() - keep_days * 86400,))

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def event(self, kind: str, name: str, ok: bool, ms: float) -> None:
        with closing(self._db()) as db, db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                       (time.time(), time.strftime("%Y-%m-%d"), kind, name[:80], int(ok), round(ms, 1)))

    def report(self, days: int = 7) -> dict[str, Any]:
        """Per-day tokens / cost / calls / error rate / tool success rate (newest first), plus per-model and per-tool totals."""
        days = max(1, min(days, 365))
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
        with closing(self._db()) as db:
            db.row_factory = sqlite3.Row
            use = {r["day"]: r for r in db.execute(
                "SELECT day, SUM(in_tok+out_tok) tok, SUM(cost) cost, COUNT(*) calls FROM usage WHERE day>=? GROUP BY day", (since,))}
            ev = {(r["day"], r["kind"]): r for r in db.execute(
                "SELECT day, kind, COUNT(*) n, SUM(ok) ok, AVG(ms) ms FROM events WHERE day>=? GROUP BY day, kind", (since,))}
            per_model = db.execute(
                "SELECT name, COUNT(*) n, SUM(1-ok) errors, AVG(ms) ms FROM events WHERE kind='llm' AND day>=? "
                "GROUP BY name ORDER BY n DESC", (since,)).fetchall()
            per_tool = db.execute(
                "SELECT name, COUNT(*) n, SUM(ok) ok, AVG(ms) ms FROM events WHERE kind='tool' AND day>=? "
                "GROUP BY name ORDER BY n DESC", (since,)).fetchall()
        out_days = []
        for d in sorted({*use, *(k[0] for k in ev)}, reverse=True):
            u, llm, tool = use.get(d), ev.get((d, "llm")), ev.get((d, "tool"))
            out_days.append({
                "day": d, "tokens": int(u["tok"] or 0) if u else 0, "cost_usd": round(float(u["cost"] or 0), 6) if u else 0.0,
                "llm_calls": llm["n"] if llm else 0,
                "llm_error_rate": round(1 - llm["ok"] / llm["n"], 4) if llm else None,
                "llm_avg_ms": round(llm["ms"]) if llm else None,
                "tool_calls": tool["n"] if tool else 0,
                "tool_success_rate": round(tool["ok"] / tool["n"], 4) if tool else None,
            })
        return {"days": out_days,
                "models": [{"model": r["name"], "calls": r["n"], "errors": r["errors"], "avg_ms": round(r["ms"])} for r in per_model],
                "tools": [{"tool": r["name"], "calls": r["n"], "ok": r["ok"], "avg_ms": round(r["ms"])} for r in per_tool]}


class MetricsCallback(BaseCallbackHandler):
    """Times every model and tool run. Never raises: metrics must not break a turn."""

    def __init__(self, metrics: Metrics) -> None:
        self._m = metrics
        self._open: dict[UUID, tuple[str, str, float]] = {}

    def _start(self, run_id: UUID, kind: str, name: str) -> None:
        self._open[run_id] = (kind, name, time.perf_counter())

    def _end(self, run_id: UUID, ok: bool) -> None:
        if (st := self._open.pop(run_id, None)) is None:
            return
        with contextlib.suppress(Exception):  # see class docstring
            self._m.event(st[0], st[1], ok, (time.perf_counter() - st[2]) * 1000)

    def on_chat_model_start(self, serialized: dict[str, Any] | None, messages: Any, *, run_id: UUID, **kw: Any) -> None:
        self._start(run_id, "llm", str((kw.get("metadata") or {}).get("ls_model_name") or "unknown"))

    def on_llm_start(self, serialized: dict[str, Any] | None, prompts: Any, *, run_id: UUID, **kw: Any) -> None:
        self._start(run_id, "llm", str((kw.get("metadata") or {}).get("ls_model_name") or "unknown"))

    def on_llm_end(self, response: Any, *, run_id: UUID, **kw: Any) -> None:
        self._end(run_id, True)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kw: Any) -> None:
        self._end(run_id, False)

    def on_tool_start(self, serialized: dict[str, Any] | None, input_str: str, *, run_id: UUID, **kw: Any) -> None:
        self._start(run_id, "tool", str(kw.get("name") or (serialized or {}).get("name") or "unknown"))

    def on_tool_end(self, output: Any, *, run_id: UUID, **kw: Any) -> None:
        # a tool that returns a ToolMessage with status="error" (denied / failed) is not a success
        self._end(run_id, getattr(output, "status", "success") != "error")

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kw: Any) -> None:
        self._end(run_id, False)
