import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from app.agents.remote import as_message
from app.core import db
from app.core.config import Settings, get_settings
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


def test_engines_use_the_pool_size(monkeypatch):
    monkeypatch.setattr(get_settings(), "db_pool_size", 2)
    engine = db.readonly_engine("postgresql+psycopg://u:p@localhost:5433/smallie")
    assert engine.pool.size() == 2
