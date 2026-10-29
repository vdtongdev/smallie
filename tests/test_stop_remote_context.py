import json

import httpx
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.pregel.remote import RemoteGraph

from app.core.usage import UsageCounter
from tests.test_remote import AI_DICT, CTX, HUMAN, _sse, agent, as_message, make_client


def _server(requests):
    def server(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = _sse("metadata", {"run_id": "r1"}) + _sse("values", {"messages": [HUMAN, AI_DICT]})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())
    return server


async def _app(requests, interrupt_before=None):
    client = make_client("http://sql:8000", service_token="t", timeout_s=5, transport=httpx.MockTransport(_server(requests)))
    remote = agent(RemoteGraph("agent", client=client))
    graph = StateGraph(MessagesState, context_schema=type(CTX))
    graph.add_node("first", lambda s: {})
    graph.add_node("sql", remote.node)
    graph.add_edge(START, "first")
    graph.add_edge("first", "sql")
    graph.add_edge("sql", END)
    return graph.compile(checkpointer=InMemorySaver(), interrupt_before=interrupt_before)


def _cfg(thread):
    return {"configurable": {"thread_id": thread}, "callbacks": [UsageCounter()], "metadata": {"user_id": "u"}}


def _assert_ok(sent):
    assert not (sent["config"].get("configurable") and sent.get("context")), \
        f"configurable={sent['config'].get('configurable')} with context"


async def test_fresh_message_sends_either_configurable_or_context_to_the_agent_server():
    requests = []
    app = await _app(requests)
    [c async for c in app.astream({"messages": [as_message(HUMAN)]}, _cfg("t1"), context=CTX)]
    _assert_ok(requests[0])


async def test_resumed_run_sends_either_configurable_or_context_to_the_agent_server():
    requests = []
    app = await _app(requests, interrupt_before=["sql"])  # stands in for a Stop between nodes
    [c async for c in app.astream({"messages": [as_message(HUMAN)]}, _cfg("t2"), context=CTX)]
    assert not requests
    [c async for c in app.astream(None, _cfg("t2"), context=CTX)]
    _assert_ok(requests[0])


async def test_team_command_resume_reaches_the_agent_server_without_configurable():
    from langgraph.types import Command, interrupt
    requests = []
    client = make_client("http://sql:8000", service_token="t", timeout_s=5, transport=httpx.MockTransport(_server(requests)))
    remote = agent(RemoteGraph("agent", client=client))

    def ask_user(state):
        interrupt("question")
        return {}

    graph = StateGraph(MessagesState, context_schema=type(CTX))
    graph.add_node("ask_user", ask_user)
    graph.add_node("sql", remote.node)
    graph.add_edge(START, "ask_user")
    graph.add_edge("ask_user", "sql")
    graph.add_edge("sql", END)
    app = graph.compile(checkpointer=InMemorySaver())
    [c async for c in app.astream({"messages": [as_message(HUMAN)]}, _cfg("t3"), context=CTX)]
    assert not requests
    [c async for c in app.astream(Command(resume="answer"), _cfg("t3"), context=CTX)]
    _assert_ok(requests[0])


async def test_resume_after_a_steer_note_reaches_the_agent_server_without_configurable():
    from app.orchestration.stopping import add_note
    requests = []
    app = await _app(requests, interrupt_before=["sql"])
    [c async for c in app.astream({"messages": [as_message(HUMAN)]}, _cfg("t4"), context=CTX)]
    await add_note(app, _cfg("t4"), "use 2024 data")
    [c async for c in app.astream(None, _cfg("t4"), context=CTX)]
    _assert_ok(requests[0])


async def test_resumed_ask_tool_reaches_the_agent_server_without_configurable():
    from langchain_core.messages import AIMessage
    from langgraph.prebuilt import ToolNode
    from app.agents.delegation import make_ask_tool
    from app.agents.sql.agent import SPEC as SQL_SPEC
    requests = []
    client = make_client("http://sql:8000", service_token="t", timeout_s=5, transport=httpx.MockTransport(_server(requests)))
    remote = agent(RemoteGraph("agent", client=client))
    call = AIMessage("", tool_calls=[{"name": "ask_sql", "args": {"question": "AAPL yield?"}, "id": "c1"}])
    graph = StateGraph(MessagesState, context_schema=type(CTX))
    graph.add_node("first", lambda s: {"messages": [call]})
    graph.add_node("tools", ToolNode([make_ask_tool(SQL_SPEC, remote)]))
    graph.add_edge(START, "first")
    graph.add_edge("first", "tools")
    graph.add_edge("tools", END)
    app = graph.compile(checkpointer=InMemorySaver(), interrupt_before=["tools"])
    [c async for c in app.astream({"messages": [as_message(HUMAN)]}, _cfg("t5"), context=CTX)]
    assert not requests
    [c async for c in app.astream(None, _cfg("t5"), context=CTX)]
    _assert_ok(requests[0])
