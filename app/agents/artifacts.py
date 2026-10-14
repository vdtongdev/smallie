"""Data that travels with the messages without the model reading it: query results ("tables",
from `run_sql` and `chart_sql`) and charts ("charts", from the chart tools).

Tools return them as their ToolMessage artifact, e.g. {"tables": [...]}. An agent called as a
tool hands its own on in its result's artifact (app/agents/delegation.py), and the orchestrator
copies a turn's into its final AIMessage's `additional_kwargs`, because only that message enters
the thread. langchain_openai only sends known `additional_kwargs` keys, so they never reach a prompt.
"""

from collections.abc import Iterable

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

KINDS = ("tables", "charts")
NOTE_TABLES = 5  # most recent tables listed in a prompt
NOTE_ROWS = 2  # sample rows per listed table
NOTE_QUERY_CHARS = 300


def artifacts_from(messages: Iterable[AnyMessage], kind: str) -> list[dict]:
    """Every `kind` item in the messages, oldest first, once per id."""
    found: dict[str, dict] = {}
    for message in messages:
        if isinstance(message, ToolMessage) and isinstance(message.artifact, dict):
            items = message.artifact.get(kind, [])
        elif isinstance(message, AIMessage):
            items = message.additional_kwargs.get(kind, [])
        else:
            continue
        for item in items:
            found.setdefault(item["id"], item)
    return list(found.values())


def collect(messages: Iterable[AnyMessage]) -> dict[str, list[dict]]:
    """All artifacts in the messages, by kind, leaving out empty kinds."""
    messages = list(messages)
    return {kind: items for kind in KINDS if (items := artifacts_from(messages, kind))}


def table_note(tables: list[dict]) -> str:
    """The most recent tables, for a prompt: what each holds, so a chart can reuse it by id."""
    lines = []
    for table in tables[-NOTE_TABLES:]:
        sample = "; ".join(str(row) for row in table["rows"][:NOTE_ROWS])
        lines.append(
            f"- {table['id']}: {len(table['rows'])} rows, columns {', '.join(table['columns'])}; "
            f"query: {table['query'][:NOTE_QUERY_CHARS]}; first rows: {sample}"
        )
    return "\n".join(lines)
