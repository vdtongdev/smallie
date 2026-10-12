import pytest

from app.sql.agent import describe_schema, execute_readonly
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
