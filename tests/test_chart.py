import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agents.chart import tools
from app.agents.chart.spec import MAX_POINTS, evenly, make_spec, summary
from app.agents.chart.tools import chart_data, chart_sql, chart_table, rows_to_spec
from app.core.memory import AgentContext
from tests.conftest import requires_db

PRICES = {
    "id": "t1", "query": "SELECT ...", "truncated": False,
    "columns": ["trade_date", "ticker", "close"],
    "rows": [["2026-10-12", "AAPL", 250.0], ["2026-10-12", "MSFT", 500.0],
             ["2026-10-13", "AAPL", 252.5], ["2026-10-13", "MSFT", None]],
}
WIDE = {"id": "t2", "query": "SELECT ...", "truncated": False,
        "columns": ["ticker", "market_cap", "pe", "sector"],
        "rows": [["AAPL", 4e12, 30.1, "Tech"], ["MSFT", 3.9e12, 35.0, "Tech"]]}


def _call(tool, args: dict) -> dict:
    return {"type": "tool_call", "name": tool.name, "id": "c1", "args": args}


def test_spec_rules_come_back_as_errors_the_model_can_fix():
    ok = make_spec(type="bar", title="t", labels=["a", "b"], series=[{"name": "s", "values": [1, 2]}])
    assert ok.id.startswith("c") and ok.labels == ["a", "b"]
    assert "has 1 values for 2 labels" in make_spec(type="bar", title="t", labels=["a", "b"],
                                                    series=[{"name": "s", "values": [1]}])
    too_many = [{"name": str(i), "values": [1]} for i in range(9)]
    assert make_spec(type="line", title="t", labels=["a"], series=too_many).startswith("Error:")
    two = [{"name": "x", "values": [1]}, {"name": "y", "values": [2]}]
    assert "exactly one series" in make_spec(type="pie", title="t", labels=["a"], series=two)
    assert "numeric x values" in make_spec(type="scatter", title="t", labels=["a"], series=[{"name": "s", "values": [1]}])
    four = [{"name": str(i), "values": [1]} for i in range(4)]
    assert "at most 3 series" in make_spec(type="scatter", title="t", labels=[1], series=four)


def test_big_pies_keep_the_largest_slices_and_fold_the_rest():
    labels = [f"s{i}" for i in range(10)]
    spec = make_spec(type="pie", title="t", labels=labels, series=[{"name": "share", "values": list(range(10))}])
    assert spec.labels == ["s9", "s8", "s7", "s6", "s5", "s4", "s3", "Other"]
    assert spec.series[0].values[-1] == 0 + 1 + 2  # s0..s2


def test_long_series_are_downsampled_keeping_both_ends():
    n = MAX_POINTS * 3
    spec = make_spec(type="line", title="t", labels=list(range(n)), series=[{"name": "s", "values": list(range(n))}])
    assert len(spec.labels) <= MAX_POINTS and spec.labels[0] == 0 and spec.labels[-1] == n - 1
    assert spec.series[0].values[-1] == n - 1 and spec.downsampled_from == n
    assert evenly(3, 10) == [0, 1, 2]
    assert "(downsampled from 1500)" in summary(spec)


def test_long_rows_pivot_into_one_series_per_value():
    spec = rows_to_spec(PRICES, "line", "Close", "trade_date", ["close"], "ticker")
    assert spec.labels == ["2026-10-12", "2026-10-13"]
    assert [(s.name, s.values) for s in spec.series] == [("AAPL", [250.0, 252.5]), ("MSFT", [500.0, None])]
    assert spec.y_label == "close" and spec.x_label == "trade_date"
    text = summary(spec)
    assert "- AAPL: first 250.00, last 252.50" in text and "- MSFT: first 500.00, last n/a" in text


def test_numeric_columns_are_the_default_y_and_column_errors_list_the_valid_ones():
    spec = rows_to_spec(WIDE, "bar", "Size", "ticker")
    assert [s.name for s in spec.series] == ["market_cap", "pe"]  # not sector (text)
    assert "4.00T" in summary(spec)
    assert rows_to_spec(WIDE, "bar", "t", "name") == (
        "Error: unknown column(s) name; the result has: ticker, market_cap, pe, sector.")
    assert "exactly one y column" in rows_to_spec(PRICES, "line", "t", "trade_date", ["close", "close"], "ticker")
    assert "no rows" in rows_to_spec({**WIDE, "rows": []}, "bar", "t", "ticker")


def test_chart_data_draws_inline_numbers():
    message = chart_data.invoke(_call(chart_data, {
        "type": "bar", "title": "Revenue", "labels": ["2024", "2025"],
        "series": [{"name": "Revenue", "values": [1.0, 1.2]}]}))
    chart = message.artifact["charts"][0]
    assert chart["labels"] == ["2024", "2025"] and chart["series"][0]["values"] == [1.0, 1.2]
    assert message.content.startswith('Chart drawn for the user (bar, "Revenue")')
    # The series schema reaches the model inline (no unresolved $ref).
    assert "$ref" not in json.dumps(convert_to_openai_tool(chart_data)["function"]["parameters"]["properties"])


async def test_chart_table_finds_earlier_results_by_id(monkeypatch):
    messages = [
        HumanMessage("prices?"),
        ToolMessage("rows", tool_call_id="0", artifact={"tables": [PRICES]}),
        AIMessage("answer", additional_kwargs={"tables": [WIDE]}),  # an orchestrator answer of an earlier turn
    ]
    runtime = SimpleNamespace(state={"messages": messages})

    async def run(table_id):
        return await chart_table.coroutine(table_id=table_id, type="bar", title="t", x_column="ticker", runtime=runtime)

    content, artifact = await run("t2")
    assert content.startswith("Chart drawn") and artifact["charts"][0]["labels"] == ["AAPL", "MSFT"]
    content, artifact = await run("t9")
    assert content == "Error: no table 't9' in this chat. Available: t1, t2." and artifact == {}


def _empty_chat():
    return SimpleNamespace(state={"messages": []})


async def test_chart_sql_errors_come_back_as_text(monkeypatch):
    monkeypatch.setattr(tools, "query_readonly", lambda sql: "Error: relation \"x\" does not exist")
    content, artifact = await chart_sql.coroutine(query="SELECT * FROM x", type="bar", title="t", x_column="a",
                                                  runtime=_empty_chat())
    assert content.startswith("Error: relation") and not artifact


async def test_the_same_query_reuses_the_earlier_table_without_querying(monkeypatch):
    monkeypatch.setattr(tools, "query_readonly", lambda sql: (_ for _ in ()).throw(AssertionError("queried again")))
    earlier = {**PRICES, "query": "SELECT trade_date, ticker, close\nFROM market.daily_prices;"}
    runtime = SimpleNamespace(state={"messages": [ToolMessage("rows", tool_call_id="0", artifact={"tables": [earlier]})]})
    content, artifact = await chart_sql.coroutine(
        query="select trade_date,  ticker, close from market.daily_prices", type="line", title="Close",
        x_column="trade_date", y_columns=["close"], series_column="ticker", runtime=runtime)
    assert content.startswith("(Reused table t1 from earlier in this chat")
    assert artifact["charts"][0]["table_id"] == "t1" and "tables" not in artifact


def test_listed_tables_say_how_they_were_already_charted():
    from app.agents.artifacts import table_note

    spec = rows_to_spec(PRICES, "line", "AAPL vs MSFT", "trade_date", ["close"], "ticker")
    assert spec.table_id == "t1"
    note = table_note([PRICES, WIDE], [spec.model_dump()])
    first, second = note.splitlines()
    assert first.endswith('; already charted as line "AAPL vs MSFT"')
    assert "already charted" not in second


async def test_chart_tables_note_in_the_tail(monkeypatch):
    from app.agents import factory
    from app.agents.chart import agent as chart_agent
    from app.core.memory import AgentContext
    from app.core.prompts import CALL_NOTE
    from tests.scripted import scripted

    model = scripted(AIMessage("Same data as a line."))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(chart_agent.sql_agent, "describe_schema", lambda: "")
    call = {"name": "chart_sql", "args": {}, "id": "c1"}
    messages = [HumanMessage("Chart AAPL vs MSFT"), AIMessage("", tool_calls=[call]),
                ToolMessage("drawn", tool_call_id="c1", name="chart_sql", artifact={"tables": [PRICES]}),
                AIMessage("Done."), HumanMessage("plot that as a line")]
    await chart_agent.build_chart_agent().ainvoke({"messages": messages}, context=AgentContext(user_id="u"))
    prompt = model.prompts[0]
    assert prompt[-1].name == CALL_NOTE and "TABLES IN THIS CHAT (" in prompt[-1].content
    assert "TABLES IN THIS CHAT (" not in prompt[0].content  # the system prompt only names the heading


@requires_db
async def test_chart_sql_charts_a_real_query_and_keeps_its_table():
    content, artifact = await chart_sql.coroutine(
        query="SELECT n AS day, n * 2 AS value FROM generate_series(1, 5) AS n",
        type="line", title="Doubling", x_column="day", runtime=_empty_chat())
    chart, table = artifact["charts"][0], artifact["tables"][0]
    assert chart["labels"] == [1, 2, 3, 4, 5] and chart["series"][0]["values"] == [2, 4, 6, 8, 10]
    assert table["columns"] == ["day", "value"] and chart["table_id"] == table["id"]
    refused, _ = await chart_sql.coroutine(query="DELETE FROM market.stocks", type="bar", title="t",
                                           x_column="a", runtime=_empty_chat())
    assert refused.startswith("Error: only a single SELECT")


@pytest.fixture
def saved_chat():
    """A user and chat of their own whose `chat_data` holds PRICES from an earlier turn."""
    from sqlalchemy import delete

    from app.core.chat_memory import save_entries
    from app.core.conversations import create_conversation
    from app.core.db import get_engine, init_db, users

    init_db()
    user_id = f"test-chart-{uuid.uuid4()}"
    with get_engine().begin() as conn:
        conn.execute(users.insert().values(id=user_id, display_name="Test"))
    conversation_id = create_conversation(user_id, "auto", "hello")["id"]
    earlier = {**PRICES, "query": "SELECT trade_date, ticker, close FROM market.daily_prices"}
    save_entries(user_id, conversation_id, 1, "sql", "sql", [
        {"tool": "run_sql", "args": {}, "content": "rows", "artifacts": {"tables": [earlier]}}])
    yield AgentContext(user_id=user_id, conversation_id=conversation_id)
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id == user_id))


@requires_db
async def test_chart_table_finds_a_table_from_an_earlier_turn(saved_chat):
    runtime = SimpleNamespace(state={"messages": []}, context=saved_chat)
    content, artifact = await chart_table.coroutine(
        table_id="t1", type="line", title="t", x_column="trade_date", y_columns=["close"], series_column="ticker",
        runtime=runtime)
    assert content.startswith("Chart drawn") and artifact["charts"][0]["table_id"] == "t1"


@requires_db
async def test_chart_sql_reuses_a_saved_same_query_table(saved_chat, monkeypatch):
    monkeypatch.setattr(tools, "query_readonly", lambda sql: (_ for _ in ()).throw(AssertionError("queried again")))
    runtime = SimpleNamespace(state={"messages": []}, context=saved_chat)
    content, artifact = await chart_sql.coroutine(
        query="select trade_date, ticker, close from market.daily_prices", type="line", title="Close",
        x_column="trade_date", y_columns=["close"], series_column="ticker", runtime=runtime)
    assert content.startswith("(Reused table t1 from earlier in this chat")
    assert artifact["charts"][0]["table_id"] == "t1"


@requires_db
async def test_tables_section_lists_saved_tables(saved_chat):
    from app.agents.chart.agent import tables_section

    request = SimpleNamespace(messages=[HumanMessage("plot that")], runtime=SimpleNamespace(context=saved_chat))
    section = await tables_section(request)
    assert "TABLES IN THIS CHAT (" in section and "- t1: 4 rows" in section


def test_known_tables_current_turn_wins_and_comes_last(monkeypatch):
    from app.core import chat_memory

    old_t1, t3 = {**PRICES, "query": "old"}, {**WIDE, "id": "t3"}
    monkeypatch.setattr(chat_memory, "chat_tables", lambda user, chat: ([old_t1, WIDE], [{"id": "c1"}]))
    messages = [ToolMessage("rows", tool_call_id="0", artifact={"tables": [PRICES, t3]})]
    tables, charts = chat_memory.known_tables(messages, SimpleNamespace(user_id="u", conversation_id="x"))
    assert [t["id"] for t in tables] == ["t2", "t1", "t3"] and tables[1]["query"] == "SELECT ..."
    assert [c["id"] for c in charts] == ["c1"]
    # without a chat, only the messages' items
    tables, _ = chat_memory.known_tables(messages, SimpleNamespace(user_id="u", conversation_id=""))
    assert [t["id"] for t in tables] == ["t1", "t3"]
