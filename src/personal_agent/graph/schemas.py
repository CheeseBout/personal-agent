"""Structured-output schemas and graph state."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from langgraph.graph import MessagesState
from pydantic import BaseModel, Field

IntentKind = Literal[
    "chat", "question", "task", "long_task", "code", "file_op", "research",
    "automation", "screen_action", "memory_op", "settings",
]


class Intent(BaseModel):
    """Classification of the user's request."""

    intent: IntentKind = "task"
    complexity: Literal["low", "medium", "high"] = "medium"
    needs_plan: bool = True
    needs_sandbox: bool = False
    needs_tools: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    confidence: float = 0.5  # 0..1; clamped in code (no JSON-schema bounds: strict modes reject them)
    clarifying_question: str | None = None  # one question, only when the request is too ambiguous to act on


StepRole = Literal["main", "web", "research", "files", "memory", "comms", "coder"]


class Step(BaseModel):
    id: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    parallel: bool = False
    # who does it: "main" = the supervisor itself (writes, sending, composing the answer); others = a sub-agent role
    role: StepRole = "main"


class Goal(BaseModel):
    """What 'done' means, in checkable terms."""

    objective: str
    success_criteria: list[str]  # explicit, checkable definition of done
    constraints: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)


class CriterionResult(BaseModel):
    criterion: str
    met: bool
    evidence: str = ""


class Verdict(BaseModel):
    status: Literal["done", "continue", "replan", "blocked", "ask_user"]
    criteria: list[CriterionResult] = Field(default_factory=list)
    message: str = ""  # what is missing / the question for the user / the blocker


def collect(old: list[dict[str, Any]] | None, new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reducer for parallel worker results; a {'_reset': True} entry clears the list (done by the merge node)."""
    if new and new[0].get("_reset"):
        return []
    return [*(old or []), *new]


class AgentState(MessagesState):
    wave: list[str]  # step ids dispatched to workers in the current wave
    wresults: Annotated[list[dict[str, Any]], collect]  # results of the workers of the current wave
    evidence: str  # first-hand tool outputs gathered by workers (what the verifier checks)
    findings: str  # compact worker summaries (untrusted) shown in the mission block
    worker_runs: int
    lang: str
    intent: dict[str, Any] | None
    goal: dict[str, Any] | None
    summary: str  # rolling summary of compacted turns
    notes: str  # verifier / no-progress feedback shown in the mission block
    verdict: str  # last verdict status; "replan" is also set by no-progress detection
    stop_reason: str  # "" while running, else why the task was stopped
    used: dict[str, Any]  # per-task counters (see limits.new_used)
    replans: int
    final: str
    memories: str  # compact long-term memories retrieved (locally) at the start of the task
    tainted: bool  # an untrusted-output tool ran in this task: later side effects need approval
    gate: dict[str, Any]  # per tool-call policy decision made by the gate node
