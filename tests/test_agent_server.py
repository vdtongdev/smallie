"""The agent server side: entry point, service-token auth, chat history store, call reporting."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.store.memory import InMemoryStore
from langgraph_sdk import Auth

from app.agents import factory, registry, server, server_auth
from app.agents.delegation import make_ask_tool
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
    # An earlier turn in the thread: its tool call must not be shown again.
    earlier = [HumanMessage("AAPL?", id="u0"),
               AIMessage("", tool_calls=[{"name": "run_sql", "args": {"query": "select 1"}, "id": "old"}], id="a0"),
               ToolMessage("| 1 |", tool_call_id="old", name="run_sql", id="t0"), AIMessage("AAPL is 1.", id="a1")]
    stream = parent.compile(checkpointer=InMemorySaver()).astream(
        {"messages": [*earlier, HumanMessage("hi", id="u1")]}, {"configurable": {"thread_id": "t"}}, context=CTX,
        stream_mode=["messages", "updates"], subgraphs=True)
    events = [event async for event in _events_from(stream)]
    tools = [(e["type"], e["name"]) for e in events if e["type"] in ("tool_call", "tool_result")]
    assert tools == [("tool_call", "search_memory"), ("tool_result", "search_memory")]  # once, no replay
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Hello there"


async def test_run_returns_only_the_new_messages(monkeypatch):
    model = scripted(AIMessage("Hello there"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    run = server.reporting_graph(factory.build_agent(name="sql_agent", system_prompt="x", tools=[]))
    updates = [chunk async for chunk in run.astream({"messages": [HumanMessage("hi", id="u1")]}, context=CTX,
                                                    stream_mode="updates")]
    assert [m.text for m in updates[-1]["run"]["messages"]] == ["Hello there"]


@pytest.mark.parametrize("value", [b"\xc3\xa9", b"\xff\xfe"])  # non-ASCII, and not even UTF-8
async def test_auth_refuses_odd_header_bytes_with_401(monkeypatch, value):
    monkeypatch.setattr(get_settings(), "service_token", "s3cret")
    with pytest.raises(Auth.exceptions.HTTPException) as refused:
        await server_auth.authenticate({b"x-service-token": value})
    assert refused.value.status_code == 401


async def test_parallel_first_runs_build_the_graph_once(monkeypatch):
    builds = []

    async def build():
        builds.append(1)
        await asyncio.sleep(0.01)
        return "graph"

    monkeypatch.setattr(server, "_GRAPH", None)
    monkeypatch.setattr(server, "_build", build)
    assert await asyncio.gather(server.make_graph(), server.make_graph()) == ["graph", "graph"]
    assert builds == [1]


async def test_budget_on_the_ask_path_still_lets_the_orchestrator_answer(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_message_model_calls", 3)
    counter = UsageCounter()
    counter.add({"model_calls": 3})
    remote = FakeRemote(output={"messages": []})
    ask = make_ask_tool(registry.get("sql"), agent(remote))
    answer, _ = await ask.coroutine("AAPL P/E?", SimpleNamespace(context=CTX, config={"callbacks": [counter]}))
    assert answer == "The sql agent failed (CallBudgetExceeded); it has no answer." and remote.calls == []


def test_agent_server_does_not_import_fastapi():
    """Dockerfile.agent doesn't install fastapi (its otel pin clashes with the Agent Server's)."""
    import subprocess
    import sys
    code = "import sys, app.agents.server; print('fastapi' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


async def test_server_run_hides_tools_outside_sources(monkeypatch):
    """The context reaches an agent server as JSON (RemoteAgent sends asdict(context)); a team
    request's sources must still hide the tools of other sources from the nested researcher."""
    import json
    from dataclasses import asdict

    model = scripted(AIMessage(NOTES))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(research_agent, "describe_schema", lambda: "")
    graph = server.reporting_graph(research_agent.build_research_workflow())
    context = json.loads(json.dumps(asdict(AgentContext(user_id="u1", sources=("web",)))))
    await graph.ainvoke({"messages": [HumanMessage("Research LangGraph", id="h1")]}, context=context)
    offered = {t["function"]["name"] for t in model.tools[-1]}
    assert "run_sql" not in offered and "search_documents" not in offered
