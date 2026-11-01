from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from test_api_hitl import app_client, connect, until

from fakes import TASK, USAGE, FakeBrain, StepModel
from personal_agent.graph.schemas import Goal, Step

THREE = [Step(id="s1", description="find fact A", role="web"), Step(id="s2", description="find fact B", role="research"),
         Step(id="s3", description="find fact C", role="files"), Step(id="s4", description="write the comparison")]


def goal_of(*steps: Step) -> Goal:
    return Goal(objective="compare the things", success_criteria=["the answer compares the things"], steps=list(steps))


def test_plan_roles_and_worker_events_are_streamed(tmp_path: Path) -> None:
    brain = FakeBrain(RunnableLambda(lambda m: AIMessage("composed", usage_metadata=USAGE)), intents=[TASK], goals=[goal_of(*THREE)],
                      role_models={"fast": StepModel({"s2": [AIMessage("B secret=sk-abcdefghijk12345", usage_metadata=USAGE)]})})
    with app_client(tmp_path, brain=brain) as c:
        ws = connect(c)
        ws.send_json({"text": "compare things", "thread_id": "t"})
        evs: list[dict[str, Any]] = until(ws)
    kinds = [e["type"] for e in evs]
    plan = next(e for e in evs if e["type"] == "plan")
    assert plan["roles"] == ["web", "research", "files", "main"] and plan["steps"][0] == "find fact A"
    workers = [e for e in evs if e["type"] == "worker"]
    assert sorted(w["step"] for w in workers) == ["s1", "s2", "s3"] and all(w["status"] == "done" for w in workers)
    assert kinds.index("plan") < kinds.index("worker") < kinds.index("verdict") < kinds.index("final")
    assert "abcdefghijk" not in str(workers)  # worker summaries are redacted before they reach the client
    assert evs[-1]["text"] == "composed" and evs[-1]["used"]["tokens"] >= 30
