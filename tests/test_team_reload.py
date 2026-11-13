from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.api import routes_conversations
from app.orchestration.team import Board
from tests.conftest import requires_db
from tests.scripted import per_agent
from tests.test_team import CONFIG, QUESTION, Fake, _assistant, _board, _ask, _stream


async def test_reload_after_pause_reply_pause_shows_the_whole_conversation(monkeypatch):
    """A team chat that paused, was answered and paused again reloads as it was shown live:
    the user's message, question 1, the user's reply, then question 2 (pending)."""
    _board(monkeypatch, Board(question="Which year?"), Board(question="Which month?"), Board())
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                           sql=Fake("sql", "draft", log=[]), research=Fake("research", "notes", log=[]))
    await _stream(assistant, _ask())
    await _stream(assistant, Command(resume="2024"))
    assert (await assistant.aget_state(CONFIG)).interrupts  # paused on question 2

    monkeypatch.setattr(routes_conversations, "get_conversation", lambda user_id, cid: {"id": cid})
    monkeypatch.setattr(routes_conversations, "get_turn_activity", lambda user_id, cid: {})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=assistant)))
    shown = await routes_conversations.messages("t1", request, "u1")

    assert [(m["role"], m["content"], m.get("pending", False)) for m in shown] == [
        ("user", QUESTION, False),
        ("assistant", "Which year?", False),
        ("user", "2024", False),
        ("assistant", "Which month?", True),
    ]


def _request(assistant):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=assistant, store=None, checkpointer=None)))


async def _history(monkeypatch, assistant):
    monkeypatch.setattr(routes_conversations, "get_conversation", lambda user_id, cid: {"id": cid})
    monkeypatch.setattr(routes_conversations, "get_turn_activity", lambda user_id, cid: {})
    return await routes_conversations.messages("t1", _request(assistant), "u1")


def _shape(shown):
    return [(m["role"], m["content"], m.get("pending", False)) for m in shown]


async def test_reload_paused_once_shows_the_question_once_and_pending(monkeypatch):  # Review Focus 2
    _board(monkeypatch, Board(question="Which year?"), Board())
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                           sql=Fake("sql", "draft", log=[]), research=Fake("research", "notes", log=[]))
    await _stream(assistant, _ask())

    assert _shape(await _history(monkeypatch, assistant)) == [
        ("user", QUESTION, False), ("assistant", "Which year?", True)]


async def test_reload_after_the_turn_finished_shows_the_thread(monkeypatch):  # Review Focus 3
    _board(monkeypatch, Board(question="Which year?"), Board())
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                           sql=Fake("sql", "draft", log=[]), research=Fake("research", "notes", log=[]))
    await _stream(assistant, _ask())
    await _stream(assistant, Command(resume="2024"))

    assert _shape(await _history(monkeypatch, assistant)) == [
        ("user", QUESTION, False), ("assistant", "Which year?", False), ("user", "2024", False),
        ("assistant", "Final.", False)]


async def test_a_chat_without_an_interrupt_reloads_as_its_thread(monkeypatch):  # Review Focus 5
    _board(monkeypatch, Board())
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                           sql=Fake("sql", "draft", log=[]), research=Fake("research", "notes", log=[]))
    await _stream(assistant, _ask())

    assert _shape(await _history(monkeypatch, assistant)) == [("user", QUESTION, False), ("assistant", "Final.", False)]


async def test_each_run_saves_its_activity_under_the_message_that_started_it(monkeypatch):  # Review Focus 4
    from app.api import routes_chat
    from app.api.routes_chat import ChatRequest, chat
    from app.core.model_settings import ModelSettings

    _board(monkeypatch, Board(question="Which year?"), Board(question="Which month?"), Board())
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                           sql=Fake("sql", "draft", log=[]), research=Fake("research", "notes", log=[]))
    saved = []

    async def none(*args, **kwargs):
        return ""

    async def snapshot(*args):
        return {"user": "", "memories": "", "recent_chats": ""}

    monkeypatch.setattr(routes_chat, "get_conversation", lambda u, cid: {"id": cid, "title": "t"})
    monkeypatch.setattr(routes_chat, "touch_conversation", lambda cid: None)
    monkeypatch.setattr(routes_chat, "chat_snapshot", snapshot)
    monkeypatch.setattr(routes_chat, "load_settings",
                        lambda u: ModelSettings(base_url="https://x.test/v1", model="m", api_key="k"))
    monkeypatch.setattr(routes_chat, "add_usage", none)
    monkeypatch.setattr(routes_chat, "save_turn_activity", lambda user, cid, turn, events: saved.append(turn))

    for message in (QUESTION, "2024", "March"):
        response = await chat("team", ChatRequest(message=message, conversation_id="t1"), _request(assistant), "u1")
        async for _ in response.body_iterator:
            pass

    assert saved == [1, 2, 3]


@requires_db
def test_paused_subgraph_state_is_readable_from_the_real_checkpointer(monkeypatch):
    import asyncio
    import selectors

    # psycopg's async driver cannot run on pytest's Proactor loop (Windows): run on a selector loop
    asyncio.run(_paused_state_from_postgres(monkeypatch),
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


async def _paused_state_from_postgres(monkeypatch):
    import uuid
    from contextlib import AsyncExitStack

    from app.core.memory import open_persistence
    from tests import test_orchestrator

    thread = f"test-reload-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread}}
    async with AsyncExitStack() as stack:
        checkpointer, _ = await open_persistence(stack)
        monkeypatch.setattr(test_orchestrator, "InMemorySaver", lambda: checkpointer)
        _board(monkeypatch, Board(question="Which year?"), Board(question="Which month?"), Board())
        assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                               sql=Fake("sql", "draft", log=[]), research=Fake("research", "notes", log=[]))
        try:
            await _stream(assistant, _ask(), config)
            await _stream(assistant, Command(resume="2024"), config)
            messages, paused = await routes_conversations.thread_messages(assistant, config)
        finally:
            await checkpointer.adelete_thread(thread)  # only this test's thread

    assert paused and [(type(m), m.text) for m in messages] == [
        (HumanMessage, QUESTION), (AIMessage, "Which year?"), (HumanMessage, "2024"), (AIMessage, "Which month?")]
