import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.messages import HumanMessage

from app.api import routes_chat, routes_conversations
from app.api.routes_chat import ChatRequest, chat, stop
from collections import Counter
from typing import Annotated, TypedDict
import operator

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from app.agents.base import STEER, user_request
from app.agents.remote import CallBudgetExceeded, RemoteAgent
from app.core.config import get_settings
from app.core.prompts import STEER_MESSAGE, reply_language
from app.core.usage import UsageCounter
from app.orchestration.stopping import add_note, is_resume, is_stopped, load_stop, save_stop


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
        self.store = InMemoryStore()
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
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(assistant=assistant, store=self.store)))

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


class CancelOnRegister(dict):
    """A Stop that lands the moment the run is registered, before the run's first step."""

    def __setitem__(self, key, task):
        super().__setitem__(key, task)
        task.cancel()


async def test_stop_before_the_run_took_a_step_still_ends_the_stream(monkeypatch):
    async def astream(*args, **kwargs):
        yield (), "custom", {"type": "plan"}

    h = Harness(monkeypatch, astream)
    monkeypatch.setattr(routes_chat, "RUNNING", CancelOnRegister())
    _, events, reader = await h.start()
    await asyncio.wait_for(reader, 5)  # a hang fails here instead of freezing the suite

    assert events[-1] == {"type": "stopped"}
    assert "c1" not in routes_chat.RUNNING


async def test_stop_when_the_run_already_finished_does_not_claim_the_turn(monkeypatch):
    h = Harness(monkeypatch, slow_run(asyncio.Event()))
    finished = asyncio.create_task(asyncio.sleep(0))
    await finished
    routes_chat.RUNNING["c1"] = finished  # finished, not yet removed by its stream

    assert await stop("c1", "u1") == {"stopped": False}


# --- Task 3: resume, notes, call budget ---


def test_is_resume():
    for message in ("continue", "Continue please", "go on", "tiếp tục", "ok làm tiếp", "tiếp đi nhé", "resume"):
        assert is_resume(message), message
    for message in ("continue the analysis with last year's data", "thanks", "stop"):
        assert not is_resume(message), message


class Chat(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    log: Annotated[list, operator.add]


async def stopped_chat(sub_name: str | None = None):
    """A graph cut off in its last node, `b` (inside a subgraph named `sub_name` when given).
    Returns (graph, config, calls)."""
    calls, started = Counter(), asyncio.Event()

    async def a(state):
        calls["a"] += 1
        return {"log": ["a"]}

    async def b(state):
        calls["b"] += 1
        calls["b_saw"] = [m.content for m in state["messages"]]
        started.set()
        await asyncio.sleep(0.5)
        return {"log": ["b"]}

    inner = StateGraph(Chat)
    inner.add_node("a", a)
    inner.add_node("b", b)
    inner.add_edge(START, "a")
    inner.add_edge("a", "b")
    inner.add_edge("b", END)
    if sub_name:
        outer = StateGraph(Chat)
        outer.add_node(sub_name, inner.compile(name=sub_name))
        outer.add_edge(START, sub_name)
        outer.add_edge(sub_name, END)
        graph = outer.compile(checkpointer=InMemorySaver())
    else:
        graph = inner.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}

    async def run():
        async for _ in graph.astream({"messages": [HumanMessage("request")]}, config, subgraphs=True):
            pass

    task = asyncio.create_task(run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert is_stopped(await graph.aget_state(config))
    return graph, config, calls


async def test_long_message_with_resume_word_is_a_note(monkeypatch):
    monkeypatch.setattr(routes_chat, "RUNNING", {})
    graph, config, _ = await stopped_chat()
    message = "continue the analysis with last year's data"

    assert await routes_chat._chat_input(graph, config, "auto", message) is None
    thread = (await graph.aget_state(config)).values["messages"]
    assert [(m.content, m.name) for m in thread[-2:]] == [(message, None), (STEER_MESSAGE, STEER)]
    assert is_stopped(await graph.aget_state(config))  # still resumable


async def test_plain_resume_adds_no_message(monkeypatch):
    monkeypatch.setattr(routes_chat, "RUNNING", {})
    graph, config, _ = await stopped_chat()
    before = (await graph.aget_state(config)).values["messages"]

    assert await routes_chat._chat_input(graph, config, "auto", "continue") is None
    assert (await graph.aget_state(config)).values["messages"] == before


async def test_resume_needs_no_registry_entry(monkeypatch):
    monkeypatch.setattr(routes_chat, "RUNNING", {})  # as after an app restart
    graph, config, calls = await stopped_chat()
    assert await routes_chat._chat_input(graph, config, "auto", "go on") is None
    async for _ in graph.astream(None, config):
        pass
    assert (await graph.aget_state(config)).next == ()
    assert calls["a"] == 1  # the finished step did not run again


async def test_paused_question_still_takes_the_reply():
    async def aget_state(config, **kwargs):
        return snapshot(next=("team",), interrupts=[object()])

    assistant = SimpleNamespace(aget_state=aget_state)
    result = await routes_chat._chat_input(assistant, {}, "team", "continue")
    assert isinstance(result, Command) and result.resume == "continue"


async def test_a_new_message_in_a_finished_chat_is_a_user_message():
    async def aget_state(config, **kwargs):
        return snapshot(next=())

    result = await routes_chat._chat_input(SimpleNamespace(aget_state=aget_state), {}, "auto", "continue")
    assert result == {"messages": [{"role": "user", "content": "continue"}]}


async def test_note_in_a_team_turn_keeps_the_earlier_steps():
    graph, config, calls = await stopped_chat(sub_name="team")  # a: finished, b: cut off, inside the team subgraph
    await add_note(graph, config, "N")
    async for _ in graph.astream(None, config, subgraphs=True):
        pass

    assert calls["a"] == 1  # not restarted from the first step
    assert calls["b"] == 2 and calls["b_saw"] == ["request", "N", STEER_MESSAGE]
    assert (await graph.aget_state(config)).next == ()


async def test_resumed_run_counts_earlier_calls(monkeypatch):
    budget = get_settings().max_message_model_calls
    h = Harness(monkeypatch, None)
    await save_stop(h.store, "u1", "c1", budget - 1)
    outcome = {}

    async def astream(*args, config, context, **kwargs):
        for counter in (c for c in config["callbacks"] if isinstance(c, UsageCounter)):
            counter.count(AIMessage("one more call"))
        agent = RemoteAgent(SimpleNamespace(route="sql"), "http://x", service_token="t", timeout_s=1, remote=object())
        try:
            await agent.ainvoke({"messages": []}, config, context=context)
        except CallBudgetExceeded:
            outcome["raised"] = True
        yield (), "custom", {"type": "plan"}

    h.request.app.state.assistant.astream = astream
    _, events, reader = await h.start(conversation_id="c1")
    await asyncio.wait_for(reader, 5)

    assert outcome == {"raised": True}
    fresh = [args[2] for args in h.saved if len(args) == 3 and isinstance(args[2], UsageCounter)]
    assert [c.model_calls for c in fresh] == [1]  # the user's totals count only this run's calls


async def test_finished_run_clears_stop_record(monkeypatch):
    async def astream(*args, **kwargs):
        yield (), "custom", {"type": "plan"}

    h = Harness(monkeypatch, astream)
    await save_stop(h.store, "u1", "c1", 4)
    await save_stop(h.store, "u1", "c2", 9)
    _, events, reader = await h.start(conversation_id="c1")
    await asyncio.wait_for(reader, 5)

    assert events[-1] == {"type": "done"}
    assert await load_stop(h.store, "u1", "c1") is None
    assert await load_stop(h.store, "u1", "c2") == {"model_calls": 9}  # another chat's record is untouched
    assert await load_stop(h.store, "u2", "c1") is None


async def test_a_run_in_another_chat_does_not_read_a_stop_record(monkeypatch):
    seen = {}

    async def astream(*args, config, **kwargs):
        seen["calls"] = [c.model_calls for c in config["callbacks"] if isinstance(c, UsageCounter)]
        yield (), "custom", {"type": "plan"}

    h = Harness(monkeypatch, astream)
    await save_stop(h.store, "u1", "c2", 50)
    _, _, reader = await h.start()  # a new chat: c1
    await asyncio.wait_for(reader, 5)
    assert seen["calls"] == [0, 0]


async def test_stopping_saves_the_calls_spent(monkeypatch):
    started = asyncio.Event()

    async def astream(*args, config, **kwargs):
        for c in config["callbacks"]:
            c.count(AIMessage("a call"))
        started.set()
        await asyncio.sleep(30)
        yield (), "custom", {"type": "plan"}

    h = Harness(monkeypatch, astream)
    await save_stop(h.store, "u1", "c1", 3)
    _, events, reader = await h.start(conversation_id="c1")
    await asyncio.wait_for(started.wait(), 5)
    await stop("c1", "u1")
    await asyncio.wait_for(reader, 5)

    assert events[-1] == {"type": "stopped"}
    assert await load_stop(h.store, "u1", "c1") == {"model_calls": 4}


def test_user_request_joins_notes():
    request, note = HumanMessage("R"), HumanMessage("N")
    steer = HumanMessage(STEER_MESSAGE, name=STEER)
    assert user_request([request]) == "R"
    assert user_request([request, note, steer]) == "R\n\nN"
    assert user_request([request, AIMessage("part"), note, steer, HumanMessage("N2"), steer]) == "R\n\nN\n\nN2"
    assert user_request([HumanMessage("old"), AIMessage("a"), request, HumanMessage("x", name="failed_attempt")]) == "R"


def test_a_note_sets_the_reply_language_not_the_steer_message():
    request = HumanMessage("compare the revenue of last year")
    steer = HumanMessage(STEER_MESSAGE, name=STEER)
    assert reply_language([request, HumanMessage("also include the margins"), steer]) == "English"
    assert reply_language([request, HumanMessage("thêm cả biên lợi nhuận nhé"), steer]) == "Vietnamese"
    assert reply_language([HumanMessage("so sánh doanh thu"), AIMessage("x"), request, steer]) == "English"
