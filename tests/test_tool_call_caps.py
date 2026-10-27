"""One model reply may not run the same tool call twice or more than MAX_TOOL_CALLS_PER_STEP calls."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.agents import factory
from app.core.memory import AgentContext
from tests.scripted import scripted


async def _run(monkeypatch, queries):
    ran = []

    async def lookup(query: str) -> str:
        ran.append(query)
        return "ok"

    tool = StructuredTool.from_function(coroutine=lookup, name="lookup", description="look something up")
    calls = [{"name": "lookup", "args": {"query": q}, "id": f"c{i}"} for i, q in enumerate(queries)]
    model = scripted(AIMessage("", tool_calls=calls), AIMessage("done"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[tool])
    result = await agent.ainvoke({"messages": [HumanMessage("go")]}, context=AgentContext(user_id="u"))
    return ran, result


async def test_identical_calls_in_one_reply_run_once(monkeypatch):
    ran, _ = await _run(monkeypatch, ["a", "a", "a", "b", "b"])
    assert sorted(ran) == ["a", "b"]


async def test_a_reply_runs_at_most_the_cap(monkeypatch):
    cap = factory.MAX_TOOL_CALLS_PER_STEP
    ran, result = await _run(monkeypatch, [f"q{i}" for i in range(cap + 5)])
    assert sorted(ran) == sorted(f"q{i}" for i in range(cap))
    ai = next(m for m in result["messages"] if isinstance(m, AIMessage) and m.tool_calls)
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(ai.tool_calls) == cap
    assert {c["id"] for c in ai.tool_calls} == {m.tool_call_id for m in tool_messages}


async def test_duplicates_are_dropped_before_the_cap(monkeypatch):
    cap = factory.MAX_TOOL_CALLS_PER_STEP
    queries = ["q0"] * 5 + [f"q{i}" for i in range(1, cap + 1)]
    ran, _ = await _run(monkeypatch, queries)
    assert sorted(ran) == sorted(f"q{i}" for i in range(cap))


async def test_distinct_calls_under_the_cap_run_unchanged(monkeypatch):
    ran, result = await _run(monkeypatch, ["q0", "q1", "q2"])
    assert sorted(ran) == ["q0", "q1", "q2"]
    assert result["messages"][-1].text == "done"
