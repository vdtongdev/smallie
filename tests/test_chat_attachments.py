import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError
from sqlalchemy import delete, event

from app.api import routes_chat, routes_conversations
from app.api.routes_chat import ChatRequest, chat
from app.core.config import get_settings
from app.core.db import documents, get_engine, init_db, users
from app.core.memory import AgentContext
from app.core.model_settings import ModelSettings
from tests.conftest import requires_db
from tests.scripted import per_agent


@pytest.fixture
def env():
    init_db()
    created: list[str] = []

    def make_user() -> str:
        user_id = f"test-attach-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        created.append(user_id)
        return user_id

    def make_doc(user_id: str, filename: str = "a.png", status: str = "ready") -> str:
        doc_id = str(uuid.uuid4())
        with get_engine().begin() as conn:
            conn.execute(documents.insert().values(id=doc_id, user_id=user_id, filename=filename, path="x", status=status))
        return doc_id

    yield SimpleNamespace(user=make_user, doc=make_doc)
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id.in_(created)))


async def _run(monkeypatch, user, body, *, kind="auto", interrupts=(), answer="It is a cat."):
    """Run `chat` with a fake graph; returns (events, what the graph got, the saved-turn and extraction calls)."""
    seen = SimpleNamespace(input=None, context=None, saved=[], extracted=[])
    messages: list = []

    async def aget_state(config, **kwargs):
        return SimpleNamespace(values={"messages": list(messages)}, interrupts=list(interrupts))

    async def astream(first, **kwargs):
        seen.input, seen.context = first, kwargs["context"]
        messages.extend([first["messages"][0], AIMessage(answer)])
        return
        yield

    async def none(*args, **kwargs):
        return ""

    async def snapshot(*args):
        return {"user": "", "memories": "", "recent_chats": ""}

    async def save_turn(store, user_id, conversation, question, answer):
        seen.saved.append((question, answer))

    async def extract_turn(user_id, question, answer, *rest):
        seen.extracted.append((question, answer))

    monkeypatch.setattr(get_settings(), "cross_chat_history", True)
    monkeypatch.setattr(routes_chat, "chat_snapshot", snapshot)
    monkeypatch.setattr(routes_chat, "load_settings", lambda u: ModelSettings(base_url="https://x.test/v1", model="m", api_key="k"))
    monkeypatch.setattr(routes_chat, "add_usage", none)
    monkeypatch.setattr(routes_chat, "save_turn", save_turn)
    monkeypatch.setattr(routes_chat, "extract_turn", extract_turn)
    assistant = SimpleNamespace(aget_state=aget_state, astream=astream)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=assistant, store=None, checkpointer=None)))
    response = await chat(kind, body, request, user)
    out = [json.loads(c.removeprefix("data: ")) async for c in response.body_iterator]
    await response.background()
    return out, seen


# ---- validation ----

@requires_db
async def test_attachment_of_other_user_404(monkeypatch, env):
    owner, other = env.user(), env.user()
    doc = env.doc(owner)
    with pytest.raises(HTTPException) as err:
        await _run(monkeypatch, other, ChatRequest(message="hi", attachments=[doc]))
    assert err.value.status_code == 404


@requires_db
async def test_unknown_attachment_404(monkeypatch, env):
    with pytest.raises(HTTPException) as err:
        await _run(monkeypatch, env.user(), ChatRequest(message="hi", attachments=[str(uuid.uuid4())]))
    assert err.value.status_code == 404


@requires_db
async def test_attachment_not_ready_409(monkeypatch, env):
    user = env.user()
    doc = env.doc(user, status="processing")
    with pytest.raises(HTTPException) as err:
        await _run(monkeypatch, user, ChatRequest(message="hi", attachments=[doc]))
    assert err.value.status_code == 409


def test_more_than_five_refused():
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", attachments=[str(uuid.uuid4()) for _ in range(6)])


def test_empty_text_needs_attachments():
    with pytest.raises(ValidationError):
        ChatRequest(message="")
    with pytest.raises(ValidationError):
        ChatRequest(message="  ")
    assert ChatRequest(message="", attachments=["d1"]).attachments == ["d1"]


@requires_db
async def test_attachments_refused_while_team_question_pending(monkeypatch, env):
    from app.core.conversations import create_conversation

    user = env.user()
    doc = env.doc(user)
    conversation = create_conversation(user, "team", "hi")["id"]
    with pytest.raises(HTTPException) as err:
        await _run(monkeypatch, user, ChatRequest(message="2024", conversation_id=conversation, attachments=[doc]),
                   kind="team", interrupts=[object()])
    assert err.value.status_code == 400


# ---- what the graph, the title and the memory get ----

@requires_db
async def test_graph_gets_attached_line_and_context_ids(monkeypatch, env):
    user = env.user()
    first, second = env.doc(user, "a.png"), env.doc(user, "b.jpg")
    out, seen = await _run(monkeypatch, user, ChatRequest(message="what is this?", attachments=[first, second, first]))
    message = seen.input["messages"][0]
    assert message.content == "what is this?\nAttached: a.png, b.jpg"
    assert message.additional_kwargs["attachments"] == [{"id": first, "filename": "a.png"}, {"id": second, "filename": "b.jpg"}]
    assert seen.context.attached_documents == (first, second)


@requires_db
async def test_graph_text_is_only_the_line_without_text(monkeypatch, env):
    user = env.user()
    doc = env.doc(user, "a.png")
    out, seen = await _run(monkeypatch, user, ChatRequest(message="", attachments=[doc]))
    assert seen.input["messages"][0].content == "Attached: a.png"


@requires_db
async def test_title_without_attached_line(monkeypatch, env):
    user = env.user()
    doc = env.doc(user, "a.png")
    out, seen = await _run(monkeypatch, user, ChatRequest(message="what is this?", attachments=[doc]))
    assert out[0]["title"] == "what is this?"


@requires_db
async def test_title_from_filenames_when_no_text(monkeypatch, env):
    user = env.user()
    docs = [env.doc(user, "a.png"), env.doc(user, "b.jpg")]
    out, seen = await _run(monkeypatch, user, ChatRequest(message="", attachments=docs))
    assert out[0]["title"] == "a.png, b.jpg"


@requires_db
async def test_save_turn_gets_text_with_attached_line(monkeypatch, env):
    user = env.user()
    doc = env.doc(user, "a.png")
    out, seen = await _run(monkeypatch, user, ChatRequest(message="what is this?", attachments=[doc]))
    assert seen.saved == [("what is this?\nAttached: a.png", "It is a cat.")]
    assert seen.extracted == [("what is this?\nAttached: a.png", "It is a cat.")]


# ---- the messages API ----

def _history(messages):
    async def aget_state(config, **kwargs):
        return SimpleNamespace(values={"messages": messages}, interrupts=[])

    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=SimpleNamespace(aget_state=aget_state))))


def _no_activity(monkeypatch):
    monkeypatch.setattr(routes_conversations, "get_conversation", lambda user_id, cid: {"id": cid})
    monkeypatch.setattr(routes_conversations, "get_turn_activity", lambda user_id, cid: {})


def _attached(text, *files):
    return HumanMessage(content=text, additional_kwargs={"attachments": [{"id": i, "filename": f} for i, f in files]})


@requires_db
async def test_messages_api_strips_line_and_returns_attachments(monkeypatch, env):
    _no_activity(monkeypatch)
    user = env.user()
    doc = env.doc(user, "a.png")
    shown = await routes_conversations.messages("c1", _history([
        _attached("what is this?\nAttached: a.png", (doc, "a.png")), AIMessage("A cat."), HumanMessage("thanks")]), user)
    assert shown[0]["content"] == "what is this?"
    assert shown[0]["attachments"] == [{"id": doc, "filename": "a.png", "deleted": False}]
    assert shown[2]["attachments"] == []


@requires_db
async def test_only_the_line_message_has_empty_content(monkeypatch, env):
    _no_activity(monkeypatch)
    user = env.user()
    doc = env.doc(user, "a.png")
    shown = await routes_conversations.messages("c1", _history([_attached("Attached: a.png", (doc, "a.png"))]), user)
    assert shown[0]["content"] == ""


@requires_db
async def test_deleted_attachment_marked_deleted(monkeypatch, env):
    _no_activity(monkeypatch)
    user, other = env.user(), env.user()
    kept, gone, theirs = env.doc(user, "a.png"), str(uuid.uuid4()), env.doc(other, "c.png")
    thread = [_attached("one\nAttached: a.png, b.png", (kept, "a.png"), (gone, "b.png")), AIMessage("x"),
              _attached("two\nAttached: c.png", (theirs, "c.png"))]
    queries = []

    def spy(conn, cursor, statement, *args):
        if "documents" in statement:
            queries.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", spy)
    try:
        shown = await routes_conversations.messages("c1", _history(thread), user)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert [a["deleted"] for a in shown[0]["attachments"]] == [False, True]
    assert shown[2]["attachments"][0]["deleted"] is True  # another user's file is never reported as there
    assert len(queries) == 1


@requires_db
async def test_attachments_survive_the_checkpointer(monkeypatch, env):
    from tests.test_team import _assistant

    _no_activity(monkeypatch)
    user = env.user()
    doc = env.doc(user, "a.png")
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Hello.")]))
    config = {"configurable": {"thread_id": "t1"}}
    stream = assistant.astream({"messages": [_attached("hi\nAttached: a.png", (doc, "a.png"))]}, config,
                               context=AgentContext(user_id=user), stream_mode=["updates"], subgraphs=True)
    [_ async for _ in stream]
    thread = (await assistant.aget_state(config)).values["messages"]
    shown = await routes_conversations.messages("t1", _history(thread), user)
    assert shown[0]["content"] == "hi"
    assert shown[0]["attachments"] == [{"id": doc, "filename": "a.png", "deleted": False}]
