from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.api.routes_chat import _to_events


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
