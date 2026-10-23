from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.api import routes_chat
from app.api.routes_chat import _events_from, _to_events


def test_model_tokens_become_token_events():
    chunk = (AIMessageChunk(content="Xin chào"), {"langgraph_node": "model"})
    assert _to_events("messages", chunk) == [{"type": "token", "text": "Xin chào"}]


def test_tokens_from_other_nodes_are_ignored():
    assert _to_events("messages", (AIMessageChunk(content="x"), {"langgraph_node": "tools"})) == []


SUBGRAPH = ("sql:1234",)  # namespace of chunks coming from inside an agent


def test_tool_calls_and_results_from_updates():
    call = AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"query": "SELECT 1"}, "id": "c1"}])
    result = ToolMessage(content="| ?column? |", name="run_sql", tool_call_id="c1")
    events = _to_events("updates", {"model": {"messages": [call]}}, SUBGRAPH) + _to_events(
        "updates", {"tools": {"messages": [result]}}, SUBGRAPH
    )
    assert events == [
        {"type": "tool_call", "name": "run_sql", "args": {"query": "SELECT 1"}},
        {"type": "tool_result", "name": "run_sql", "content": "| ?column? |"},
    ]


def test_updates_without_messages_are_ignored():
    assert _to_events("updates", {"prompt_with_memories.before_model": None}, SUBGRAPH) == []


def test_router_choice_becomes_agent_event():
    assert _to_events("updates", {"router": {"route": "sql"}}) == [{"type": "agent", "name": "sql"}]


def test_orchestrator_handoff_becomes_agent_event():
    update = {"orchestrator": {"route": "sql", "findings": ""}}
    assert _to_events("updates", update) == [{"type": "agent", "name": "sql"}]


def test_handoff_tool_calls_are_not_shown():
    call = AIMessage(content="", tool_calls=[{"name": "handoff", "args": {"agent": "sql"}, "id": "c1"}])
    result = ToolMessage(content="Handed off to sql.", name="handoff", tool_call_id="c1")
    namespace = ("orchestrator:1",)
    assert _to_events("updates", {"model": {"messages": [call]}}, namespace) == []
    assert _to_events("updates", {"tools": {"messages": [result]}}, namespace) == []


def test_tokens_of_agents_called_as_tools_are_dropped():
    chunk = (AIMessageChunk(content="Revenue was $1B"), {"langgraph_node": "model"})
    assert _to_events("messages", chunk, ("orchestrator:1", "tools:2")) == []
    assert _to_events("messages", chunk, ("orchestrator:1",)) == [{"type": "token", "text": "Revenue was $1B"}]


def test_top_level_agent_updates_are_not_repeated():
    call = AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"query": "SELECT 1"}, "id": "c1"}])
    assert _to_events("updates", {"sql": {"messages": [call]}}) == []


def test_tokens_of_silent_workflow_steps_are_dropped():
    chunk = (AIMessageChunk(content="FINDINGS: ..."), {"langgraph_node": "model"})
    assert _to_events("messages", chunk, ("research:1", "gather:2", "model:3")) == []
    assert _to_events("messages", chunk, ("research:1", "write:2", "model:3")) == [
        {"type": "token", "text": "FINDINGS: ..."}
    ]


def test_mcp_tool_results_are_shown_as_text():
    result = ToolMessage(content=[{"type": "text", "text": "Article"}], name="knowledge_wikipedia_page", tool_call_id="1")
    events = _to_events("updates", {"tools": {"messages": [result]}}, SUBGRAPH)
    assert events == [{"type": "tool_result", "name": "knowledge_wikipedia_page", "content": "Article"}]


async def test_step_events_only_when_a_workflow_changes_step(monkeypatch):
    monkeypatch.setattr(routes_chat, "STEPS", {"research": frozenset({"gather", "write"})})
    chunk = (AIMessageChunk(content="x"), {"langgraph_node": "model"})

    async def stream():
        yield (), "updates", {"router": {"route": "research"}}
        yield ("research:1", "gather:2"), "messages", chunk
        yield ("research:1", "gather:2", "model:3"), "messages", chunk
        yield ("research:1", "write:4"), "messages", chunk
        yield ("sql:5",), "messages", chunk  # single agents have no steps
        yield ("research:6", "run:7", "gather:8", "model:9"), "messages", chunk  # an agent server's wrapper node

    steps = [e for e in [e async for e in _events_from(stream())] if e["type"] == "step"]
    assert steps == [{"type": "step", "agent": "research", "name": "gather"},
                     {"type": "step", "agent": "research", "name": "write"},
                     {"type": "step", "agent": "research", "name": "gather"}]


CHART = {"id": "c1", "type": "bar", "title": "t", "labels": ["a"], "series": [{"name": "s", "values": [1.0]}]}


def test_chart_results_carry_their_charts_but_not_their_tables():
    result = ToolMessage(content="Chart drawn", name="chart_sql", tool_call_id="1",
                         artifact={"charts": [CHART], "tables": [{"id": "t1", "rows": [[1]]}]})
    # Also from inside an agent the orchestrator called as a tool.
    for namespace in (SUBGRAPH, ("orchestrator:1", "tools:2")):
        events = _to_events("updates", {"tools": {"messages": [result]}}, namespace)
        assert events == [{"type": "tool_result", "name": "chart_sql", "content": "Chart drawn", "charts": [CHART]}]
    plain = ToolMessage(content="rows", name="run_sql", tool_call_id="2", artifact={"tables": [{"id": "t1"}]})
    assert "charts" not in _to_events("updates", {"tools": {"messages": [plain]}}, SUBGRAPH)[0]


def test_history_shows_charts_of_tool_results_and_orchestrator_answers():
    from app.api.routes_conversations import _serialize

    tool = _serialize(ToolMessage(content="Chart drawn", name="chart_table", tool_call_id="1", artifact={"charts": [CHART]}))
    assert tool["charts"] == [CHART]
    answer = _serialize(AIMessage("See the chart.", name="orchestrator", additional_kwargs={"charts": [CHART], "tables": []}))
    assert answer["charts"] == [CHART] and answer["agent"] == "orchestrator" and "tables" not in answer


def test_a_fallback_drops_the_reply_before_the_orchestrator_takes_over():
    update = {"fallback": {"route": "orchestrator", "failed": "docs", "attempt": "...", "messages": []}}
    assert _to_events("updates", update) == [
        {"type": "fallback", "agent": "docs"}, {"type": "agent", "name": "orchestrator"}]


def test_unknown_remote_messages_are_skipped_not_fatal():
    """A remote message of a type the app doesn't know must not end the whole reply."""
    assert _to_events("messages", ({"type": "remove", "id": "x"}, {"langgraph_node": "model"}), SUBGRAPH) == []
    assert _to_events("messages", ({"type": "function", "content": "x"}, {"langgraph_node": "model"}), SUBGRAPH) == []
    single = {"type": "ai", "content": "", "tool_calls": [{"name": "run_sql", "args": {}, "id": "c1"}]}
    assert _to_events("updates", {"model": {"messages": single}}, SUBGRAPH) == [
        {"type": "tool_call", "name": "run_sql", "args": {}}]


def test_remote_dict_chunks_become_events():
    """A remote agent's chunks arrive as JSON dicts; they give the same events as messages do."""
    token = ({"type": "AIMessageChunk", "content": "Hi"}, {"langgraph_node": "model"})
    assert _to_events("messages", token, SUBGRAPH) == [{"type": "token", "text": "Hi"}]
    call = {"type": "ai", "content": "", "tool_calls": [{"name": "run_sql", "args": {"query": "SELECT 1"}, "id": "c1"}]}
    result = {"type": "tool", "content": "| ?column? |", "name": "run_sql", "tool_call_id": "c1"}
    assert _to_events("updates", {"model": {"messages": [call]}, "tools": {"messages": [result]}}, SUBGRAPH) == [
        {"type": "tool_call", "name": "run_sql", "args": {"query": "SELECT 1"}},
        {"type": "tool_result", "name": "run_sql", "content": "| ?column? |"},
    ]


def test_custom_team_events_pass_through():
    for chunk in ({"type": "team", "phase": "delegate"}, {"type": "verdict", "action": "approve"},
                  {"type": "needs_human", "question": "Which year?"}):
        assert _to_events("custom", chunk, SUBGRAPH) == [chunk]


def test_other_custom_chunks_are_dropped():
    assert _to_events("custom", {"type": "progress"}, SUBGRAPH) == []
    assert _to_events("custom", "text", SUBGRAPH) == []


def test_serialize_exposes_team_transcript():
    from app.api.routes_conversations import _serialize

    team = {"entries": [{"agent": "sql", "phase": "delegate", "text": "draft"}]}
    assert _serialize(AIMessage("Final.", name="orchestrator", additional_kwargs={"team": team}))["team"] == team
    assert _serialize(AIMessage("Hi.", name="orchestrator"))["team"] is None


async def test_messages_show_a_pending_question(monkeypatch):
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from app.api import routes_conversations

    monkeypatch.setattr(routes_conversations, "get_conversation", lambda user_id, cid: {"id": cid})
    snapshot = SimpleNamespace(values={"messages": [HumanMessage("compare revenue")]},
                               interrupts=[SimpleNamespace(value={"question": "Which company?"})])

    async def aget_state(config):
        return snapshot

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=SimpleNamespace(aget_state=aget_state))))
    shown = await routes_conversations.messages("c1", request, "u1")
    assert shown[0] == {"role": "user", "content": "compare revenue"}
    assert shown[1] == {"role": "assistant", "agent": "orchestrator", "content": "Which company?", "pending": True,
                        "team": None, "tool_calls": [], "charts": []}
    snapshot.interrupts = []
    assert len(await routes_conversations.messages("c1", request, "u1")) == 1


async def test_paused_turn_is_not_remembered(monkeypatch):
    """A turn that paused on a question saves no history and extracts no facts."""
    from types import SimpleNamespace

    from app.api.routes_chat import ChatRequest, chat

    calls = []

    async def spy(*args, **kwargs):
        calls.append(args)

    async def aget_state(config):
        return SimpleNamespace(values={"messages": [AIMessage("x", name="orchestrator")]},
                               interrupts=[SimpleNamespace(value={"question": "Which?"})])

    async def astream(*args, **kwargs):
        return
        yield

    async def none(*args, **kwargs):
        return ""

    monkeypatch.setattr(routes_chat, "create_conversation", lambda u, k, m: {"id": "c1", "title": "t"})
    monkeypatch.setattr(routes_chat, "touch_conversation", lambda cid: None)
    monkeypatch.setattr(routes_chat, "recall_facts", lambda u: "")
    monkeypatch.setattr(routes_chat, "_recent_chats", none)
    monkeypatch.setattr(routes_chat, "_user", none)
    monkeypatch.setattr(routes_chat, "add_usage", spy)
    monkeypatch.setattr(routes_chat, "save_turn", spy)
    monkeypatch.setattr(routes_chat, "extract_facts", spy)
    monkeypatch.setattr(routes_chat, "_chat_input", lambda *a: none())
    assistant = SimpleNamespace(aget_state=aget_state, astream=astream)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=assistant, store=None)))

    response = await chat("team", ChatRequest(message="compare"), request, "u1")
    async for _ in response.body_iterator:
        pass
    calls.clear()  # add_usage runs for the stream itself
    await response.background()
    assert calls == []
