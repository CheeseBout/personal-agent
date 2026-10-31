from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from fakes import ScriptedModel, call
from personal_agent.graph.builder import build_graph, render_system


def cfg(tid: str) -> Any:
    return {"configurable": {"thread_id": tid}, "recursion_limit": 20}


async def test_react_loop_uses_tool_and_answers(env: Path) -> None:
    (env / "a.txt").write_text("secret-42")
    model = ScriptedModel(responses=[call("file_read", {"path": "a.txt"}), AIMessage("File says secret-42")])
    g = build_graph(InMemorySaver(), lambda tools: model)
    out = await g.ainvoke({"messages": [HumanMessage("read a.txt")]}, cfg("t1"))
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs[0].content == "secret-42"
    assert out["messages"][-1].content == "File says secret-42"


async def test_unknown_tool_is_denied_not_executed() -> None:
    model = ScriptedModel(responses=[call("format_disk", {}), AIMessage("sorry")])
    g = build_graph(InMemorySaver(), lambda tools: model)
    out = await g.ainvoke({"messages": [HumanMessage("do it")]}, cfg("t2"))
    denied = next(m for m in out["messages"] if isinstance(m, ToolMessage))
    assert denied.status == "error" and "DENIED" in str(denied.content)


async def test_tool_error_is_returned_to_model() -> None:
    model = ScriptedModel(responses=[call("file_read", {"wrong_arg": 1}), AIMessage("recovered")])
    g = build_graph(InMemorySaver(), lambda tools: model)
    out = await g.ainvoke({"messages": [HumanMessage("x")]}, cfg("t3"))
    assert out["messages"][-1].content == "recovered"


async def test_checkpointer_keeps_thread_history() -> None:
    g = build_graph(InMemorySaver(), lambda tools: ScriptedModel(responses=[AIMessage("a"), AIMessage("b")]))
    await g.ainvoke({"messages": [HumanMessage("one")]}, cfg("t4"))
    out = await g.ainvoke({"messages": [HumanMessage("two")]}, cfg("t4"))
    assert [m.content for m in out["messages"]] == ["one", "a", "two", "b"]


async def test_system_prompt_uses_configured_agent_name() -> None:
    seen: list[Any] = []

    def model(msgs: Any) -> AIMessage:
        seen.append(msgs)
        return AIMessage("ok")

    g = build_graph(InMemorySaver(), lambda tools: RunnableLambda(model))
    await g.ainvoke({"messages": [HumanMessage("hi")]}, cfg("t5"))
    assert "personal-agent" in str(seen[0][0].content)
    assert "Mimi" in render_system("Mimi") and "{{" not in render_system("Mimi")
