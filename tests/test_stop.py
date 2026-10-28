import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.messages import HumanMessage

from app.api import routes_chat, routes_conversations
from app.api.routes_chat import ChatRequest, chat, stop
from app.orchestration.stopping import is_stopped


def snapshot(next=(), interrupts=(), values=None):
    return SimpleNamespace(next=next, interrupts=list(interrupts), values=values or {"messages": []})


def test_is_stopped():
    assert is_stopped(snapshot(next=("team",)))
    assert not is_stopped(snapshot(next=("team",), interrupts=[object()]))
    assert not is_stopped(snapshot(next=()))


class Harness:
    """The chat route with its storage faked: `astream` is the scripted run, `saved` records what the background task wrote."""

    def __init__(self, monkeypatch, astream):
        async def none(*args, **kwargs):
            return ""

        async def record(*args, **kwargs):
            self.saved.append(args)

        async def aget_state(config):
            return snapshot(values={"messages": []})

        self.saved = []
        self.owners = {"c1": "u1"}  # conversation id -> owner
        monkeypatch.setattr(routes_chat, "get_conversation",
                            lambda u, cid: {"id": cid, "title": "t"} if self.owners.get(cid) == u else None)
        monkeypatch.setattr(routes_chat, "create_conversation", lambda u, k, m: {"id": "c1", "title": "t"})
        monkeypatch.setattr(routes_chat, "touch_conversation", lambda cid: None)
        monkeypatch.setattr(routes_chat, "recall_facts", lambda u: "")
        monkeypatch.setattr(routes_chat, "load_settings", lambda u: object())
        monkeypatch.setattr(routes_chat, "_recent_chats", none)
        monkeypatch.setattr(routes_chat, "_user", none)
        monkeypatch.setattr(routes_chat, "add_usage", record)
        monkeypatch.setattr(routes_chat, "save_turn", record)
        monkeypatch.setattr(routes_chat, "extract_facts", record)
        monkeypatch.setattr(routes_chat, "_chat_input", lambda *a: none())
        monkeypatch.setattr(routes_chat, "RUNNING", {})
        assistant = SimpleNamespace(aget_state=aget_state, astream=astream)
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=assistant, store=None)))

    async def start(self, conversation_id=None):
        response = await chat("auto", ChatRequest(message="hi", conversation_id=conversation_id), self.request, "u1")
        events = []

        async def read():
            async for chunk in response.body_iterator:
                events.append(json.loads(chunk.removeprefix("data: ")))

        reader = asyncio.create_task(read())
        return response, events, reader


def slow_run(started: asyncio.Event):
    async def astream(*args, **kwargs):
        started.set()
        await asyncio.sleep(30)
        yield (), "custom", {"type": "plan"}

    return astream


async def test_stop_cancels_the_running_turn(monkeypatch):
    started = asyncio.Event()
    h = Harness(monkeypatch, slow_run(started))
    response, events, reader = await h.start()
    await asyncio.wait_for(started.wait(), 5)
    assert "c1" in routes_chat.RUNNING

    assert await stop("c1", "u1") == {"stopped": True}
    await asyncio.wait_for(reader, 5)

    assert events[-1] == {"type": "stopped"}
    assert all(e["type"] != "done" for e in events)
    assert "c1" not in routes_chat.RUNNING
    h.saved.clear()  # add_usage ran for the stream itself
    await response.background()
    assert h.saved == []  # nothing remembered: no history, no facts


async def test_stop_after_done_is_a_no_op(monkeypatch):
    async def astream(*args, **kwargs):
        yield (), "custom", {"type": "plan"}

    h = Harness(monkeypatch, astream)
    _, events, reader = await h.start()
    await asyncio.wait_for(reader, 5)
    assert events[-1] == {"type": "done"}
    assert await stop("c1", "u1") == {"stopped": False}


async def test_stop_is_scoped_by_user(monkeypatch):
    started = asyncio.Event()
    h = Harness(monkeypatch, slow_run(started))
    _, events, reader = await h.start()
    await asyncio.wait_for(started.wait(), 5)

    with pytest.raises(HTTPException) as err:
        await stop("c1", "u2")
    assert err.value.status_code == 404
    assert "c1" in routes_chat.RUNNING and not reader.done()

    await stop("c1", "u1")
    await asyncio.wait_for(reader, 5)


async def test_second_message_while_running_is_409(monkeypatch):
    started = asyncio.Event()
    h = Harness(monkeypatch, slow_run(started))
    _, events, reader = await h.start()
    await asyncio.wait_for(started.wait(), 5)

    with pytest.raises(HTTPException) as err:
        await chat("auto", ChatRequest(message="again", conversation_id="c1"), h.request, "u1")
    assert err.value.status_code == 409

    await stop("c1", "u1")
    await asyncio.wait_for(reader, 5)


async def test_messages_mark_a_stopped_chat(monkeypatch):
    monkeypatch.setattr(routes_conversations, "get_conversation", lambda user_id, cid: {"id": cid})
    state = snapshot(next=("team",), values={"messages": [HumanMessage("compare revenue")]})

    async def aget_state(config):
        return state

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=SimpleNamespace(aget_state=aget_state))))
    shown = await routes_conversations.messages("c1", request, "u1")
    assert shown[0] == {"role": "user", "content": "compare revenue"}
    assert shown[1] == {"role": "assistant", "stopped": True, "content": "", "team": None, "tool_calls": [], "charts": []}

    state.next = ()  # a finished chat shows no stopped line
    assert len(await routes_conversations.messages("c1", request, "u1")) == 1
