import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import openai
import pytest
from langchain_core.callbacks import AsyncCallbackManager
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.pregel.remote import RemoteException, RemoteGraph

from pathlib import Path

from app.agents import registry
from app.agents.artifacts import collect
from app.agents.delegation import make_ask_tool
from app.agents.registry import AGENTS, ROUTES
from app.agents.remote import RemoteAgent, as_message, make_client, remote_agents
from app.core import db
from app.core.config import Settings, get_settings
from app.core.memory import AgentContext
from app.core.usage import UsageCounter

AI_DICT = {
    "type": "ai", "content": "AAPL pays 0.4%.", "name": "sql_agent", "id": "m1",
    "tool_calls": [{"name": "run_sql", "args": {"query": "select 1"}, "id": "call1", "type": "tool_call"}],
    "usage_metadata": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    "response_metadata": {"cost": 0.0001}, "additional_kwargs": {"tables": [{"id": "t1"}]},
    "invalid_tool_calls": [], "example": False,
}
TOOL_DICT = {
    "type": "tool", "content": "| x |\n| 1 |", "name": "run_sql", "tool_call_id": "call1", "id": "m2",
    "artifact": {"tables": [{"id": "t1", "rows": [[1]]}]}, "status": "success",
}


def test_agent_url_map_parses_json():
    assert Settings(_env_file=None, agent_urls='{"sql": "http://localhost:2102"}').agent_url_map == {
        "sql": "http://localhost:2102"}
    assert Settings(_env_file=None, agent_urls="").agent_url_map == {}
    with pytest.raises(ValueError, match="AGENT_URLS"):
        Settings(_env_file=None, agent_urls="not json").agent_url_map


@pytest.mark.parametrize("kind, cls", [
    ("ai", AIMessage), ("AIMessageChunk", AIMessageChunk), ("human", HumanMessage),
    ("system", SystemMessage), ("tool", ToolMessage),
])
def test_as_message_rebuilds_each_type(kind, cls):
    value = {"type": kind, "content": "hi", **({"tool_call_id": "c1"} if kind == "tool" else {})}
    message = as_message(value)
    assert type(message) is cls and message.text == "hi"
    assert as_message(message) is message


def test_as_message_keeps_usage_artifact_and_kwargs():
    ai = as_message(AI_DICT)
    assert ai.usage_metadata == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
    assert ai.response_metadata == {"cost": 0.0001}
    assert ai.additional_kwargs == {"tables": [{"id": "t1"}]}  # usage did not land here
    assert ai.tool_calls[0]["name"] == "run_sql" and ai.name == "sql_agent" and ai.id == "m1"
    tool = as_message(TOOL_DICT)
    assert tool.artifact == {"tables": [{"id": "t1", "rows": [[1]]}]}
    assert (tool.name, tool.tool_call_id) == ("run_sql", "call1")


def test_usage_counter_counts_one_message():
    counter = UsageCounter()
    counter.count(as_message(AI_DICT))
    assert (counter.input_tokens, counter.output_tokens, counter.cost) == (11, 7, 0.0001)


class FakeRemote:
    """Stands in for RemoteGraph: records each call, returns `output` or raises `error`."""

    def __init__(self, output=None, error=None, delay=0.0):
        self.output, self.error, self.delay, self.calls = output, error, delay, []

    async def ainvoke(self, input, config=None, *, context=None, headers=None):
        self.calls.append({"input": input, "config": config, "context": context, "headers": headers})
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.output


SPEC = SimpleNamespace(route="sql")
CTX = AgentContext(user_id="u-7", memories="- likes AAPL", mentioned=("sql",))
HUMAN = {"type": "human", "content": "AAPL yield?", "id": "h1"}


def agent(remote, timeout_s: float = 5) -> RemoteAgent:
    return RemoteAgent(SPEC, "http://sql:8000", service_token="t", timeout_s=timeout_s, remote=remote)


async def test_sends_context_and_user_header_without_thread_id():
    remote = FakeRemote(output={"messages": [HUMAN]})
    config = {"configurable": {"thread_id": "c-42", "checkpoint_ns": "sql:abc", "__pregel_stream": "hook"}}
    await agent(remote).node({"messages": [as_message(HUMAN)]}, config, SimpleNamespace(context=CTX))
    call = remote.calls[0]
    assert call["context"] == asdict(CTX)
    assert call["headers"] == {"x-smallie-user-id": "u-7"}
    # Stateless: no thread. checkpoint_ns stays: RemoteGraph prefixes streamed chunks with it.
    assert call["config"]["configurable"] == {"checkpoint_ns": "sql:abc", "__pregel_stream": "hook"}


async def test_returns_only_new_messages_as_objects():
    remote = FakeRemote(output={"messages": [HUMAN, AI_DICT, TOOL_DICT], "findings": "notes"})
    result = await agent(remote).node({"messages": [as_message(HUMAN)]}, {}, SimpleNamespace(context=CTX))
    assert [type(m) for m in result["messages"]] == [AIMessage, ToolMessage]
    assert result["findings"] == "notes"


async def test_keeps_table_artifact_for_plot_that():
    remote = FakeRemote(output={"messages": [HUMAN, TOOL_DICT]})
    result = await agent(remote).ainvoke({"messages": [as_message(HUMAN)]}, context=CTX)
    assert collect(result["messages"])["tables"] == [{"id": "t1", "rows": [[1]]}]


@pytest.mark.parametrize("wrap", [lambda c: [c], lambda c: AsyncCallbackManager(handlers=[c])])
async def test_counts_usage_of_returned_ai_messages(wrap):
    counter = UsageCounter()
    remote = FakeRemote(output={"messages": [HUMAN, AI_DICT, TOOL_DICT]})
    await agent(remote).node({"messages": [as_message(HUMAN)]}, {"callbacks": wrap(counter)},
                             SimpleNamespace(context=CTX))
    assert (counter.input_tokens, counter.output_tokens, counter.cost) == (11, 7, 0.0001)


@pytest.mark.parametrize("name, expected", [
    ("RateLimitError", openai.RateLimitError), ("AuthenticationError", openai.AuthenticationError),
    ("ValueError", RemoteException),
])
async def test_remote_errors_become_openai_errors(name, expected):
    remote = FakeRemote(error=RemoteException({"error": name, "message": "An internal error occurred"}))
    with pytest.raises(expected):
        await agent(remote).ainvoke({"messages": []}, context=CTX)


async def test_unreachable_agent_raises_quickly():
    remote = FakeRemote(error=httpx.ConnectError("connection refused"))
    with pytest.raises(httpx.ConnectError):
        await agent(remote).node({"messages": []}, {}, SimpleNamespace(context=CTX))
    assert len(remote.calls) == 1


async def test_remote_state_cannot_steer_the_parent():
    remote = FakeRemote(output={"messages": [HUMAN], "findings": "f", "route": "sql", "direct": True,
                                "failed": "docs", "attempt": "x"})
    result = await agent(remote).node({"messages": [as_message(HUMAN)]}, {}, SimpleNamespace(context=CTX))
    assert set(result) == {"messages", "findings"}


async def test_sdk_client_does_not_retry_runs():
    requests = []

    def unavailable(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"detail": "starting"})

    client = make_client("http://sql:8000", service_token="t", timeout_s=5, transport=httpx.MockTransport(unavailable))
    with pytest.raises(httpx.HTTPStatusError):
        await RemoteGraph("agent", client=client).ainvoke({"messages": []}, context=asdict(CTX))
    assert len(requests) == 1
    assert requests[0].headers["x-service-token"] == "t" and "x-api-key" not in requests[0].headers


async def test_no_final_state_is_a_clear_error():
    with pytest.raises(RuntimeError, match="sql agent"):
        await agent(FakeRemote(output=None)).ainvoke({"messages": []}, context=CTX)


async def test_a_stuck_agent_call_times_out():
    # The httpx timeout is per read: a server sending keep-alives could otherwise hold a call forever.
    with pytest.raises(TimeoutError):
        await agent(FakeRemote(output={"messages": []}, delay=1), timeout_s=0.05).ainvoke({"messages": []}, context=CTX)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def test_real_remote_graph_as_a_node_is_stateless_and_streams_into_the_parent():
    """The real RemoteGraph inside a parent graph, as routes_chat runs it: guards what a LangGraph
    upgrade could change (no thread on the agent server, chunks under the node's namespace)."""
    requests = []

    def server(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = (_sse("metadata", {"run_id": "r1"})
                + _sse("messages", [{"type": "AIMessageChunk", "content": "ans", "id": "a1"}, {"langgraph_node": "model"}])
                + _sse("values", {"messages": [HUMAN, AI_DICT]}))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    client = make_client("http://sql:8000", service_token="t", timeout_s=5, transport=httpx.MockTransport(server))
    remote = agent(RemoteGraph("agent", client=client))
    graph = StateGraph(MessagesState, context_schema=AgentContext)
    graph.add_node("sql", remote.node)
    graph.add_edge(START, "sql")
    graph.add_edge("sql", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "c-42"}}
    chunks = [chunk async for chunk in app.astream({"messages": [as_message(HUMAN)]}, config, context=CTX,
                                                    stream_mode=["messages", "updates"], subgraphs=True)]

    [request] = requests
    sent = json.loads(request.content)
    assert request.url.path == "/runs/stream"  # no /threads/<id>/: stateless
    assert not any(key.startswith(("thread_id", "checkpoint", "__pregel")) for key in sent["config"]["configurable"])
    assert request.headers["x-smallie-user-id"] == "u-7"
    assert any(ns and ns[0].startswith("sql:") and mode == "messages" for ns, mode, _ in chunks)
    saved = (await app.aget_state(config)).values["messages"]
    assert [m.id for m in saved] == ["h1", "m1"] and saved[1].usage_metadata["output_tokens"] == 7


def test_missing_agent_url_names_the_routes(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_urls", '{"sql": "http://x"}')
    with pytest.raises(RuntimeError) as error:
        remote_agents(AGENTS)
    assert all(route in str(error.value) for route in ("docs", "chart", "research"))
    assert "sql" not in str(error.value).split(":", 1)[1]


async def test_ask_tool_failure_still_answers():
    remote = agent(FakeRemote(error=httpx.ConnectError("connection refused")))
    ask = make_ask_tool(registry.get("sql"), remote)
    answer, artifact = await ask.coroutine("AAPL P/E?", SimpleNamespace(context=CTX, config={}))
    assert answer == "The sql agent failed (ConnectError); it has no answer." and artifact == {}


def test_specialists_cannot_import_remote_graph():
    """Agents talk only through the main app (no agent-to-agent loops)."""
    root = Path(__file__).resolve().parent.parent / "app" / "agents"
    for route in ROUTES:
        for path in (root / route).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not any(name in text for name in ("RemoteGraph", "langgraph.pregel.remote", "app.agents.remote")), path


def test_engines_use_the_pool_size(monkeypatch):
    monkeypatch.setattr(get_settings(), "db_pool_size", 2)
    engine = db.readonly_engine("postgresql+psycopg://u:p@localhost:5433/smallie")
    assert engine.pool.size() == 2
