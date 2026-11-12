import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from sqlalchemy import delete, select

from app.api import routes_chat
from app.api.routes_chat import ChatRequest, _keep, chat
from app.api.routes_conversations import user_messages
from app.core.conversations import create_conversation, delete_conversation, get_turn_activity, save_turn_activity
from app.core.db import get_engine, init_db, turn_activity, users
from tests.conftest import requires_db


@pytest.fixture
def env():
    init_db()
    created: list[str] = []

    def make_user() -> str:
        user_id = f"test-activity-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        created.append(user_id)
        return user_id

    yield SimpleNamespace(user=make_user)
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id.in_(created)))


@requires_db
def test_saved_events_come_back_under_their_turn(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    save_turn_activity(user, chat, 1, [{"type": "agent", "name": "sql"}])
    assert get_turn_activity(user, chat) == {1: [{"type": "agent", "name": "sql"}]}


@requires_db
def test_rows_of_one_turn_are_joined_in_order(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    a, b, c = {"type": "a"}, {"type": "b"}, {"type": "c"}
    save_turn_activity(user, chat, 2, [a])
    save_turn_activity(user, chat, 2, [b])
    save_turn_activity(user, chat, 1, [c])
    assert get_turn_activity(user, chat) == {1: [c], 2: [a, b]}


@requires_db
def test_another_users_rows_are_never_returned(env):
    owner, other = env.user(), env.user()
    chat = create_conversation(owner, "auto", "hi")["id"]
    save_turn_activity(owner, chat, 1, [{"type": "a"}])
    assert get_turn_activity(other, chat) == {}
    assert get_turn_activity(owner, chat) == {1: [{"type": "a"}]}


@requires_db
def test_deleting_the_chat_removes_its_rows(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    save_turn_activity(user, chat, 1, [{"type": "a"}])
    delete_conversation(user, chat)
    with get_engine().begin() as conn:
        rows = conn.execute(select(turn_activity).where(turn_activity.c.conversation_id == chat)).all()
    assert rows == []


@requires_db
def test_a_chat_without_rows_reads_empty(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    assert get_turn_activity(user, chat) == {}


# ---- events() saves the reply's display events (no DB) ----

SQL = ("sql:1",)
CALL = AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"query": "SELECT 1"}, "id": "c1"}])


def _token(text):
    return ((), "messages", (AIMessageChunk(content=text), {"langgraph_node": "model"}))


def _updates(namespace, update):
    return (namespace, "updates", update)


def _result(content="rows"):
    return _updates(SQL, {"tools": {"messages": [ToolMessage(content=content, name="run_sql", tool_call_id="c1")]}})


AGENT = _updates((), {"router": {"route": "sql"}})
TOOL_CALL = _updates(SQL, {"model": {"messages": [CALL]}})


async def _chat(monkeypatch, chunks, *, kind="auto", settings="set", earlier=1, adds=True, interrupts=(),
                raises=None, save=None, stop_after=None):
    """Run `chat`; the thread holds `earlier` user messages and `astream` adds one when `adds`."""
    messages = [HumanMessage(content=f"q{i}") for i in range(earlier)]

    async def aget_state(config):
        return SimpleNamespace(values={"messages": list(messages)}, interrupts=list(interrupts))

    async def astream(*args, **kwargs):
        if adds:
            messages.append(HumanMessage(content="new"))
        for chunk in chunks:
            yield chunk
        if raises:
            raise raises

    async def none(*args, **kwargs):
        return ""

    async def snapshot(*args):
        return {"user": "", "memories": "", "recent_chats": ""}

    saved = []

    def record(*args):
        saved.append(args)
        if save:
            raise save

    from app.core.model_settings import ModelSettings

    monkeypatch.setattr(routes_chat, "create_conversation", lambda u, k, m: {"id": "c1", "title": "t"})
    monkeypatch.setattr(routes_chat, "touch_conversation", lambda cid: None)
    monkeypatch.setattr(routes_chat, "chat_snapshot", snapshot)
    monkeypatch.setattr(routes_chat, "load_settings",
                        lambda u: ModelSettings(base_url="https://x.test/v1", model="m", api_key="k") if settings else None)
    monkeypatch.setattr(routes_chat, "add_usage", none)
    monkeypatch.setattr(routes_chat, "_chat_input", lambda *a: none())
    monkeypatch.setattr(routes_chat, "save_turn_activity", record)
    assistant = SimpleNamespace(aget_state=aget_state, astream=astream)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=assistant, store=None, checkpointer=None)))
    response = await chat(kind, ChatRequest(message="hi"), request, "u1")
    out = []
    async for c in response.body_iterator:
        out.append(json.loads(c.removeprefix("data: ")))
        if stop_after and out[-1]["type"] == stop_after:
            await response.body_iterator.aclose()
            break
    return out, saved


async def test_a_reply_saves_its_events_under_its_user_message(monkeypatch):
    chunks = [AGENT, TOOL_CALL, _result(), _token("a"), _token("b"), _token("c")]
    out, saved = await _chat(monkeypatch, chunks)
    assert len(saved) == 1
    user, chat_id, turn, events = saved[0]
    assert (user, chat_id, turn) == ("u1", "c1", 2)
    assert [e["type"] for e in events] == ["routing", "agent", "tool_call", "tool_result", "token"]
    assert events[-1] == {"type": "token", "text": ""}


def test_token_runs_become_one_marker_each():
    shown: list[dict] = []
    for e in [{"type": "token", "text": "a"}, {"type": "token", "text": "b"},
              {"type": "token", "text": "c", "agent": "sql"}, {"type": "token", "text": "d", "agent": "sql"},
              {"type": "tool_call", "name": "x", "args": {}}, {"type": "token", "text": "e"}]:
        _keep(shown, e)
    assert shown == [{"type": "token", "text": ""}, {"type": "token", "text": "", "agent": "sql"},
                     {"type": "tool_call", "name": "x", "args": {}}, {"type": "token", "text": ""}]


async def test_a_long_tool_result_is_cut(monkeypatch):
    big = {"type": "tool_result", "name": "run_sql", "content": "x" * 5000}

    async def fake_events(stream):  # _to_events already cuts; feed an uncut event to see the saved copy is cut
        async for _ in stream:  # let the fake graph run, so its message is added
            pass
        yield big

    monkeypatch.setattr(routes_chat, "_events_from", fake_events)
    out, saved = await _chat(monkeypatch, [])
    streamed = next(e for e in out if e["type"] == "tool_result")
    kept = next(e for e in saved[0][3] if e["type"] == "tool_result")
    assert len(kept["content"]) == 4000
    assert len(streamed["content"]) == 5000


async def test_a_failed_reply_still_saves(monkeypatch):
    out, saved = await _chat(monkeypatch, [AGENT], raises=RuntimeError("boom"))
    events = saved[0][3]
    assert events[-1]["type"] == "error" and events[-1]["code"] == "internal"


async def test_a_cut_off_reply_still_saves(monkeypatch):
    out, saved = await _chat(monkeypatch, [AGENT, TOOL_CALL, _result()], stop_after="tool_call")
    assert len(saved) == 1
    assert [e["type"] for e in saved[0][3]] == ["routing", "agent", "tool_call"]


async def test_a_save_error_does_not_break_the_reply(monkeypatch):
    out, saved = await _chat(monkeypatch, [AGENT], save=RuntimeError("db down"))
    assert out[-1]["type"] == "done"


async def test_no_model_saves_nothing(monkeypatch):
    out, saved = await _chat(monkeypatch, [], settings=None)
    assert saved == []
    assert [e["type"] for e in out] == ["conversation", "error", "done"]


async def test_a_resumed_team_turn_saves_under_the_paused_message(monkeypatch):
    out, saved = await _chat(monkeypatch, [AGENT], kind="team", adds=False, interrupts=[object()])
    assert len(saved) == 1 and saved[0][2] == 1


async def test_a_run_that_adds_no_message_saves_nothing(monkeypatch):
    out, saved = await _chat(monkeypatch, [AGENT], adds=False)
    assert saved == []


def test_user_messages_counts_what_history_shows():
    messages = [HumanMessage(content="a"), AIMessage(content="b"), ToolMessage(content="c", name="t", tool_call_id="1"),
                HumanMessage(content="d")]
    assert user_messages(messages) == 2
