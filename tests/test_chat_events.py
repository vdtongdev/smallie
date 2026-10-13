from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

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


async def test_step_events_only_when_a_workflow_changes_step():
    chunk = (AIMessageChunk(content="x"), {"langgraph_node": "model"})

    async def stream():
        yield (), "updates", {"router": {"route": "research"}}
        yield ("research:1", "gather:2"), "messages", chunk
        yield ("research:1", "gather:2", "model:3"), "messages", chunk
        yield ("research:1", "write:4"), "messages", chunk
        yield ("sql:5",), "messages", chunk  # single agents have no steps

    steps = [e for e in [e async for e in _events_from(stream())] if e["type"] == "step"]
    assert steps == [{"type": "step", "agent": "research", "name": "gather"},
                     {"type": "step", "agent": "research", "name": "write"}]
