"""Token/cost accounting persisted in SQLite so daily caps survive restarts."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from personal_agent.settings.settings import Budgets, Price


class Meter:
    def __init__(self, path: Path, pricing: dict[str, Price]) -> None:
        self._path = path
        self._pricing = pricing
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS usage (ts REAL, day TEXT, model TEXT, in_tok INT, out_tok INT, cost REAL)"
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def cost(self, model: str, in_tok: int, out_tok: int) -> float:
        # longest matching key wins, so dated ids like "claude-haiku-5-5-20260101" still price correctly
        key = max((k for k in self._pricing if k != "default" and model.startswith(k)), key=len, default="default")
        p = self._pricing.get(key) or Price(input=5.0, output=20.0)
        return (in_tok * p.input + out_tok * p.output) / 1_000_000

    def record(self, model: str, in_tok: int, out_tok: int) -> float:
        c = self.cost(model, in_tok, out_tok)
        with closing(self._db()) as db, db:
            db.execute(
                "INSERT INTO usage VALUES (?,?,?,?,?,?)",
                (time.time(), time.strftime("%Y-%m-%d"), model, in_tok, out_tok, c),
            )
        return c

    def today(self) -> dict[str, Any]:
        with closing(self._db()) as db:
            row = db.execute(
                "SELECT COALESCE(SUM(in_tok+out_tok),0), COALESCE(SUM(cost),0), COUNT(*) FROM usage WHERE day=?",
                (time.strftime("%Y-%m-%d"),),
            ).fetchone()
        return {"tokens": int(row[0]), "cost_usd": round(float(row[1]), 6), "calls": int(row[2])}

    def daily_exceeded(self, b: Budgets) -> str | None:
        t = self.today()
        if t["tokens"] >= b.daily_max_tokens:
            return "daily_tokens"
        if t["cost_usd"] >= b.daily_max_cost_usd:
            return "daily_cost"
        return None
