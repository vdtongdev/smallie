"""Regression tests for the chat-memory report (spec: docs/superpowers/specs/2026-11-13-chat-memory-design.md).

The user's report: when the app shuts down mid question and the two earlier questions already
fetched data, asking to retry fetches the data again instead of using it. The same blindness
shows when a later team question comes after finished turns, and when the orchestrator asks an
agent in a 1v1 chat. Each test fails today and passes once every agent call in a chat receives
the data fetched earlier in that chat.

The tests watch only what a fake remote agent receives (its input messages and its context dict)
and what the plan call and the final answer's workspace record, so they do not depend on how the
chat data is stored or named.

Facts recorded from a crash (a team turn whose board call dies after the sql task returned,
on the real Postgres checkpointer, observed 2026-11-13 on this branch before any change):

- The crashed question IS in the parent thread: after the crash `aget_state(config)` holds
  [Q1, final answer 1, Q2] with `next == ("team",)`; no answer follows Q2.
- The crashed task's result (here the sql tool result) lives only in the team subgraph's
  checkpoint: `aget_state(config, subgraphs=True)` shows it under the `team` task's
  `workspace["results"]`. Nothing on the parent thread carries it.
- The next `ainvoke` with a new user message starts a fresh run from START, on the parent
  thread [Q1, answer 1, Q2, new message]. The subgraph's saved results are not reused: the
  workspace `goal` is the new message. For "try again" today `retry_tasks` re-runs the last
  finished turn's tasks (turn 1's, not the crashed turn 2's) with goal "try again", and the
  sql agent receives the thread's questions and final answers but neither earlier tool result.
So the crashed question is detectable as "the latest visible user message before the newest
one, with no final answer after it".
"""

import asyncio
import itertools
import json
import selectors
import uuid
from contextlib import AsyncExitStack
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.registry import get
from app.agents.remote import RemoteAgent
from app.core.db import get_engine, init_db, users
from app.core.memory import AgentContext
from app.orchestration import team
from app.orchestration.orchestrator import HIDDEN
from app.orchestration.team import Board
from app.orchestration.team_plan import Plan, Task
from tests import test_orchestrator, test_team
from tests.conftest import requires_db
from tests.scripted import per_agent
from tests.test_team import _assistant, _plan, _stream
from app.core.conversations import create_conversation
from sqlalchemy import delete


@pytest.fixture
def chat(monkeypatch):
    """Its own user with one chat row (chat data is tied to a chat row), removed afterwards by id."""
    init_db()
    user_id = f"test-chatmem-regression-{uuid.uuid4()}"
    with get_engine().begin() as conn:
        conn.execute(users.insert().values(id=user_id, display_name="Test"))
    monkeypatch.setattr(test_team, "TEAM_CONTEXT", replace(test_team.TEAM_CONTEXT, user_id=user_id))
    yield user_id, create_conversation(user_id, "auto", "hello")["id"]
    with get_engine().begin() as conn:  # cascades to the user's conversations and chat_data
        conn.execute(delete(users).where(users.c.id == user_id))

SQL, RESEARCH = get("sql"), get("research")
Q1 = "What is the AAPL market cap?"
Q2 = "What is the AAPL dividend yield?"
Q3 = "What is the AAPL share count?"
R1, R2, R3 = "RESULT-ONE-8841 cap 3T", "RESULT-TWO-5527 yield 0.4", "RESULT-THREE-1906 shares 15B"
ONE_PLAN = Plan(tasks=[Task(id="t1", agent="sql", goal="Fetch the figures.")])


class Crash(BaseException):
    """The app dying mid turn (not an Exception, so no step's error handling catches it)."""


class FakeRemote:
    """A remote agent server: records what each call receives, answers with a `run_sql` call whose
    result is the next scripted text (or, with `findings`, plain research notes)."""

    def __init__(self, *results, findings=None):
        self.results, self.findings, self.calls = list(results), findings, []

    async def ainvoke(self, input, config=None, *, context=None, headers=None, **kwargs):
        self.calls.append({"input": input, "context": context})
        if self.findings is not None:
            new = [AIMessage(self.findings)]
        else:
            call = f"call{len(self.calls)}"
            new = [AIMessage("", tool_calls=[{"name": "run_sql", "args": {"query": "select 1"}, "id": call}]),
                   ToolMessage(self.results.pop(0), tool_call_id=call, name="run_sql",
                               artifact={"tables": [{"id": f"tbl{len(self.calls)}", "columns": ["x"], "rows": [[1]],
                                                     "query": "select 1"}]}),
                   AIMessage("Done.")]
        return {"messages": [*input["messages"], *new], "findings": self.findings or ""}


def remote_agent(spec, remote) -> RemoteAgent:
    return RemoteAgent(spec, "http://agent.test", service_token="t", timeout_s=5, remote=remote)


def sent(call) -> str:
    """Everything one call put on the wire: its input messages and its context values."""
    texts = [m.text if hasattr(m, "text") else str(m) for m in call["input"]["messages"]]
    return "\n".join(texts) + "\n" + json.dumps(call["context"], default=str)


def question_of(messages) -> str:
    return next(m.text for m in reversed(messages) if isinstance(m, HumanMessage) and m.name not in HIDDEN)


def boards(monkeypatch, *items):
    """Scripted review boards in order; the string "crash" kills the app at that board."""
    queue = iter(items)

    async def fake(messages, answers, config, user_id=None, inputs=None, agents=None):
        item = next(queue)
        if item == "crash":
            raise Crash()
        return item

    monkeypatch.setattr(team, "ask_board", fake)


async def final_goal(assistant, config) -> str:
    final = (await assistant.aget_state(config)).values["messages"][-1]
    return final.additional_kwargs["team"]["goal"]


@requires_db
@requires_db
def test_team_retry_after_crash_reuses_fetched_data(monkeypatch, chat):
    # psycopg's async driver cannot run on pytest's Proactor loop (Windows): run on a selector loop
    asyncio.run(_retry_after_crash(monkeypatch, chat[1]),
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


async def _retry_after_crash(monkeypatch, thread):
    from app.core.memory import open_persistence

    config = {"configurable": {"thread_id": thread}}
    async with AsyncExitStack() as stack:
        checkpointer, _ = await open_persistence(stack)
        monkeypatch.setattr(test_orchestrator, "InMemorySaver", lambda: checkpointer)
        asked = _plan(monkeypatch, *itertools.repeat(ONE_PLAN, 5))
        boards(monkeypatch, Board(), "crash", Board())
        sql = FakeRemote(R1, R2, R3)
        assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                               documents=(), sql=remote_agent(SQL, sql))
        try:
            await _stream(assistant, {"messages": [HumanMessage(Q1)]}, config)  # finishes with R1
            try:
                await _stream(assistant, {"messages": [HumanMessage(Q2)]}, config)  # dies after R2
            except Crash:
                pass
            assert len(sql.calls) == 2, "setup: the crashed turn's sql task must have run"
            planned, before = len(asked), len(sql.calls)
            await _stream(assistant, {"messages": [HumanMessage("try again")]}, config)
            retried = sql.calls[before:]
            goal = await final_goal(assistant, config)
        finally:
            await checkpointer.adelete_thread(thread)  # only this test's thread

    problems = []
    if not retried:
        problems.append("no agent was called in the retry turn")
    for call in retried:
        for marker in (R1, R2):
            if marker not in sent(call):
                problems.append(f"the retry's sql call did not receive {marker!r}")
    plan_calls = asked[planned:]
    if not plan_calls:
        problems.append(f"no plan call asked the crashed question {Q2!r}")
    elif question_of(plan_calls[-1]) != Q2:
        problems.append(f"the plan call's question was {question_of(plan_calls[-1])!r}, not {Q2!r}")
    if goal != Q2:
        problems.append(f"workspace goal was {goal!r}, not {Q2!r}")
    assert not problems, "; ".join(problems)


@requires_db
async def test_later_team_question_sees_earlier_turns_data(monkeypatch, chat):
    config = {"configurable": {"thread_id": chat[1]}}
    _plan(monkeypatch, *itertools.repeat(ONE_PLAN, 5))
    boards(monkeypatch, Board(), Board(), Board())
    sql = FakeRemote(R1, R2, R3)
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                           documents=(), sql=remote_agent(SQL, sql))
    for question in (Q1, Q2, Q3):
        await _stream(assistant, {"messages": [HumanMessage(question)]}, config)

    assert len(sql.calls) == 3, "setup: each finished turn made one sql call"
    third = sent(sql.calls[2])
    assert R1 in third and R2 in third, (
        f"the third question's sql call did not receive both earlier results (R1: {R1 in third}, R2: {R2 in third})")


@requires_db
async def test_asked_agent_sees_chat_data(monkeypatch, chat):
    config = {"configurable": {"thread_id": chat[1]}}
    sql = FakeRemote(R1, "RESULT-ASKED-0042 rows")
    research = FakeRemote(findings="Sector notes.")
    ask_both = AIMessage("", tool_calls=[
        {"name": "ask_sql", "args": {"question": "Give the yield."}, "id": "a1"},
        {"name": "ask_research", "args": {"question": "Sector outlook?"}, "id": "a2"}])
    model = per_agent(orchestrator=[ask_both, AIMessage("Combined.")])
    assistant = _assistant(monkeypatch, model, documents=(),
                           sql=remote_agent(SQL, sql), research=remote_agent(RESEARCH, research))
    context = AgentContext(user_id=chat[0])
    for message in (Q1, "Now put that in context with research on the sector."):
        stream = assistant.astream({"messages": [HumanMessage(message)]}, config, context=context,
                                   stream_mode=["updates"], subgraphs=True)
        async for _ in stream:
            pass

    assert len(sql.calls) == 2, "setup: turn 1 answered by sql, turn 2 asked it once"
    asked_call = sent(sql.calls[1])
    assert R1 in asked_call, "the asked sql agent did not receive turn 1's result"
