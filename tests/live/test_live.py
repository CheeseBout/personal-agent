"""Real-provider checks (cost a few cents). Run explicitly: `uv run pytest -m live -s`."""

import os
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from personal_agent.graph.builder import build_graph
from personal_agent.graph.schemas import Intent
from personal_agent.llm.factory import get_chat_model
from personal_agent.observability.usage import Meter
from personal_agent.settings.settings import data_dir, load_settings, set_agent_name

pytestmark = pytest.mark.live
ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest points ENV_FILE at nothing; here we want the user's real .env (repo root or %APPDATA%)
    for p in (ROOT / ".env", Path(os.environ.get("APPDATA", "")) / "personal-agent" / ".env"):
        if p.is_file():
            monkeypatch.setenv("PERSONAL_AGENT_ENV_FILE", str(p))
            return
    pytest.skip("no .env found")


def graph() -> tuple[Any, Meter]:
    s = load_settings()
    m = Meter(data_dir() / "u.db", s.pricing)
    return build_graph(InMemorySaver(), None, s, m), m


async def ask(g: Any, text: str, tid: str) -> dict[str, Any]:
    out: dict[str, Any] = await g.ainvoke({"messages": [HumanMessage(text)]}, {"configurable": {"thread_id": tid}})
    return out


async def test_structured_output_returns_raw_usage_and_parsed() -> None:
    r = await get_chat_model("classifier", schema=Intent).ainvoke("Classify: 'chào bạn'")
    assert isinstance(r["parsed"], Intent) and r["parsed"].needs_plan is False
    assert r["raw"].usage_metadata["input_tokens"] > 0


async def test_fallback_takes_over_when_anthropic_fails() -> None:
    from personal_agent.settings.settings import ModelRef, ModelSpec, Settings

    bad = {"provider": "anthropic", "model": "claude-does-not-exist"}  # stands in for 'credit exhausted'
    fb = [ModelRef(**f.model_dump()) for f in load_settings().models["worker"].fallbacks]
    s = Settings(models={"worker": ModelSpec(**bad, max_tokens=300, fallbacks=fb),
                         "classifier": ModelSpec(**bad, max_tokens=300, fallbacks=fb)})
    try:
        r = await get_chat_model("worker", settings=s).ainvoke("Reply with exactly: pong")
    except Exception as e:  # noqa: BLE001
        # The mechanism is covered offline; this only checks that real fallback accounts are usable.
        pytest.skip(f"every fallback provider is unavailable right now ({type(e).__name__}: out of credit / 503?)")
    assert "pong" in str(r.content).lower()  # answered by the fallback chain, not Anthropic
    # structured output (classifier/planner/verifier) must fall back too
    out = await get_chat_model("classifier", settings=s, schema=Intent).ainvoke("Classify: 'chào bạn'")
    assert isinstance(out["parsed"], Intent)


async def test_rename_and_language_with_real_model() -> None:
    g, m = graph()
    set_agent_name(load_settings(), "Mimi")
    vi = await ask(g, "Bạn là ai? Giới thiệu ngắn thôi.", "vi")
    en = await ask(g, "Who are you? One sentence.", "en")
    print("\nVI:", vi["final"], "\nEN:", en["final"], "\nusage:", m.today())
    assert "Mimi" in vi["final"] and "Mimi" in en["final"]
    assert vi["lang"] == "vi" and en["lang"] == "en"
    assert vi["goal"] is None  # chit-chat took the cheap fast path (no planner / verifier)
    assert any(w in vi["final"].lower() for w in ("tôi", "mình", "là"))  # answered in Vietnamese


async def test_planned_task_with_tool_and_verifier(env: Path) -> None:
    (env / "hello.txt").write_text("the secret word is banana-7731", encoding="utf-8")
    g, m = graph()
    out = await ask(g, "Read hello.txt in my workspace and tell me the secret word.", "task")
    print("\nFINAL:", out["final"], "\nGOAL:", out["goal"]["success_criteria"], "\nused:", out["used"], m.today())
    assert "banana-7731" in out["final"] and out["verdict"] == "done" and not out["stop_reason"]
    assert out["used"]["tool_calls"] >= 1
    assert m.today()["cost_usd"] < 0.15  # whole task, planner+worker+verifier
