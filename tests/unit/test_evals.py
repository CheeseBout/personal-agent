"""Eval harness logic (scoring, loading, summary, compare) and one end-to-end run on a scripted brain."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from fakes import FakeBrain, ScriptedModel, answer
from personal_agent.evals import check, compare, load_scenarios, run_scenarios, summarize

SCENARIOS = Path("tests/evals/scenarios.yaml")


def res(**kw: object) -> dict[str, object]:
    base = {"final": "", "tools": [], "paused": False, "steps": 1, "cost": 0.001}
    return {**base, **kw}


def test_shipped_scenarios_are_valid_and_cover_both_languages() -> None:
    items = load_scenarios(SCENARIOS)
    assert {i["lang"] for i in items} == {"vi", "en"} and len({i["id"] for i in items}) == len(items) >= 8
    assert [i["id"] for i in load_scenarios(SCENARIOS, lang="vi")] and all(i["lang"] == "vi" for i in load_scenarios(SCENARIOS, lang="vi"))


def test_unknown_check_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text("- {id: x, lang: en, prompt: hi, expect: {contain_any: [a]}}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown check"):
        load_scenarios(p)


@pytest.mark.parametrize(("expect", "r", "ok"), [
    ({"contains_any": ["Hà Nội"]}, res(final="Thủ đô là HÀ NỘI."), True),  # case-insensitive, Vietnamese kept intact
    ({"contains_any": ["Hà Nội"]}, res(final="Thủ đô là Huế."), False),
    ({"contains_all": ["a", "b"]}, res(final="a only"), False),
    ({"not_contains": ["deleted"]}, res(final="I deleted it"), False),
    ({"tools_any": ["web_search"]}, res(tools=["web_search"]), True),
    ({"tools_none": ["shell"]}, res(tools=["shell"]), False),
    ({"reply_lang": "vi"}, res(final="Xin chào bạn"), True),
    ({"reply_lang": "vi"}, res(final="Hello there"), False),
    ({"paused": True}, res(paused=False), False),
    ({"max_steps": 3}, res(steps=4), False),
    ({"max_cost_usd": 0.01}, res(cost=0.02), False),
    ({}, res(), True),
])
def test_check(expect: dict[str, object], r: dict[str, object], ok: bool) -> None:
    assert (check(expect, r) == []) is ok


def test_summary_and_compare() -> None:
    a = [{"id": "x", "lang": "en", "passed": True, "steps": 2, "cost": 0.01},
         {"id": "y", "lang": "vi", "passed": False, "steps": 4, "cost": 0.03}]
    s = summarize(a)
    assert s["all"]["pass_rate"] == 0.5 and s["vi"]["pass_rate"] == 0.0 and s["all"]["cost_usd"] == 0.04
    b = [{**a[0]}, {**a[1], "passed": True}]
    lines = compare({"summary": s, "results": a}, {"summary": summarize(b), "results": b})
    assert "50% -> 100%" in lines[0] and any("y: FAIL->PASS" in line for line in lines)


async def test_end_to_end_on_a_scripted_brain() -> None:
    sc = [{"id": "ok", "lang": "en", "prompt": "17*23?", "expect": {"contains_any": ["391"], "max_steps": 3}},
          {"id": "bad", "lang": "en", "prompt": "17*23?", "expect": {"contains_any": ["999"]}}]
    out = await run_scenarios(sc, brain=FakeBrain(ScriptedModel(responses=[answer("391")])))
    assert [r["passed"] for r in out] == [True, False] and out[1]["failures"] and out[0]["error"] is None


async def test_a_crashing_scenario_fails_without_stopping_the_run() -> None:
    class Boom(ScriptedModel):
        def _generate(self, *a: object, **k: object) -> object:
            raise RuntimeError("provider down")

    out = await run_scenarios([{"id": "boom", "lang": "en", "prompt": "hi"}], brain=FakeBrain(Boom(responses=[AIMessage("x")])))
    assert out[0]["passed"] is False and "provider down" in out[0]["error"]
