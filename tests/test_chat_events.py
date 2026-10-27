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


def test_team_custom_events_pass_through():
    for chunk in ({"type": "plan", "tasks": [], "fallback": False}, {"type": "plan_updated", "tasks": []},
                  {"type": "wave", "wave": 1, "kind": "plan", "tasks": ["t1"], "agents": ["sql"]},
                  {"type": "review", "challenges": [], "follow_ups": [], "forced": False},
                  {"type": "synthesize"}, {"type": "needs_human", "question": "Which year?"}):
        assert _to_events("custom", chunk, SUBGRAPH) == [chunk]


def test_round_table_events_are_gone():
    for chunk in ({"type": "round", "round": 1, "kind": "first"}, {"type": "board", "round": 1}):
        assert _to_events("custom", chunk, SUBGRAPH) == []


def test_relay_event_types_are_gone():
    for chunk in ({"type": "team", "phase": "delegate"}, {"type": "verdict", "action": "approve"}):
        assert _to_events("custom", chunk, SUBGRAPH) == []


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


TOKEN = {"langgraph_node": "model"}


async def _collect(chunks):
    async def stream():
        for chunk in chunks:
            yield chunk
    return [e async for e in _events_from(stream())]


async def test_round_events_carry_the_agent():
    speaker = lambda agent, ns: ((), "custom", {"type": "speaker", "agent": agent, "ns": ns})  # noqa: E731
    call = AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"query": "SELECT 1"}, "id": "c1"}])
    result = {"type": "tool", "content": "rows", "name": "search", "tool_call_id": "c2"}
    events = await _collect([
        speaker("sql", "team:T|speak:A"), speaker("research", "team:T|speak:B"),
        (("team:T", "speak:A"), "messages", (AIMessageChunk(content="sql says"), TOKEN)),
        (("team:T", "speak:B", "gather:1"), "messages", ({"type": "AIMessageChunk", "content": "notes"}, TOKEN)),
        (("team:T", "speak:A", "tools:2"), "updates", {"model": {"messages": [call]}}),
        (("team:T", "speak:B", "run:3"), "updates", {"tools": {"messages": [result]}}),
        (("team:T", "speak:A"), "messages", (AIMessageChunk(content=" more"), TOKEN)),
    ])
    assert events == [
        {"type": "token", "text": "sql says", "agent": "sql"},
        {"type": "token", "text": "notes", "agent": "research"},
        {"type": "tool_call", "name": "run_sql", "args": {"query": "SELECT 1"}, "agent": "sql"},
        {"type": "tool_result", "name": "search", "content": "rows", "agent": "research"},
        {"type": "token", "text": " more", "agent": "sql"},
    ]


async def test_events_outside_rounds_are_unchanged():
    call = AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {}, "id": "c1"}])
    events = await _collect([
        ((), "updates", {"router": {"route": "sql"}}),
        (("sql:1",), "messages", (AIMessageChunk(content="Hi"), TOKEN)),
        (("sql:1",), "updates", {"model": {"messages": [call]}}),
        (("orchestrator:2", "tools:3"), "messages", (AIMessageChunk(content="dropped"), TOKEN)),
    ])
    assert events == [{"type": "agent", "name": "sql"}, {"type": "token", "text": "Hi"},
                      {"type": "tool_call", "name": "run_sql", "args": {}}]


def test_single_participant_reply_badge():
    from app.api.routes_conversations import _serialize

    assert _serialize(AIMessage("Answer.", name="sql_agent"))["agent"] == "sql"


async def test_real_team_stream_tags_agents(monkeypatch):
    """The live graph: the team is a subgraph node, so speakers sit at ("team:<id>", "speak:<id>", ...)."""
    from tests import test_team
    from tests.scripted import per_agent
    from tests.test_orchestrator import _assistant

    call = AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"query": "SELECT 1"}, "id": "c1"}])
    model = per_agent(sql=[call, AIMessage("sql answer")], research=[AIMessage("research notes")],
                      orchestrator=[AIMessage("final writeup")])
    test_team._board(monkeypatch, test_team.Board())
    assistant = _assistant(monkeypatch, model)
    stream = assistant.astream(test_team._ask(), test_team.CONFIG, context=test_team.TEAM_CONTEXT,
                               stream_mode=["messages", "updates", "custom"], subgraphs=True)
    events = [e async for e in _events_from(stream)]

    def text(agent):
        return "".join(e["text"] for e in events if e["type"] == "token" and e.get("agent") == agent)

    assert text("sql").split() == ["sql", "answer"] and text("research").split() == ["research", "notes"]
    assert any(e["type"] == "tool_call" and e["name"] == "run_sql" and e["agent"] == "sql" for e in events)
    assert any(e["type"] == "tool_result" and e["name"] == "run_sql" and e["agent"] == "sql" for e in events)
    assert not [e for e in events if e["type"] == "speaker"]
    after = events[[e["type"] for e in events].index("synthesize"):]
    writeup = [e for e in after if e["type"] == "token"]
    assert "".join(e["text"] for e in writeup).split() == ["final", "writeup"]
    assert all("agent" not in e for e in writeup)


async def test_lone_participant_reply_reaches_the_answer(monkeypatch):
    """One participant, no write-up: no `synthesize`, and its text arrives in `spoke` (the route sends `done` after the stream)."""
    from tests import test_team
    from tests.scripted import per_agent
    from tests.test_orchestrator import _assistant

    test_team._research_does_not_join(monkeypatch)
    test_team._board(monkeypatch, test_team.Board())
    # A speaker answers from its source: the fake calls its source tool (Task 6).
    assistant = _assistant(monkeypatch, per_agent(), sql=test_team.Fake("sql", "Cap is 3T.", log=[]))
    stream = assistant.astream(test_team._ask(test_team.SQL_QUESTION), test_team.CONFIG, context=test_team.TEAM_CONTEXT,
                               stream_mode=["messages", "updates", "custom"], subgraphs=True)
    events = [e async for e in _events_from(stream)]

    assert not [e for e in events if e["type"] == "synthesize"]
    assert any(e["type"] == "spoke" and e["text"] == "Cap is 3T." for e in events)


def test_spoke_event_passes_through():
    chunk = {"type": "spoke", "agent": "sql", "round": 1, "text": "x", "tools": []}
    assert _to_events("custom", chunk, SUBGRAPH) == [chunk]


async def test_speaker_task_splits_two_tasks_of_one_agent():
    speaker = lambda task, ns: ((), "custom", {"type": "speaker", "agent": "sql", "task": task, "ns": ns})  # noqa: E731
    events = await _collect([
        speaker("t1", "team:T|speak:A"), speaker("t2", "team:T|speak:B"),
        (("team:T", "speak:A"), "messages", (AIMessageChunk(content="one"), TOKEN)),
        (("team:T", "speak:B"), "messages", (AIMessageChunk(content="two"), TOKEN)),
    ])
    assert events == [{"type": "token", "text": "one", "agent": "sql", "task": "t1"},
                      {"type": "token", "text": "two", "agent": "sql", "task": "t2"}]


async def test_specialist_tool_events_inside_an_ask_name_their_agent(monkeypatch):
    """The activity popup lists tool calls under the agent that made them: when the orchestrator
    asks sql as a tool, sql's own tool events say which agent ran them."""
    from langchain_core.messages import HumanMessage
    from tests.scripted import per_agent
    from tests.test_orchestrator import CONFIG, _assistant, _call, _fake_db
    from app.core.memory import AgentContext

    _fake_db(monkeypatch)
    model = per_agent(
        orchestrator=[_call("ask_sql", {"question": "AAPL close?"}), AIMessage("AAPL closed at 250.")],
        sql=[_call("run_sql", {"query": "SELECT ticker, close FROM market.daily_prices"}), AIMessage("AAPL 250.")],
    )
    assistant = _assistant(monkeypatch, model)
    stream = assistant.astream({"messages": [HumanMessage("which tech company is the most valuable?")]}, CONFIG,
                               context=AgentContext(user_id="u1"), stream_mode=["messages", "updates"], subgraphs=True)
    events = [e async for e in _events_from(stream)]

    inner = [e for e in events if e["type"] in ("tool_call", "tool_result") and e["name"] == "run_sql"]
    assert len(inner) == 2
    assert all(e.get("agent") == "sql" for e in inner)


async def test_parallel_asks_name_the_agent_behind_each_tool_step(monkeypatch):
    """Two specialists asked in one orchestrator step run side by side: each one's tool events carry its own agent."""
    from langchain_core.messages import HumanMessage
    from tests.scripted import per_agent
    from tests.test_orchestrator import CONFIG, _assistant, _call, _fake_db
    from app.core.memory import AgentContext

    _fake_db(monkeypatch)
    model = per_agent(
        orchestrator=[AIMessage("", tool_calls=[
            {"name": "ask_sql", "args": {"question": "AAPL close?"}, "id": "c1"},
            {"name": "ask_research", "args": {"question": "AAPL news?"}, "id": "c2"},
        ]), AIMessage("done")],
        sql=[_call("run_sql", {"query": "SELECT ticker, close FROM market.daily_prices"}), AIMessage("AAPL 250.")],
        research=[_call("tavily_tavily_search", {"query": "AAPL news"}), AIMessage("news")],
    )
    assistant = _assistant(monkeypatch, model)
    stream = assistant.astream({"messages": [HumanMessage("which tech company is the most valuable?")]}, CONFIG,
                               context=AgentContext(user_id="u1"), stream_mode=["messages", "updates"], subgraphs=True)
    events = [e async for e in _events_from(stream)]

    seen = {(e["name"], e.get("agent")) for e in events if e["type"] in ("tool_call", "tool_result")}
    assert ("run_sql", "sql") in seen and ("tavily_tavily_search", "research") in seen
