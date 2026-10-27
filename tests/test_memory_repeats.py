"""A memory search whose result equals an earlier result of this run comes back as a short note."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import app.core.memory
from app.agents import factory
from app.agents.research.agent import cite_by_id
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



class _History:
    """search_chat_history's store: returns the given items, in the given order, one list per call."""

    def __init__(self, *orders):
        self.orders = iter(orders)

    async def asearch(self, namespace, query=None, limit=5, refresh_ttl=False):
        return [SimpleNamespace(value={"created_at": "2026-10-27T02:23+00:00", "title": "c", "text": f"Q: {t}\nA: see https://x.com/{t}"})
                for t in next(self.orders)]


async def _history_run(monkeypatch, *orders):
    """The research agent's middleware (cite_by_id) around search_chat_history, one search per order."""
    monkeypatch.setattr(app.core.memory, "_history_store", _History(*orders))
    calls = [AIMessage("", tool_calls=[{"name": "search_chat_history", "args": {"query": f"q{i}"}, "id": f"c{i}"}])
             for i in range(len(orders))]
    model = scripted(*calls, AIMessage("done"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[], middleware=[cite_by_id])
    result = await agent.ainvoke({"messages": [HumanMessage("go")]}, context=AgentContext(user_id="u"))
    return [m.text for m in result["messages"] if isinstance(m, ToolMessage)]


async def test_the_same_chats_in_another_order_are_the_same_result(monkeypatch):
    """The store ranks by query, so a second query returns the same chats in a new order."""
    first, second = await _history_run(monkeypatch, ["a", "b", "c"], ["a", "c", "b"])
    assert second == SAME_RESULT.format(name="search_chat_history")


async def test_a_search_with_a_new_chat_comes_back_in_full(monkeypatch):
    first, second = await _history_run(monkeypatch, ["a", "b", "c"], ["a", "b", "d"])
    assert "Q: d" in second
