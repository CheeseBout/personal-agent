from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from personal_agent.graph.schemas import Goal, Intent, Verdict
from personal_agent.memory.reflect import Reflection

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


class StepModel:
    """Scripted sub-agent model. The reply depends on WHICH STEP the worker was given (read from its task message) and
    on how many AI turns it already had, so parallel workers stay deterministic. Records what it was shown."""

    def __init__(self, scripts: dict[str, list[AIMessage]], delay: float = 0.0, on_call: Any = None,
                 main_reply: str = "main loop done") -> None:
        import threading

        self.scripts, self.delay, self.on_call, self.main_reply = scripts, delay, on_call, main_reply
        self.seen: dict[str, list[Any]] = {}  # step id -> messages of its LAST call
        self.times: dict[str, list[float]] = {}  # step id -> [first start, last end]
        self.running = self.max_running = 0
        self._lock = threading.Lock()

    def bind_tools(self, tools: Any, **kw: Any) -> "StepModel":
        return self

    async def ainvoke(self, msgs: Any, *a: Any, **k: Any) -> AIMessage:
        import asyncio
        import re
        import time

        found = re.search(r"Your step \((\w+)\)", str(msgs[1].content)) if len(msgs) > 1 else None
        if found is None:  # not a worker call (e.g. a coder worker shares the main loop's model role)
            return AIMessage(self.main_reply, usage_metadata=USAGE)
        sid = found.group(1)
        n_ai = sum(1 for m in msgs if m.type == "ai")
        with self._lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
            self.seen[sid] = list(msgs)
            self.times.setdefault(sid, [time.time(), 0.0])
        try:
            if self.on_call:
                self.on_call(sid, n_ai)
            if self.delay:
                await asyncio.sleep(self.delay)
            script = self.scripts.get(sid) or [AIMessage(f"result of {sid}", usage_metadata=USAGE)]
            reply = script[min(n_ai, len(script) - 1)]
            if isinstance(reply, Exception):
                raise reply
            return reply.model_copy(update={"id": None})
        finally:
            with self._lock:
                self.running -= 1
                self.times[sid][1] = time.time()


class HashEmbedder:
    """Deterministic offline embedder: hashed bag-of-words, L2-normalised. Similar words => similar vectors."""

    id = "fake:hash:64"
    dim = 64

    def __init__(self) -> None:
        self.calls = 0
        self.fail: Exception | None = None

    def embed(self, texts: list[str], query: bool = False) -> list[list[float]]:
        import hashlib
        import math
        import re

        if self.fail is not None:
            raise self.fail
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class FakeBrain:
    """Scripted stand-in for LiveBrain. `calls` records (role, schema-or-'chat') for assertions."""

    def __init__(self, worker: Runnable[Any, Any] | None = None, intents: Sequence[Intent] = (QUESTION,),
                 goals: Sequence[Goal] = (GOAL,), verdicts: Sequence[Verdict] = (DONE,),
                 summary: str = "SUMMARY", reflections: Sequence[Reflection] = (Reflection(episode="Did the task."),),
                 role_models: dict[str, Runnable[Any, Any]] | None = None) -> None:
        self.worker = worker or ScriptedModel(responses=[AIMessage("ok")])
        self.role_models = role_models or {}  # e.g. {"fast": StepModel(...)} for sub-agent workers
        self.tool_sets: list[tuple[str, list[str]]] = []  # (role, tool names) of every chat() call
        self.inputs: dict[str, list[Any]] = {}  # schema name -> the messages each structured() call received
        self.queues: dict[str, list[BaseModel]] = {
            "Intent": list(intents), "Goal": list(goals), "Verdict": list(verdicts), "Reflection": list(reflections)}
        self.summary = summary
        self.calls: list[tuple[str, str]] = []

    def count(self, what: str) -> int:
        return sum(1 for _, w in self.calls if w == what)

    def chat(self, role: str, tools: Sequence[Any] = ()) -> Runnable[Any, Any]:
        self.calls.append((role, "chat"))
        self.tool_sets.append((role, sorted(t.name for t in tools)))
        if role == "summarizer":
            return RunnableLambda(lambda m: AIMessage(self.summary, usage_metadata=USAGE))
        return self.role_models.get(role, self.worker)

    def structured(self, role: str, schema: type[BaseModel]) -> Runnable[Any, Any]:
        def run(inp: Any) -> dict[str, Any]:
            self.calls.append((role, schema.__name__))
            self.inputs.setdefault(schema.__name__, []).append(inp)
            q = self.queues[schema.__name__]
            parsed = q.pop(0) if len(q) > 1 else q[0]  # the last item repeats forever
            return {"raw": AIMessage("", usage_metadata=USAGE), "parsed": parsed}

        return RunnableLambda(run)

    def model_name(self, role: str) -> str:
        return "claude-haiku-5-5"
