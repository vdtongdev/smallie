import pytest

from app.agents.sql.tools import describe_schema, execute_readonly
from tests.conftest import requires_db


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "insert into customers(full_name) values ('x')",
        "SELECT 1; DROP TABLE customers",
        "  update products set stock = 0",
    ],
)
def test_non_select_statements_are_refused_before_reaching_db(sql):
    assert execute_readonly(sql, max_rows=10).startswith("Error: only a single SELECT")


@requires_db
def test_select_returns_pipe_table():
    out = execute_readonly("SELECT n AS id, 'x' || n AS name FROM generate_series(1, 5) AS n", max_rows=10)
    assert out.splitlines()[0] == "| id | name |"
    assert "(5 rows)" in out


@requires_db
def test_rows_are_capped():
    out = execute_readonly("SELECT generate_series(1, 10) AS n", max_rows=3)
    assert "truncated to the first 3" in out


@requires_db
def test_readonly_role_blocks_writes_even_inside_cte():
    out = execute_readonly("WITH x AS (DELETE FROM market.stocks RETURNING ticker) SELECT * FROM x", max_rows=5)
    assert out.startswith("Error:")
    assert "read-only" in out or "permission denied" in out


@requires_db
def test_readonly_role_cannot_see_app_tables():
    out = execute_readonly("SELECT * FROM public.documents", max_rows=5)
    assert out.startswith("Error:")


@requires_db
def test_schema_description_lists_market_tables():
    schema = describe_schema()
    assert "CREATE TABLE market.stocks (" in schema
    assert "REFERENCES market.stocks.ticker" in schema
    assert "-- sample rows:" in schema


@requires_db
def test_readonly_role_can_read_market_schema():
    out = execute_readonly("SELECT count(*) AS n FROM market.stocks", max_rows=5)
    assert out.splitlines()[0] == "| n |"


@requires_db
async def test_run_sql_keeps_every_row_as_a_table_artifact(monkeypatch):
    from app.agents.sql import tools
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "sql_max_rows", 3)
    call = {"type": "tool_call", "name": "run_sql", "id": "c1", "args": {
        "query": "SELECT n, n * 1.5::numeric AS half, DATE '2026-01-01' + n AS day FROM generate_series(1, 10) AS n"}}
    message = await tools.run_sql.ainvoke(call)
    table = message.artifact["tables"][0]
    # The model sees 3 rows and the table's id; the artifact has all 10, JSON-safe.
    assert message.content.endswith(f"(3 rows, truncated to the first 3; table {table['id']})")
    assert table["columns"] == ["n", "half", "day"]
    assert len(table["rows"]) == 10 and table["rows"][0] == [1, 1.5, "2026-01-02"]
    refused = await tools.run_sql.ainvoke({**call, "args": {"query": "DELETE FROM market.stocks"}})
    assert refused.content.startswith("Error: only a single SELECT") and not refused.artifact


def test_artifacts_are_collected_once_per_id_from_tools_and_answers():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from app.agents.artifacts import artifacts_from, collect, table_note

    t1 = {"id": "t1", "query": "SELECT 1", "columns": ["a"], "rows": [[1], [2], [3]]}
    t2 = {"id": "t2", "query": "SELECT 2", "columns": ["b"], "rows": [[4]]}
    messages = [
        HumanMessage("q"),
        ToolMessage("rows", tool_call_id="1", artifact={"tables": [t1]}),
        AIMessage("answer", additional_kwargs={"tables": [t1, t2]}),
        ToolMessage("no artifact", tool_call_id="2"),
    ]
    assert [t["id"] for t in artifacts_from(messages, "tables")] == ["t1", "t2"]
    assert collect(messages) == {"tables": [t1, t2]}
    note = table_note([t1, t2])
    assert note.splitlines()[0] == "- t1: 3 rows, columns a; query: SELECT 1; first rows: [1]; [2]"
