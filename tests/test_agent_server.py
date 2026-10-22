"""The agent server side: entry point, service-token auth, chat history store, call reporting."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.store.memory import InMemoryStore
from langgraph_sdk import Auth

from app.agents import factory, server, server_auth
from app.agents.research import agent as research_agent
from app.agents.remote import CallBudgetExceeded
from app.api.routes_chat import _events_from
from app.core import memory
from app.core.config import get_settings
from app.core.memory import AgentContext, history_namespace
from app.core.usage import UsageCounter
from tests.conftest import requires_db
from tests.scripted import scripted
from tests.test_remote import CTX, HUMAN, FakeRemote, agent

NOTES = "FINDINGS:\n- LangGraph is a library for agent graphs [source: https://example.com/lg]\nGAPS: none"


async def test_unknown_route_is_refused(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTE", "nope")
    monkeypatch.setattr(server, "_GRAPH", None)
    with pytest.raises(ValueError, match="nope"):
        await server.make_graph()


@requires_db
async def test_make_graph_builds_the_route(monkeypatch):
    monkeypatch.setenv("AGENT_ROUTE", "chart")
    monkeypatch.setattr(server, "_GRAPH", None)
    monkeypatch.setattr(memory, "_history_store", None)  # restored after: the store's pool dies with this loop
    graph = await server.make_graph()
    assert "run" in graph.nodes and await server.make_graph() is graph  # built once per process


@pytest.mark.parametrize("token, headers", [
    ("s3cret", {}),
    ("s3cret", {b"x-service-token": b"wrong"}),
    ("", {b"x-service-token": b""}),  # unset on the server: fail closed
    ("", {b"x-service-token": b"anything"}),
])
async def test_auth_rejects_missing_wrong_and_unset_token(monkeypatch, token, headers):
    monkeypatch.setattr(get_settings(), "service_token", token)
    with pytest.raises(Auth.exceptions.HTTPException) as refused:
        await server_auth.authenticate(headers)
    assert refused.value.status_code == 401


async def test_auth_accepts_the_token_and_names_the_user(monkeypatch):
    monkeypatch.setattr(get_settings(), "service_token", "s3cret")
    user = await server_auth.authenticate({b"x-service-token": b"s3cret", b"x-smallie-user-id": b"u-7"})
    assert user["identity"] == "u-7"


async def test_chat_history_uses_the_set_store(monkeypatch):
    app_store = InMemoryStore()
    await app_store.aput(history_namespace("u-7"), "k1", {"text": "Q: Apple price?", "created_at": "2026-10-01",
                                                          "title": "Stocks", "conversation_id": "c1"})
    monkeypatch.setattr(memory, "_history_store", app_store)
    runtime = SimpleNamespace(context=CTX, store=InMemoryStore())  # the agent server's own, empty store
    found = await memory.search_chat_history.coroutine("Apple", runtime)
    assert "Apple price?" in found


async def test_server_reports_usage_of_every_call(monkeypatch):
    """Research keeps its researcher's messages out of the state; its calls are still reported."""
    model = scripted(AIMessage("", tool_calls=[{"name": "load_skill", "args": {"name": "academic"}, "id": "c1"}]),
                     AIMessage(NOTES))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(research_agent, "describe_schema", lambda: "")
    graph = server.reporting_graph(research_agent.build_research_workflow())
    result = await graph.ainvoke({"messages": [HumanMessage("Research LangGraph", id="h1")]}, context=CTX)
    assert result["usage"]["model_calls"] == 2
    assert "LangGraph is a library" in result["findings"]


async def test_reported_usage_reaches_the_counter():
    counter = UsageCounter()
    usage = {"model_calls": 3, "input_tokens": 100, "output_tokens": 20, "cost": 0.0002}
    remote = FakeRemote(output={"messages": [HUMAN], "usage": usage})
    result = await agent(remote).ainvoke({"messages": []}, {"callbacks": [counter]}, context=CTX)
    assert counter.totals() == usage
    assert "usage" not in result  # never into the chat state


async def test_budget_stops_further_specialist_calls(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_message_model_calls", 5)
    counter = UsageCounter()
    counter.add({"model_calls": 5})
    remote = FakeRemote(output={"messages": []})
    with pytest.raises(CallBudgetExceeded, match="5"):
        await agent(remote).ainvoke({"messages": []}, {"callbacks": [counter]}, context=CTX)
    assert remote.calls == []  # refused before calling


async def test_wrapper_keeps_tokens_and_tool_events_streaming(monkeypatch):
    """The `run:` wrapper level must not hide the agent's tokens or tool events from the UI."""
    model = scripted(AIMessage("", tool_calls=[{"name": "search_memory", "args": {"query": "job"}, "id": "c1"}]),
                     AIMessage("Hello there"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(memory, "search_facts", lambda user_id, query: "")
    inner = factory.build_agent(name="sql_agent", system_prompt="x", tools=[])
    parent = StateGraph(MessagesState, context_schema=AgentContext)
    parent.add_node("sql", server.reporting_graph(inner))
    parent.add_edge(START, "sql")
    parent.add_edge("sql", END)
    stream = parent.compile(checkpointer=InMemorySaver()).astream(
        {"messages": [HumanMessage("hi")]}, {"configurable": {"thread_id": "t"}}, context=CTX,
        stream_mode=["messages", "updates"], subgraphs=True)
    events = [event async for event in _events_from(stream)]
    assert {"type": "tool_call", "name": "search_memory", "args": {"query": "job"}} in events
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Hello there"
