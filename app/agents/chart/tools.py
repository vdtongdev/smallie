"""Chart tools. Each draws a chart for the user and returns a short summary for the model;
the spec itself (and any query result) travels as the ToolMessage artifact
(app/agents/artifacts.py), so the model never copies data points.

- `chart_sql`: runs a read-only SELECT (same guard as `run_sql`) and charts the result.
- `chart_table`: charts a table an earlier `run_sql`/`chart_sql` call already returned.
- `chart_data`: charts a few numbers given inline (from files, research or the chat).
"""

import asyncio

from langchain.tools import ToolRuntime, tool

from app.agents.artifacts import artifacts_from
from app.agents.chart.spec import ChartSpec, ChartType, Series, make_spec, summary
from app.agents.sql.tools import query_readonly
from app.core.memory import AgentContext


def to_number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def numeric_columns(table: dict, exclude: set[str]) -> list[str]:
    """Columns (other than `exclude`) whose non-empty values are all numbers."""
    columns = []
    for i, name in enumerate(table["columns"]):
        values = [row[i] for row in table["rows"] if row[i] is not None]
        if name not in exclude and values and all(to_number(v) is not None for v in values):
            columns.append(name)
    return columns


def rows_to_spec(table: dict, type: str, title: str, x_column: str, y_columns: list[str] | None = None,
                 series_column: str = "", x_label: str = "", y_label: str = "") -> ChartSpec | str:
    """A chart of a query result. With `series_column`, long rows (x, series, y) become one
    series per distinct value, e.g. one line per ticker."""
    columns, rows = table["columns"], table["rows"]
    wanted = [x_column, *(y_columns or []), *([series_column] if series_column else [])]
    if missing := [c for c in wanted if c not in columns]:
        return f"Error: unknown column(s) {', '.join(missing)}; the result has: {', '.join(columns)}."
    if not rows:
        return "Error: the query returned no rows, so there is nothing to chart."
    xi = columns.index(x_column)
    y_columns = y_columns or numeric_columns(table, {x_column, series_column})
    if not y_columns:
        return f"Error: no numeric column to plot; name y_columns (the result has: {', '.join(columns)})."

    if series_column:
        if len(y_columns) != 1:
            return "Error: with series_column, give exactly one y column (the value to plot)."
        si, yi = columns.index(series_column), columns.index(y_columns[0])
        labels = list(dict.fromkeys(row[xi] for row in rows))
        values: dict[str, dict] = {}
        for row in rows:
            values.setdefault(str(row[si]), {})[row[xi]] = to_number(row[yi])
        series = [{"name": name, "values": [by_x.get(x) for x in labels]} for name, by_x in values.items()]
        y_label = y_label or y_columns[0]
    else:
        labels = [row[xi] for row in rows]
        series = [{"name": name, "values": [to_number(row[columns.index(name)]) for row in rows]}
                  for name in y_columns]
    labels = ["" if x is None else x for x in labels]
    return make_spec(type=type, title=title, labels=labels, series=series,
                     x_label=x_label or x_column, y_label=y_label)


def drawn(spec: ChartSpec | str, table: dict | None = None) -> tuple[str, dict]:
    tables = {"tables": [table]} if table else {}
    if isinstance(spec, str):
        return spec, tables
    return summary(spec), {**tables, "charts": [spec.model_dump()]}


@tool(response_format="content_and_artifact", parse_docstring=True)
async def chart_sql(query: str, type: ChartType, title: str, x_column: str, y_columns: list[str] | None = None,
                    series_column: str = "", x_label: str = "", y_label: str = "") -> tuple[str, dict]:
    """Run a read-only SELECT on the stock database and draw its result as a chart for the user.

    Args:
        query: One SELECT (or WITH ... SELECT). Return one row per x value, or per x value and series (long format), with clear column names. No LIMIT for time series.
        type: line (values over time), bar (comparing categories), area (a total over time), pie (parts of one whole, at most 6 slices), scatter (two numeric columns, at most 3 series).
        title: The chart title, in the user's language.
        x_column: The column for the x axis (e.g. trade_date, ticker).
        y_columns: The numeric column(s) to plot; empty means every numeric column.
        series_column: Optional column that splits rows into one series per value (e.g. ticker for one line per stock); then give exactly one y column.
        x_label: Optional x axis label (defaults to x_column).
        y_label: Optional y axis label, with the unit (e.g. "Close (USD)").
    """
    table = await asyncio.to_thread(query_readonly, query)
    if isinstance(table, str):
        return table, {}
    spec = rows_to_spec(table, type, title, x_column, y_columns, series_column, x_label, y_label)
    return drawn(spec, table)


@tool(response_format="content_and_artifact", parse_docstring=True)
async def chart_table(table_id: str, type: ChartType, title: str, x_column: str, runtime: ToolRuntime[AgentContext],
                      y_columns: list[str] | None = None, series_column: str = "", x_label: str = "",
                      y_label: str = "") -> tuple[str, dict]:
    """Draw a chart for the user from a query result already in the chat (its table id, e.g. t1a2b3c), without querying again.

    Args:
        table_id: The id of a table returned earlier by run_sql or chart_sql.
        type: line (values over time), bar (comparing categories), area (a total over time), pie (parts of one whole, at most 6 slices), scatter (two numeric columns, at most 3 series).
        title: The chart title, in the user's language.
        x_column: The column for the x axis.
        y_columns: The numeric column(s) to plot; empty means every numeric column.
        series_column: Optional column that splits rows into one series per value; then give exactly one y column.
        x_label: Optional x axis label (defaults to x_column).
        y_label: Optional y axis label, with the unit.
    """
    tables = {t["id"]: t for t in artifacts_from(runtime.state["messages"], "tables")}
    table = tables.get(table_id.strip())
    if table is None:
        return f"Error: no table {table_id!r} in this chat. Available: {', '.join(tables) or 'none'}.", {}
    spec = rows_to_spec(table, type, title, x_column, y_columns, series_column, x_label, y_label)
    return drawn(spec)


@tool(response_format="content_and_artifact", parse_docstring=True)
def chart_data(type: ChartType, title: str, labels: list[str | float], series: list[Series],
               x_label: str = "", y_label: str = "") -> tuple[str, dict]:
    """Draw a chart for the user from a few numbers you already have (from files, research or the chat). For stock database data use chart_sql or chart_table instead.

    Args:
        type: line, bar, area, pie (one series, at most 6 slices) or scatter (numeric labels).
        title: The chart title, in the user's language.
        labels: The x values (categories, years or dates), in order.
        series: One or more series, each {"name": ..., "values": [one number per label]}.
        x_label: Optional x axis label.
        y_label: Optional y axis label, with the unit.
    """
    spec = make_spec(type=type, title=title, labels=labels, series=[dict(s) for s in series],
                     x_label=x_label, y_label=y_label)
    return drawn(spec)
