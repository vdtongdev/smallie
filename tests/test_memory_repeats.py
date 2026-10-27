"""A memory search whose result equals an earlier result of this run comes back as a short note."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import app.core.memory
from app.agents import factory
from app.core.memory import AgentContext
from app.core.prompts import SAME_RESULT
from tests.scripted import scripted


async def _run(monkeypatch, fake):
    monkeypatch.setattr(app.core.memory, "search_facts", fake)
    calls = [AIMessage("", tool_calls=[{"name": "search_memory", "args": {"query": q}, "id": f"c{q}"}])
             for q in ("a", "b")]
    model = scripted(*calls, AIMessage("done"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[])
    result = await agent.ainvoke({"messages": [HumanMessage("go")]}, context=AgentContext(user_id="u"))
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


async def test_a_repeated_memory_result_is_a_short_note(monkeypatch):
    first, second = await _run(monkeypatch, lambda user_id, query: "- likes tea")
    assert first.text == "- likes tea"
    assert second.text == SAME_RESULT.format(name="search_memory")


async def test_new_memory_results_come_back_in_full(monkeypatch):
    tool_messages = await _run(monkeypatch, lambda user_id, query: f"- fact about {query}")
    assert [m.text for m in tool_messages] == ["- fact about a", "- fact about b"]


async def test_parallel_memory_calls_with_the_same_result_get_the_note(monkeypatch):
    """One model reply with several searches (different queries, same text) runs them together."""
    monkeypatch.setattr(app.core.memory, "search_facts", lambda user_id, query: "- likes tea")
    calls = AIMessage("", tool_calls=[{"name": "search_memory", "args": {"query": q}, "id": f"c{q}"} for q in ("a", "b")])
    model = scripted(calls, AIMessage("done"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[])
    result = await agent.ainvoke({"messages": [HumanMessage("go")]}, context=AgentContext(user_id="u"))
    texts = [m.text for m in result["messages"] if isinstance(m, ToolMessage)]
    assert texts == ["- likes tea", SAME_RESULT.format(name="search_memory")]
