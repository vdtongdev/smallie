"""Chat memory (docs/superpowers/specs/2026-11-13-chat-memory-design.md): what a chat's agents fetched,
saved per step and rendered as a note for later agent calls."""

import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import delete, func, select

from app.core.chat_memory import chat_tables, entries_from, render, save_entries
from app.core.config import get_settings
from app.core.conversations import create_conversation, delete_conversation
from app.core.db import chat_data, get_engine, init_db, users
from tests.conftest import requires_db


def table(table_id: str, rows: int = 3) -> dict:
    return {"id": table_id, "columns": ["a"], "rows": [[f"secret-row-{i}"] for i in range(rows)], "query": "SELECT a"}


def call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def result(name: str, content: str, call_id: str, artifact=None) -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=call_id, artifact=artifact)


@pytest.fixture
def chat():
    """Two users with one chat each, removed afterwards."""
    init_db()
    made = []

    def make():
        user_id = f"test-chatmem-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        made.append(user_id)
        return user_id, create_conversation(user_id, "auto", "hello")["id"]

    yield make
    with get_engine().begin() as conn:  # cascades to conversations and chat_data
        conn.execute(delete(users).where(users.c.id.in_(made)))


def count(user_id: str, conversation_id: str) -> int:
    with get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(chat_data).where(
            chat_data.c.user_id == user_id, chat_data.c.conversation_id == conversation_id)).scalar()


def entry(content: str, tool: str = "run_sql", args: dict | None = None, artifacts: dict | None = None) -> dict:
    return {"tool": tool, "args": args or {"query": "SELECT 1"}, "content": content, "artifacts": artifacts or {}}


def test_entries_pair_calls_with_results():
    messages = [
        HumanMessage("q"),
        AIMessage(content="", tool_calls=[
            {"name": "run_sql", "args": {"query": "SELECT 1"}, "id": "c1"},
            {"name": "search_memory", "args": {"query": "x"}, "id": "c2"},
            {"name": "run_sql", "args": {"query": "SELECT bad"}, "id": "c3"},
        ]),
        result("run_sql", "| a |\n| 1 |", "c1", {"tables": [table("t1")]}),
        result("search_memory", "saved", "c2"),
        result("run_sql", "Error: no such column", "c3"),
        AIMessage("done"),
    ]
    entries = entries_from(messages)
    assert len(entries) == 1
    assert entries[0]["tool"] == "run_sql"
    assert entries[0]["args"] == {"query": "SELECT 1"}
    assert entries[0]["content"] == "| a |\n| 1 |"
    assert entries[0]["artifacts"]["tables"][0]["id"] == "t1"


def test_entries_include_findings():
    entries = entries_from([HumanMessage("q")], findings="Source A says 5.")
    assert [(e["tool"], e["content"]) for e in entries] == [("findings", "Source A says 5.")]
    assert entries_from([HumanMessage("q")], findings="") == []


def test_entries_cut_content_to_the_entry_limit(monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_data_entry_chars", 50)
    messages = [call("run_sql", {"query": "q"}, "c1"), result("run_sql", "x" * 500, "c1")]
    assert len(entries_from(messages)[0]["content"]) <= 50


@requires_db
def test_render_is_scoped_to_user_and_chat(chat):
    user_a, chat_a1 = chat()
    _, chat_a2 = user_a, create_conversation(user_a, "auto", "second")["id"]
    user_b, chat_b = chat()
    save_entries(user_a, chat_a1, 1, "reply", "sql", [entry("MINE-A1")])
    save_entries(user_a, chat_a2, 1, "reply", "sql", [entry("OTHER-CHAT")])
    save_entries(user_b, chat_b, 1, "reply", "sql", [entry("OTHER-USER")])
    note = render(user_a, chat_a1)
    assert "MINE-A1" in note
    assert "OTHER-CHAT" not in note and "OTHER-USER" not in note
    assert render(user_a, "no-such-chat") == ""
    assert count(user_b, chat_b) == 1


@requires_db
def test_render_respects_budget_newest_first(chat, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_data_chars", 2000)
    monkeypatch.setattr(get_settings(), "chat_data_entry_chars", 100000)
    user, conversation = chat()
    save_entries(user, conversation, 1, "reply", "sql", [entry("OLDEST-BODY " + "o" * 50, args={"query": "OLDEST-ARGS"})])
    save_entries(user, conversation, 1, "reply", "sql", [entry("huge " + "h" * 10000)])
    for i in range(20):
        save_entries(user, conversation, 2, "reply", "sql", [entry(f"small-{i} " + "s" * 200)])
    save_entries(user, conversation, 3, "reply", "sql", [entry("NEWEST-BODY")])
    note = render(user, conversation)
    assert 0 < len(note) <= 2000
    assert "NEWEST-BODY" in note
    assert "OLDEST-ARGS" in note and "OLDEST-BODY" not in note  # header kept, body dropped


@requires_db
def test_render_shows_table_ids_and_row_counts_but_no_rows(chat):
    user, conversation = chat()
    save_entries(user, conversation, 1, "reply", "sql", [entry("| a |", artifacts={"tables": [table("tbl-77", rows=5)]})])
    note = render(user, conversation)
    assert "tbl-77" in note and "5 rows" in note
    assert "secret-row" not in note


@requires_db
def test_chat_tables_returns_saved_tables(chat):
    user, conversation = chat()
    other_user, other_chat = chat()
    save_entries(user, conversation, 1, "reply", "sql", [entry("a", artifacts={"tables": [table("t1")], "charts": [{"id": "c1", "table_id": "t1"}]})])
    save_entries(user, conversation, 2, "reply", "sql", [
        entry("b", artifacts={"tables": [table("t1"), table("t2")]})])  # t1 repeated: returned once
    save_entries(other_user, other_chat, 1, "reply", "sql", [entry("c", artifacts={"tables": [table("t9")]})])
    tables, charts = chat_tables(user, conversation)
    assert [t["id"] for t in tables] == ["t1", "t2"]
    assert len(tables[0]["rows"]) == 3  # full rows come back for the chart tools
    assert [c["id"] for c in charts] == ["c1"]
    assert chat_tables(user, "no-such-chat") == ([], [])


@requires_db
def test_deleting_chat_deletes_its_data(chat):
    user, conversation = chat()
    _, kept = user, create_conversation(user, "auto", "kept")["id"]
    other_user, other_chat = chat()
    save_entries(user, conversation, 1, "reply", "sql", [entry("a")])
    save_entries(user, kept, 1, "reply", "sql", [entry("b")])
    save_entries(other_user, other_chat, 1, "reply", "sql", [entry("c")])
    delete_conversation(user, conversation)
    assert count(user, conversation) == 0
    assert count(user, kept) == 1 and count(other_user, other_chat) == 1


@requires_db
def test_save_for_deleted_chat_is_dropped(chat):
    user, conversation = chat()
    delete_conversation(user, conversation)
    assert save_entries(user, conversation, 1, "reply", "sql", [entry("late")]) == 0
    assert count(user, conversation) == 0


@requires_db
def test_save_returns_rows_written(chat):
    user, conversation = chat()
    assert save_entries(user, conversation, None, "t2", "sql", [entry("a"), entry("b", tool="findings", args={})]) == 2
    assert save_entries(user, conversation, 1, "reply", "sql", []) == 0


@requires_db
def test_render_newest_entry_larger_than_the_budget_is_cut(chat, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_data_chars", 1000)
    monkeypatch.setattr(get_settings(), "chat_data_entry_chars", 100000)
    user, conversation = chat()
    save_entries(user, conversation, 1, "reply", "sql", [entry("older")])
    save_entries(user, conversation, 2, "reply", "sql", [entry("START-OF-HUGE " + "h" * 5000)])
    note = render(user, conversation)
    assert 0 < len(note) <= 1000
    assert "START-OF-HUGE" in note


@requires_db
@pytest.mark.parametrize("budget", [10, 30, 60, 80, 150])
def test_render_never_exceeds_a_tiny_budget(chat, monkeypatch, budget):
    monkeypatch.setattr(get_settings(), "chat_data_chars", budget)
    monkeypatch.setattr(get_settings(), "chat_data_entry_chars", 100000)
    user, conversation = chat()
    for i in range(5):
        save_entries(user, conversation, i + 1, "reply", "sql", [entry(f"body-{i} " + "b" * 100)])
    assert len(render(user, conversation)) <= budget
