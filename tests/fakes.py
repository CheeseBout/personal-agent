from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from personal_agent.graph.schemas import Goal, Intent, Verdict

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class ScriptedModel(FakeMessagesListChatModel):
    """Replays `responses` cyclically; bind_tools is a no-op."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        result = super()._generate(*args, **kwargs)
        # Real models return a fresh message (new id) per call; replaying one object would be
        # de-duplicated by add_messages instead of appended.
        for g in result.generations:
            g.message = g.message.model_copy(update={"id": None})
        return result


def call(name: str, args: dict[str, Any], id: str = "c1", tokens: int = 0) -> AIMessage:
    usage = {"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens} if tokens else None
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id}], usage_metadata=usage)


def answer(text: str, tokens: int = 0) -> AIMessage:
    usage = {"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens} if tokens else None
    return AIMessage(text, usage_metadata=usage)


QUESTION = Intent(intent="question", needs_plan=False, risk_level="low", confidence=0.9)
TASK = Intent(intent="task", needs_plan=True, risk_level="low", confidence=0.9)
GOAL = Goal(objective="read the file", success_criteria=["the answer states the file content"])
DONE = Verdict(status="done", criteria=[{"criterion": "the answer states the file content", "met": True}])  # type: ignore[list-item]


class FakeBrain:
    """Scripted stand-in for LiveBrain. `calls` records (role, schema-or-'chat') for assertions."""

    def __init__(self, worker: Runnable[Any, Any] | None = None, intents: Sequence[Intent] = (QUESTION,),
                 goals: Sequence[Goal] = (GOAL,), verdicts: Sequence[Verdict] = (DONE,),
                 summary: str = "SUMMARY") -> None:
        self.worker = worker or ScriptedModel(responses=[AIMessage("ok")])
        self.queues: dict[str, list[BaseModel]] = {
            "Intent": list(intents), "Goal": list(goals), "Verdict": list(verdicts)}
        self.summary = summary
        self.calls: list[tuple[str, str]] = []

    def count(self, what: str) -> int:
        return sum(1 for _, w in self.calls if w == what)

    def chat(self, role: str, tools: Sequence[Any] = ()) -> Runnable[Any, Any]:
        self.calls.append((role, "chat"))
        if role == "summarizer":
            return RunnableLambda(lambda m: AIMessage(self.summary, usage_metadata=USAGE))
        return self.worker

    def structured(self, role: str, schema: type[BaseModel]) -> Runnable[Any, Any]:
        def run(_: Any) -> dict[str, Any]:
            self.calls.append((role, schema.__name__))
            q = self.queues[schema.__name__]
            parsed = q.pop(0) if len(q) > 1 else q[0]  # the last item repeats forever
            return {"raw": AIMessage("", usage_metadata=USAGE), "parsed": parsed}

        return RunnableLambda(run)

    def model_name(self, role: str) -> str:
        return "claude-haiku-5-5"
