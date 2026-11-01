"""Local metrics callback, usage report, and Langfuse gating (no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from fakes import ScriptedModel
from personal_agent.observability.metrics import Metrics, MetricsCallback
from personal_agent.observability.tracing import Tracing
from personal_agent.observability.usage import Meter
from personal_agent.settings.settings import ObservabilityCfg, Price, Settings


@tool
def good(x: str) -> str:
    """ok"""
    return "fine"


@tool
def bad(x: str) -> str:
    """boom"""
    raise ValueError("nope")


async def test_callback_records_llm_and_tool_success_and_errors(tmp_path: Path) -> None:
    m = Metrics(tmp_path / "u.db")
    cfg: Any = {"callbacks": [MetricsCallback(m)]}
    await ScriptedModel(responses=[AIMessage("hi")]).ainvoke("q", cfg)
    await good.ainvoke({"x": "a"}, cfg)
    with pytest.raises(ValueError, match="nope"):
        await bad.ainvoke({"x": "a"}, cfg)
    d = m.report(1)["days"][0]
    assert d["llm_calls"] == 1 and d["llm_error_rate"] == 0.0
    assert d["tool_calls"] == 2 and d["tool_success_rate"] == 0.5
    assert {t["tool"] for t in m.report(1)["tools"]} == {"good", "bad"}


def test_report_joins_tokens_and_cost_per_day(tmp_path: Path) -> None:
    Meter(tmp_path / "u.db", {"default": Price(input=1, output=1)}).record("x", 1000, 500)
    d = Metrics(tmp_path / "u.db").report(7)["days"][0]
    assert d["tokens"] == 1500 and d["cost_usd"] == pytest.approx(0.0015) and d["llm_error_rate"] is None


def test_old_events_are_pruned(tmp_path: Path) -> None:
    m = Metrics(tmp_path / "u.db")
    m.event("llm", "x", True, 1)
    Metrics(tmp_path / "u.db", keep_days=-1)  # cutoff in the future: everything is "old" (keep_days=0 raced the ~15 ms Windows clock)
    assert m.report(1)["days"] == []


def test_langfuse_stays_off_without_keys_or_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("personal_agent.observability.tracing.get_secret", lambda n: None)
    on = Settings(observability=ObservabilityCfg(langfuse_enabled=True))
    t = Tracing(on, Metrics(tmp_path / "u.db"))
    assert not t.langfuse_on and len(t.run_config("t1", 50)["callbacks"]) == 1  # metrics only
    off = Tracing(Settings(), Metrics(tmp_path / "u.db"))
    assert not off.langfuse_on and off.run_config("t1", 50)["configurable"] == {"thread_id": "t1"}


def test_langfuse_mask_redacts_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class Fake:
        def __init__(self, **kw: Any) -> None:
            seen.update(kw)

    monkeypatch.setattr("personal_agent.observability.tracing.get_secret", lambda n: "k" if "KEY" in n else None)
    monkeypatch.setattr("langfuse.Langfuse", Fake)
    t = Tracing(Settings(observability=ObservabilityCfg(langfuse_enabled=True)), None)
    assert t.langfuse_on
    assert seen["mask"]({"api_key": "x", "t": "use sk-abcdefghijkl now"}) == {"api_key": "***", "t": "use *** now"}
