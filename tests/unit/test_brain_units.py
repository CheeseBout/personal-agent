import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from personal_agent.graph.context import split_for_compaction
from personal_agent.graph.lang import detect_lang
from personal_agent.graph.limits import exceeded, new_used, repeating
from personal_agent.llm.errors import classify_provider_error
from personal_agent.observability.usage import Meter
from personal_agent.settings.settings import Budgets, Price


def test_detect_lang() -> None:
    assert detect_lang("Xin chào, bạn khỏe không?") == "vi"
    assert detect_lang("toi can ban giup") == "vi"  # no diacritics
    assert detect_lang("please ban this user") == "en"  # one stray word is not enough
    assert detect_lang("hello") == "en"
    assert detect_lang("hello", override="vi") == "vi"


def test_repeating_and_exceeded() -> None:
    assert repeating(["a", "b", "a", "c", "a"])
    assert not repeating(["a", "b", "c", "a", "b", "c"])
    assert not repeating(["a"] * 3 + list("bcdefg"))  # old repeats fall out of the window
    u = new_used()
    b = Budgets(max_steps=2, max_seconds=5)
    assert exceeded(u, b) is None
    u["steps"] = 2
    assert exceeded(u, b) == "steps"
    u["steps"], u["started"] = 0, time.time() - 10
    assert exceeded(u, b) == "seconds"


def test_meter_prices_by_prefix_and_daily_totals(tmp_path: Path) -> None:
    m = Meter(tmp_path / "u.db", {"claude-haiku-5-5": Price(input=0.1, output=0.5), "default": Price(input=5, output=20)})
    assert m.cost("claude-haiku-5-5-20261001", 1_000_000, 1_000_000) == 0.6  # dated id still priced as haiku
    assert m.cost("mystery-model", 1_000_000, 0) == 5.0  # unknown models are never free
    m.record("claude-haiku-5-5", 600, 400)
    assert m.today()["tokens"] == 1000 and m.today()["calls"] == 1
    b = Budgets(daily_max_tokens=1000)
    assert m.daily_exceeded(b) == "daily_tokens"
    assert m.daily_exceeded(Budgets()) is None


def test_provider_error_classification() -> None:
    assert classify_provider_error(RuntimeError("credit balance is too low"))[0] == "quota_exhausted"  # type: ignore[index]
    assert classify_provider_error(RuntimeError("insufficient_quota"))[0] == "quota_exhausted"  # type: ignore[index]
    assert classify_provider_error(RuntimeError("HTTP 429"))[0] == "rate_limited"  # type: ignore[index]
    assert classify_provider_error(RuntimeError("something odd")) is None


def test_compaction_keeps_the_current_request_verbatim() -> None:
    msgs = [HumanMessage("old"), AIMessage("old answer"), HumanMessage("CURRENT REQUEST")]
    for i in range(3):
        msgs += [AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": str(i)}]), ToolMessage("r", tool_call_id=str(i))]
    old = split_for_compaction(msgs, keep_last=2)  # naive cut (index 6) would summarise the request away
    assert old is not None and len(old) == 2 and msgs[len(old)].content == "CURRENT REQUEST"
    assert split_for_compaction([HumanMessage("only")] + msgs[3:], keep_last=2) is None  # nothing before the request


def test_compaction_never_orphans_tool_results() -> None:
    msgs = [HumanMessage("q"), AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
            ToolMessage("r", tool_call_id="1"), AIMessage("a"), HumanMessage("q2"), AIMessage("a2")]
    old = split_for_compaction(msgs, keep_last=3)  # naive cut would start the tail at a ToolMessage
    assert old is not None and not isinstance(msgs[len(old)], ToolMessage)
    assert split_for_compaction(msgs, keep_last=10) is None
