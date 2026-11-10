"""A model that answers with unusable JSON must hand over to the next model in the chain."""

from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from personal_agent.llm.factory import StructuredParseError, _require_parsed


class S(BaseModel):
    x: int


def test_unparsed_output_raises_and_parsed_passes() -> None:
    with pytest.raises(StructuredParseError):
        _require_parsed({"raw": None, "parsed": None, "parsing_error": ValueError()})
    assert _require_parsed({"parsed": S(x=1)})["parsed"] == S(x=1)


async def test_fallback_runs_after_a_failed_parse() -> None:
    bad = RunnableLambda(lambda _: {"parsed": None, "parsing_error": ValueError()}) | RunnableLambda(_require_parsed)
    good = RunnableLambda(lambda _: {"parsed": S(x=2)})
    out = await bad.with_fallbacks([good]).ainvoke("hi")
    assert out["parsed"] == S(x=2)
