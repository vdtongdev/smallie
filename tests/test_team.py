import asyncio
import json
import itertools
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langgraph.types import Command

from app.agents.base import RouteInfo
from app.agents.registry import AGENTS, get
from app.agents.research import agent as research_agent
from app.core.config import get_settings
from app.core.memory import AgentContext
from app.core.model_settings import ModelSettings
from app.core.prompts import reply_language
from app.core.usage import UsageCounter, add_usage, get_usage
from app.agents.remote import RemoteAgent
from langgraph.store.memory import InMemoryStore
from app.agents import factory
from app.orchestration import graph as graph_module
from app.orchestration import team
from app.orchestration.router import build_router
from app.orchestration.team import Board, Challenge, Conflict, Difference, Outcome, Review, Side, clean, participants
from app.orchestration.team_plan import Plan, Task
from tests.scripted import per_agent
from tests.test_orchestrator import _assistant as _build_assistant

SQL, RESEARCH = get("sql"), get("research")

QUESTION = "What is the AAPL market cap?"  # names no source: every available agent takes part (Goal 1)
SQL_QUESTION = "Using the SQL agent, what is the AAPL market cap?"  # names the sql source (Goal 1)
SQL_CHART = "Using the SQL agent, draw a chart of AAPL"
THREE = QUESTION  # names no source: with files uploaded, docs + sql + research all take part (Goal 1)
TEAM_CONTEXT = AgentContext(user_id="u1", agent="team")
CONFIG = {"configurable": {"thread_id": "t1"}}


def _assistant(monkeypatch, model, documents=(), **agents):
    """The orchestrator tests' assistant, but the user has no files unless a test says so: docs is
    available with permission only then (Goal 1), and an available agent takes part."""
    return _build_assistant(monkeypatch, model, documents=documents, **agents)


# --- who takes part ---

def _available(*, docs=True):
    infos = {"sql": RouteInfo(matches=lambda m: "AAPL" in m), "docs": RouteInfo(matches=lambda m: "handbook.pdf" in m)}
    return [(spec, infos.get(spec.route, RouteInfo())) for spec in AGENTS if docs or spec.route != "docs"]


def _routes(message, **kw):
    return [s.route for s in participants(_available(**kw), message)]


def test_pick_mentioned_with_others_and_overrides():
    # Goal 1: no source named (a ticker is a topic word) -> every available agent, in registry order.
    assert _routes(QUESTION) == ["docs", "sql", "research"]
    assert _routes(QUESTION, docs=False) == ["sql", "research"]
    assert _routes("chart AAPL") == ["docs", "sql", "research", "chart"]  # Goal 3: asked for a chart, so the drawer joins
    # Goal 1 "available with permission": a named file of an unavailable docs agent does not bring it in.
    assert "docs" not in _routes("what does handbook.pdf say", docs=False)


def test_chart_mention_brings_in_sql():
    assert {"sql", "chart"} <= set(_routes("chart AAPL"))
    assert {"sql", "chart"} <= set(_routes("plot a chart of prices"))


def test_message_naming_sql_and_chart_agents_runs_both():
    # A user who addresses the SQL agent by name must get it, even when the Chart agent is named too.
    message = "SQL Agent, pull the top 3 stocks by growth. Chart Agent, suggest layouts for them."
    assert "sql" in _routes(message)


def test_nobody_mentioned_picks_nobody():
    assert _routes("write a short poem") == []  # research alone does not join


def test_board_drops_fixers_and_routes_outside_the_conflict():
    board = Board(conflicts=[Conflict(routes=["sql", "docs", "ghost"], claim="c", fixable_by=["research", "sql", "ghost"], issue="i")])
    cleaned = team.clean(board, speakers=["sql", "docs", "research"])
    assert cleaned.conflicts[0].routes == ["sql", "docs"]
    assert cleaned.conflicts[0].fixable_by == ["sql"]


async def test_ask_board_calls_the_model_once(monkeypatch):
    calls, schemas = [], []

    async def ainvoke(messages, config):
        calls.append((messages, config))
        return Board()

    monkeypatch.setattr(team, "get_chat_model", lambda model=None: SimpleNamespace(
        with_structured_output=lambda schema, method: schemas.append((schema, method)) or SimpleNamespace(ainvoke=ainvoke)))
    config = {"callbacks": []}
    answers = [{"round": 1, "agent": "sql", "text": "SQL SAYS 3T"}, {"round": 1, "agent": "docs", "text": "DOCS SAYS 2T"}]
    await team.ask_board([HumanMessage("question?")], answers, config, user_id="u")
    assert len(calls) == 1 and calls[0][1] is config and schemas == [(Review, "function_calling")]
    human = calls[0][0][1].content
    assert "question?" in human and "SQL SAYS 3T" in human and "DOCS SAYS 2T" in human


async def test_ask_plan_uses_the_users_model_once(monkeypatch):
    from app.orchestration.team_plan import Plan
    calls, asked = [], []

    async def ainvoke(messages, config):
        calls.append((messages, config))
        return Plan()

    def fake_model(user_id):
        asked.append(user_id)
        return SimpleNamespace(with_structured_output=lambda schema, method: SimpleNamespace(ainvoke=ainvoke))

    monkeypatch.setattr(team, "get_chat_model", fake_model)
    available = [(SQL, RouteInfo())]
    await team.ask_plan([HumanMessage("question?")], available, {"callbacks": []}, user_id="u-7")
    assert asked == ["u-7"] and len(calls) == 1


NEWS = "Nvidia, Meta deepen AI ties with $3 billion investment deal (Reuters, October 27, 2026)"


async def _planner_input(monkeypatch, thread):
    seen = []

    async def ainvoke(messages, config):
        seen.append(messages)
        return Plan()

    monkeypatch.setattr(team, "get_chat_model", lambda user_id: SimpleNamespace(
        with_structured_output=lambda schema, method: SimpleNamespace(ainvoke=ainvoke)))
    await team.ask_plan(thread, [(get("sql"), RouteInfo()), (get("research"), RouteInfo())],
                        {"callbacks": []}, user_id="u")
    return seen[0][1].content


async def test_planner_sees_the_earlier_answer_the_message_refers_to(monkeypatch):
    """The user asks to analyse "the news" from the previous answer; the planner must be able to
    see that answer, or it plans a new news search."""
    thread = [HumanMessage("Find three recent, credible news reports about NVIDIA."),
              AIMessage(NEWS, name="orchestrator"),
              HumanMessage("pull the data of nvida from database and analyze if the news actually reflect")]
    human = await _planner_input(monkeypatch, thread)
    assert NEWS in human
    assert human.index(NEWS) < human.index("analyze if the news")


async def test_planner_cuts_each_earlier_message(monkeypatch):
    thread = [HumanMessage("first"), AIMessage("A" * (team.PLAN_MESSAGE_LIMIT + 500)), HumanMessage("second")]
    human = await _planner_input(monkeypatch, thread)
    assert "A" * team.PLAN_MESSAGE_LIMIT in human
    assert "A" * (team.PLAN_MESSAGE_LIMIT + 1) not in human


async def test_planner_drops_the_oldest_turns_past_the_total_cap(monkeypatch):
    big = team.PLAN_MESSAGE_LIMIT
    thread = [HumanMessage("OLDEST " + "x" * big)]
    for i in range(team.PLAN_HISTORY_LIMIT // big + 2):
        thread += [AIMessage(f"ANSWER{i} " + "y" * big), HumanMessage(f"ask{i}")]
    latest = "LATEST " + "z" * (big * 3)
    thread.append(HumanMessage(latest))
    human = await _planner_input(monkeypatch, thread)
    assert "OLDEST" not in human
    assert f"ANSWER{team.PLAN_HISTORY_LIMIT // big + 1} " in human
    assert latest in human


async def test_planner_leaves_out_hidden_notes_and_tool_messages(monkeypatch):
    from app.orchestration.orchestrator import HIDDEN
    hidden = next(iter(HIDDEN))
    thread = [HumanMessage("first"), HumanMessage("HIDDENNOTE", name=hidden),
              AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
              ToolMessage("TOOLRESULT", tool_call_id="1"), AIMessage("final answer"), HumanMessage("second")]
    human = await _planner_input(monkeypatch, thread)
    assert "HIDDENNOTE" not in human and "TOOLRESULT" not in human
    assert "final answer" in human


async def test_planner_input_for_a_new_chat_has_no_chat_section(monkeypatch):
    human = await _planner_input(monkeypatch, [HumanMessage("question?")])
    assert human.startswith("USER MESSAGE:\nquestion?\n\nAGENTS:")


def test_plan_prompt_formats():
    from app.orchestration.prompts import PLAN_PROMPT
    assert "6" in PLAN_PROMPT.format(language="English", max_tasks=6)


async def test_board_uses_the_users_model(monkeypatch):
    asked = []

    async def ainvoke(messages, config):
        return Board()

    def fake_model(user_id):
        asked.append(user_id)
        return SimpleNamespace(with_structured_output=lambda schema, method: SimpleNamespace(ainvoke=ainvoke))

    monkeypatch.setattr(team, "get_chat_model", fake_model)
    await team.ask_board([HumanMessage("question?")], [{"round": 1, "agent": "sql", "text": "x"}],
                         {"callbacks": []}, user_id="u-7")
    assert asked == ["u-7"]


# --- the team subgraph in the real assistant graph, with scripted models and a scripted board ---

class Fake(Runnable):
    """An agent that logs when it runs and answers with scripted texts (the last one repeats)."""

    def __init__(self, name, *texts, log, delay=0.0, tools=None, chart=None, table=None, meta=None):
        self.meta = meta
        self.name, self.texts, self.log, self.delay, self.chart = name, list(texts), log, delay, chart
        # A speaker answers from its source, so it calls its source tool (Task 6); the drawer has none.
        self.tools = name != "chart" if tools is None else tools
        self.table = table
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        raise NotImplementedError

    async def ainvoke(self, input, config=None, **kwargs):
        loop = asyncio.get_running_loop()
        self.log.append({"event": "start", "agent": self.name, "at": loop.time(), "last": input["messages"][-1],
                         "sources": kwargs["context"].sources, "deny": kwargs["context"].deny_tools, "tier": kwargs["context"].tier})
        await asyncio.sleep(self.delay)
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        out = [AIMessage(text, response_metadata=self.meta or {})]
        if self.tools:
            tool_name = "search_documents" if self.name == "docs" else "run_sql"
            out = [AIMessage("", tool_calls=[{"name": tool_name, "args": {"query": "SELECT 1"}, "id": "k1"}]),
                   ToolMessage("1 row", tool_call_id="k1", name=tool_name), *out]
        self.log.append({"event": "end", "agent": self.name, "at": loop.time()})
        if self.table:
            out = [ToolMessage("1 row", tool_call_id="k3", name="run_sql", artifact={"tables": [self.table]}), *out]
        if self.chart:
            out = [ToolMessage("drawn", tool_call_id="k2", name="chart_data", artifact={"charts": [{"id": self.chart}]}), *out]
        return {"messages": [*input["messages"], *out], "findings": text}


class _Failing(Runnable):
    def invoke(self, input, config=None, **kwargs):
        raise RuntimeError("boom")

    async def ainvoke(self, input, config=None, **kwargs):
        raise RuntimeError("boom")


def _board(monkeypatch, *boards, then=None):
    """Scripted board: the boards in order, then `then` forever."""
    queue = itertools.chain(boards, itertools.repeat(then) if then else ())

    async def fake(messages, answers, config, user_id=None, inputs=None, agents=None):
        return next(queue)

    monkeypatch.setattr(team, "ask_board", fake)


def _plan(monkeypatch, *plans):
    """Scripted plan call: the plans in order. Returns the list of messages it was asked with."""
    queue, asked = iter(plans), []

    async def fake(messages, available, config, user_id=None):
        asked.append(messages)
        return next(queue)

    monkeypatch.setattr(team, "ask_plan", fake)
    return asked


async def _stream(assistant, first, config=CONFIG):
    return [(mode, chunk) async for _, mode, chunk in assistant.astream(
        first, config, context=TEAM_CONTEXT, stream_mode=["messages", "updates", "custom"], subgraphs=True)]


def _ask(message=QUESTION):
    return {"messages": [HumanMessage(message)]}


def _events(chunks, kind):
    return [c for mode, c in chunks if mode == "custom" and c["type"] == kind]


async def _thread(assistant, config=CONFIG):
    return (await assistant.aget_state(config)).values["messages"]


def _agents(transcript):
    """The agents of the workspace's tasks, in task order (was `participants`)."""
    return [t["agent"] for t in transcript["tasks"]]


def _withdrawn(transcript):
    return [t["agent"] for t in transcript["tasks"] if t["status"] == "withdrew"]


def _spoke(transcript):
    """What each agent said, by agent (newest result)."""
    return {r["agent"]: r["text"] for r in transcript["results"].values()}


def _waves(transcript):
    return [e for e in transcript["history"] if e["type"] == "wave"]


def _reviews(transcript):
    return [e for e in transcript["history"] if e["type"] == "review"]


def _writeups(model):
    return [m.text for prompt in model.prompts for m in prompt if "TEAM RESULT" in m.text]


def _two(monkeypatch, log, *, sql="Cap is 3T.", research="Notes: 3T.", final="Final.", **kw):
    """sql and research take part; returns the orchestrator's model and the assistant."""
    model = per_agent(orchestrator=[AIMessage(final)])
    return model, _assistant(monkeypatch, model, sql=Fake("sql", sql, log=log, **kw),
                             research=Fake("research", research, log=log))


def _three(monkeypatch, log, **texts):
    model = per_agent(orchestrator=[AIMessage("Final.")])
    agents = {n: Fake(n, *texts.get(n, (f"{n} v1", f"{n} v2")), log=log) for n in ("sql", "docs", "research")}
    return model, _assistant(monkeypatch, model, documents=("handbook.pdf",), **agents), agents


def _research_does_not_join(monkeypatch):
    # Only used with a message that names the SQL agent: with no source named, research would take part too (Goal 1).
    monkeypatch.setattr(graph_module, "AGENTS", tuple(replace(s, joins="mentioned") for s in AGENTS))


async def test_router_sends_team_mode_to_team():
    router = build_router()
    state = {"messages": [HumanMessage(QUESTION)]}
    assert await router(state, SimpleNamespace(context=TEAM_CONTEXT)) == {"route": "team", "direct": False}


async def test_nobody_mentioned_orchestrator_answers_alone(monkeypatch):
    model = per_agent(orchestrator=[AIMessage("Roses are red.")])
    assistant = _assistant(monkeypatch, model)
    chunks = await _stream(assistant, _ask("write a short poem"))

    assert not _events(chunks, "wave") and not _events(chunks, "review")
    messages = await _thread(assistant)
    assert [(type(m), m.name, m.text) for m in messages][1:] == [(AIMessage, "orchestrator", "Roses are red.")]
    assert "team" not in messages[1].additional_kwargs and len(model.prompts) == 1


async def test_round_one_runs_participants_in_parallel(monkeypatch):
    log = []
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "a", log=log, delay=0.2),
                           research=Fake("research", "b", log=log, delay=0.2))
    await _stream(assistant, _ask())

    at = {(e["agent"], e["event"]): e["at"] for e in log}
    assert at[("sql", "start")] < at[("research", "end")] and at[("research", "start")] < at[("sql", "end")]


async def test_speakers_use_only_their_own_sources_and_announce_themselves(monkeypatch):
    log = []
    _board(monkeypatch, Board())
    _, assistant = _two(monkeypatch, log)
    chunks = await _stream(assistant, _ask())

    sources = {e["agent"]: e["sources"] for e in log if e["event"] == "start"}
    assert set(sources["sql"]) == set(SQL.sources) and set(sources["research"]) == set(RESEARCH.sources)
    speakers = _events(chunks, "speaker")
    assert sorted(e["agent"] for e in speakers) == ["research", "sql"]
    assert all(e["ns"] for e in speakers)
    # The hidden round message names the reply language.
    assert "English" in next(e["last"] for e in log if e["event"] == "start").text


async def test_single_participant_answer_stands(monkeypatch):
    _research_does_not_join(monkeypatch)
    log = []
    _board(monkeypatch, Board(agreements=["only one source"]))
    model = per_agent(sql=[AIMessage("unused")])  # no orchestrator script: a call to it fails the test
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap is 3T.", log=log, tools=True))
    chunks = await _stream(assistant, _ask(SQL_QUESTION))

    reply = (await _thread(assistant))[-1]
    assert (reply.name, reply.text) == ("sql_agent", "Cap is 3T.")
    assert _agents(reply.additional_kwargs["team"]) == ["sql"]
    assert not model.prompts and not _events(chunks, "synthesize")


async def test_agreement_writes_up_once(monkeypatch):
    log = []
    _board(monkeypatch, Board(agreements=["both say 3T"]))
    model, assistant = _two(monkeypatch, log)
    chunks = await _stream(assistant, _ask())

    assert [e["wave"] for e in _events(chunks, "wave")] == [1] and len(_events(chunks, "review")) == 1
    assert len(_events(chunks, "synthesize")) == 1
    writeups = _writeups(model)
    assert len(writeups) == 1 and "Cap is 3T." in writeups[0] and "Notes: 3T." in writeups[0]
    assert (await _thread(assistant))[-1].text == "Final."


def _conflict(fixable_by, routes=("sql", "docs")):
    return Board(conflicts=[Conflict(routes=list(routes), claim="CAP-CLAIM", fixable_by=fixable_by, issue="the figures differ")])


async def test_fixable_conflict_runs_a_conflict_round_for_its_routes_only(monkeypatch):
    log = []
    _board(monkeypatch, _conflict(["sql"]), Board())
    model, assistant, agents = _three(monkeypatch, log)
    chunks = await _stream(assistant, _ask(THREE))

    rounds = _events(chunks, "wave")
    assert [(e["wave"], e["kind"]) for e in rounds] == [(1, "plan"), (2, "conflict")]
    assert sorted(rounds[1]["agents"]) == ["docs", "sql"]
    assert [agents[n].calls for n in ("sql", "docs", "research")] == [2, 2, 1]
    second = [e["last"] for e in log if e["event"] == "start" and e["agent"] == "sql"][1]
    assert second.name == "team" and "CAP-CLAIM" in second.text
    assert len(_writeups(model)) == 1


async def test_unfixable_conflict_goes_to_write_up(monkeypatch):
    for fixers in (["research"], []):  # nobody inside the conflict can fix it / nobody at all
        _board(monkeypatch, _conflict(fixers))
        model, assistant, _ = _three(monkeypatch, [])
        chunks = await _stream(assistant, _ask(THREE))

        assert [e["wave"] for e in _events(chunks, "wave")] == [1]
        assert "CAP-CLAIM" in _writeups(model)[0]
        assert (await _thread(assistant))[-1].additional_kwargs["team"]["synthesis"]["forced"] is False


async def test_cap_forces_write_up_after_the_conflict_round_limit(monkeypatch):
    _board(monkeypatch, then=_conflict(["sql"]))
    model, assistant, _ = _three(monkeypatch, [])
    chunks = await _stream(assistant, _ask(THREE))

    limit = get_settings().team_max_rounds  # conflict rounds after round 1
    assert [e["wave"] for e in _events(chunks, "wave")] == list(range(1, limit + 2))
    assert _events(chunks, "review")[-1]["forced"] is True
    assert (await _thread(assistant))[-1].additional_kwargs["team"]["synthesis"]["forced"] is True
    assert len(_events(chunks, "synthesize")) == 1 and len(_writeups(model)) == 1


async def test_failed_speaker_forces_write_up(monkeypatch):
    log = []
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap 3T.", log=log), research=_Failing())
    await _stream(assistant, _ask())

    last = (await _thread(assistant))[-1]
    assert last.text == "Final." and last.additional_kwargs["team"]["synthesis"]["forced"] is True
    assert _spoke(last.additional_kwargs["team"])["research"] == "failed: RuntimeError"


async def test_failed_board_forces_write_up(monkeypatch):
    async def broken(messages, answers, config, user_id=None, inputs=None, agents=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(team, "ask_board", broken)
    model, assistant = _two(monkeypatch, [])
    chunks = await _stream(assistant, _ask())

    last = (await _thread(assistant))[-1]
    assert last.text == "Final." and last.additional_kwargs["team"]["synthesis"]["forced"] is True
    assert _events(chunks, "review")[-1]["forced"] is True
    assert "Cap is 3T." in _writeups(model)[0]


async def test_failed_board_rate_limit_still_propagates(monkeypatch):
    import httpx
    import openai

    async def limited(messages, answers, config, user_id=None, inputs=None, agents=None):
        raise openai.RateLimitError("slow", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)

    monkeypatch.setattr(team, "ask_board", limited)
    _, assistant = _two(monkeypatch, [])
    with pytest.raises(openai.RateLimitError):
        await _stream(assistant, _ask())


async def test_team_reply_carries_participants_artifacts(monkeypatch):
    # The single participant's reply.
    _research_does_not_join(monkeypatch)
    _board(monkeypatch, Board())
    assistant = _assistant(monkeypatch, per_agent(sql=[AIMessage("unused")]), sql=Fake("sql", "Cap.", log=[], chart="chart-sql"))
    await _stream(assistant, _ask(SQL_QUESTION))
    assert (await _thread(assistant))[-1].additional_kwargs["charts"] == [{"id": "chart-sql"}]


async def test_team_write_up_carries_participants_artifacts(monkeypatch):
    # The orchestrator's write-up with two participants, one of which drew a chart.
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], chart="chart-sql"),
                           research=Fake("research", "Notes.", log=[]))
    await _stream(assistant, _ask())
    assert (await _thread(assistant))[-1].additional_kwargs["charts"] == [{"id": "chart-sql"}]


async def test_drawer_does_not_speak_in_rounds(monkeypatch):
    log = []
    boards = []

    async def fake(messages, answers, config, user_id=None, inputs=None, agents=None):
        boards.append([a["agent"] for a in answers])
        return Board()

    monkeypatch.setattr(team, "ask_board", fake)
    model = per_agent(orchestrator=[AIMessage("Final.")])
    chart = Fake("chart", "drawn", log=log)
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log), research=Fake("research", "n", log=log),
                           chart=chart)
    chunks = await _stream(assistant, _ask("chart AAPL"))

    assert [e["agents"] for e in _events(chunks, "wave")] == [["sql", "research"]]
    assert boards == [["sql", "research"]] and chart.calls == 1  # the draw only
    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert [a for a in _agents(transcript) if a == "chart"] == ["chart"] and [a for a in _agents(transcript) if a != "chart"] == ["sql", "research"]


async def test_write_up_note_says_the_drawer_draws_after_the_answer(monkeypatch):
    log = []
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log), research=Fake("research", "n", log=log),
                           chart=Fake("chart", "drawn", log=log))
    await _stream(assistant, _ask("chart AAPL"))

    note = _writeups(model)[0]
    assert "chart" in note.replace("TEAM RESULT", "").lower()  # names the drawer and that its chart follows the answer


async def test_round_note_says_the_drawer_draws(monkeypatch):
    log = []
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log), research=Fake("research", "n", log=log),
                           chart=Fake("chart", "drawn", log=log))
    await _stream(assistant, _ask("chart AAPL"))
    starts = [e for e in log if e["event"] == "start" and e["agent"] in ("sql", "research")]
    assert starts and all("chart" in e["last"].text.lower() for e in starts)

    log.clear()
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log), research=Fake("research", "n", log=log))
    await _stream(assistant, _ask(), {"configurable": {"thread_id": "no-drawer"}})
    starts = [e for e in log if e["event"] == "start"]
    assert starts and all("chart" not in e["last"].text.lower() for e in starts)
    assert all("chart" not in note.lower() for note in _writeups(model))


async def test_rate_limit_still_propagates(monkeypatch):
    import httpx
    import openai

    class Limited(_Failing):
        async def ainvoke(self, input, config=None, **kwargs):
            request = httpx.Request("POST", "http://x")
            raise openai.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None)

    _board(monkeypatch, Board())
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                           sql=Fake("sql", "x", log=[]), research=Limited())
    with pytest.raises(openai.RateLimitError):
        await _stream(assistant, _ask())


async def _paused(monkeypatch):
    _board(monkeypatch, Board(question="Which year?"), Board())
    sql = Fake("sql", "draft", log=[])
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final 2024.")]),
                           sql=sql, research=Fake("research", "notes", log=[]))
    chunks = await _stream(assistant, _ask())
    return assistant, sql, chunks


async def test_needs_human_pauses_and_resume_continues(monkeypatch):
    assistant, sql, first = await _paused(monkeypatch)

    assert _events(first, "needs_human") == [{"type": "needs_human", "question": "Which year?"}]
    assert (await assistant.aget_state(CONFIG)).interrupts
    # The parent's thread only gets the subgraph's messages when it finishes; while paused they are
    # in the subgraph's own snapshot.
    paused = await assistant.aget_state(CONFIG, subgraphs=True)
    assert [m.text for m in paused.tasks[0].state.values["messages"]] == [QUESTION, "Which year?"]

    second = await _stream(assistant, Command(resume="2024"))
    assert not _events(second, "wave") and sql.calls == 1  # the participants do not run again
    assert not (await assistant.aget_state(CONFIG)).interrupts
    messages = await _thread(assistant)
    assert [(type(m), m.text) for m in messages] == [
        (HumanMessage, QUESTION), (AIMessage, "Which year?"), (HumanMessage, "2024"), (AIMessage, "Final 2024.")]


async def _chart_asks(monkeypatch, reply, **kw):
    """Chart (a drawer) and sql (its feed) take part; the board asks a question; the user's `reply` resumes the turn."""
    _research_does_not_join(monkeypatch)
    _board(monkeypatch, Board(question="Which data?"), Board())
    log = []
    agents = {"chart": Fake("chart", "drawn", log=log), "sql": Fake("sql", "rows", log=log),
              "docs": Fake("docs", "from file", log=log)}
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]),
                           **{"documents": ("handbook.pdf",), **agents, **kw})
    await _stream(assistant, _ask("Using the SQL agent, draw a chart of prices"))
    return assistant, agents, await _stream(assistant, Command(resume=reply))


async def test_reply_to_question_naming_a_new_agent_brings_it_in(monkeypatch):
    assistant, agents, second = await _chart_asks(monkeypatch, "check handbook.pdf")

    assert [(e["wave"], e["kind"], e["agents"]) for e in _events(second, "wave")] == [(2, "plan", ["docs"])]
    assert agents["docs"].calls == 1 and agents["sql"].calls == 1
    team_record = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert [a for a in _agents(team_record) if a != "chart"] == ["sql", "docs"] and [a for a in _agents(team_record) if a == "chart"] == ["chart"]


async def test_reply_task_has_origin_reply(monkeypatch):
    assistant, agents, second = await _chart_asks(monkeypatch, "check handbook.pdf")

    tasks = (await _thread(assistant))[-1].additional_kwargs["team"]["tasks"]
    docs = next(t for t in tasks if t["agent"] == "docs")
    assert docs["origin"] == "reply" and docs["depends_on"] == [] and docs["goal"] == ""


async def test_follow_up_and_reply_after_pause_keep_one_workspace(monkeypatch):
    assistant, agents, second = await _chart_asks(monkeypatch, "check handbook.pdf")

    team = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert sorted(_agents(team)) == ["chart", "docs", "sql"]
    assert {t["agent"] for t in team["tasks"] if t["status"] == "done"} >= {"sql", "docs"}
    assert {e["agent"] for e in team["history"] if e["type"] == "result"} >= {"sql", "docs"}


async def test_reply_without_new_agent_goes_straight_to_judge(monkeypatch):
    assistant, agents, second = await _chart_asks(monkeypatch, "2024 please")

    assert not _events(second, "wave") and agents["sql"].calls == 1 and agents["chart"].calls == 1  # the draw only
    assert all(e["agent"] != "chart" for e in (await _thread(assistant))[-1].additional_kwargs["team"]["history"] if e["type"] == "result")


async def test_reply_naming_an_agent_already_taking_part_does_not_run_it_again(monkeypatch):
    assistant, agents, second = await _chart_asks(monkeypatch, "make it a bar chart")

    assert not _events(second, "wave") and agents["chart"].calls == 1  # the draw only
    assert all(e["agent"] != "chart" for e in (await _thread(assistant))[-1].additional_kwargs["team"]["history"] if e["type"] == "result")
    assert [a for a in _agents((await _thread(assistant))[-1].additional_kwargs["team"]) if a == "chart"] == ["chart"]


async def test_reply_never_brings_in_an_unavailable_agent(monkeypatch):
    docs = Fake("docs", "from file", log=[])
    assistant, agents, second = await _chart_asks(monkeypatch, "check the uploaded file", documents=(), docs=docs)

    assert not _events(second, "wave") and docs.calls == 0


async def test_second_turn_starts_fresh(monkeypatch):
    log = []
    _board(monkeypatch, then=Board())
    model = per_agent(orchestrator=[AIMessage("F1"), AIMessage("F2")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "d1", "d2", log=log),
                           research=Fake("research", "n1", "n2", log=log))
    await _stream(assistant, _ask())
    await _stream(assistant, _ask("And the AAPL market cap in euro?"))

    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert len(_waves(transcript)) == 1 and len(_reviews(transcript)) == 1
    assert _spoke(transcript) == {"sql": "d2", "research": "n2"}


async def test_only_the_answer_joins_the_thread(monkeypatch):
    _board(monkeypatch, Board())
    _, assistant = _two(monkeypatch, [], sql="secret draft", research="secret notes")
    await _stream(assistant, _ask())

    messages = await _thread(assistant)
    assert [type(m) for m in messages] == [HumanMessage, AIMessage]
    assert not any("secret" in m.text for m in messages)


SIDES = [Side(route="sql", value="v1", source="db", as_of="2024-01-01"),
         Side(route="research", value="v2", source="a page", as_of="2024-02-01")]


def test_difference_without_source_or_date_becomes_conflict():
    for gap in ({"source": ""}, {"as_of": ""}):
        undated = SIDES[1].model_copy(update=gap)
        board = Board(differences=[Difference(routes=["sql", "research"], explanation="x", sides=[SIDES[0], undated])])
        cleaned = clean(board, ["sql", "research"])
        assert cleaned.differences == []
        (conflict,) = cleaned.conflicts
        assert conflict.routes == ["sql", "research"] and conflict.fixable_by == []
        assert "v1" in conflict.claim and "v2" in conflict.claim and conflict.issue


def test_dated_sourced_difference_stays_explained():
    board = Board(differences=[Difference(routes=["sql", "research"], explanation="x", sides=SIDES)])
    cleaned = clean(board, ["sql", "research"])
    assert len(cleaned.differences) == 1 and cleaned.conflicts == []


async def test_transcript_holds_rounds_tools_and_boards(monkeypatch):
    board = Board(agreements=["same figure"], differences=[Difference(routes=["sql", "research"], explanation="as-of date", sides=SIDES)])
    _board(monkeypatch, board)
    _, assistant = _two(monkeypatch, [], tools=True)
    await _stream(assistant, _ask())

    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert _agents(transcript) == ["sql", "research"] and transcript["synthesis"]["forced"] is False
    (first,) = _waves(transcript)
    assert (first["wave"], first["kind"]) == (1, "plan")
    sql = next(a for a in transcript["results"].values() if a["agent"] == "sql")
    assert sql["text"] == "Cap is 3T."
    assert sql["tools"] == [{"name": "run_sql", "args": {"query": "SELECT 1"}, "result": "1 row"}]
    assert _reviews(transcript)[0]["agreements"] == ["same figure"]
    assert _reviews(transcript)[0]["differences"][0]["explanation"] == "as-of date"


async def test_write_up_names_differences_and_the_message_language(monkeypatch):
    _board(monkeypatch, Board(differences=[Difference(routes=["sql", "research"], explanation="AS-OF-DATE", sides=SIDES)]))
    model, assistant = _two(monkeypatch, [])
    await _stream(assistant, _ask("Vốn hóa của AAPL là bao nhiêu?"))

    text = _writeups(model)[0]
    assert "AS-OF-DATE" in text and "Vietnamese" in text


async def test_chat_input_resumes_a_paused_team_turn(monkeypatch):
    from app.api.routes_chat import _chat_input

    assistant, _, _ = await _paused(monkeypatch)
    assert await _chat_input(assistant, CONFIG, "team", "2024") == Command(resume="2024")
    # Only team mode resumes; another kind sends a message.
    assert await _chat_input(assistant, CONFIG, "auto", "2024") == {"messages": [{"role": "user", "content": "2024"}]}


async def test_chat_input_is_a_message_otherwise(monkeypatch):
    from app.api.routes_chat import _chat_input

    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Hi.")]))
    assert await _chat_input(assistant, CONFIG, "team", "hello") == {"messages": [{"role": "user", "content": "hello"}]}


def test_prompts_format():
    from app.orchestration import prompts

    prompts.BOARD_PROMPT.format(language="English")
    prompts.ROUND_MESSAGE.format(language="English", drawing="")
    prompts.CONFLICT_MESSAGE.format(conflicts="", others="", own="", language="English")
    prompts.TEAM_MESSAGE.format(answers="", differences="none", conflicts="none", open_points="none", not_done="none", language="English", drawing="")


def test_round_message_formats_with_language():
    from app.orchestration.prompts import ROUND_MESSAGE

    text = ROUND_MESSAGE.format(language="English", drawing="")
    assert "English" in text
    assert "{" not in text and "}" not in text


async def test_transcript_holds_research_tool_calls(monkeypatch):
    """Research's researcher runs inside its workflow: its tool calls are not in the workflow's messages."""
    @tool
    def tavily_tavily_search(query: str) -> str:
        """Web search."""
        return "Apple cap 3T"

    monkeypatch.setattr(research_agent, "_mcp_tools", [tavily_tavily_search])
    _board(monkeypatch, Board())
    model = per_agent(
        research=[AIMessage("", tool_calls=[{"name": "tavily_tavily_search", "args": {"query": "AAPL cap"}, "id": "w1"}]),
                  AIMessage("FINDINGS:\n- 3T\nGAPS: none")],
        orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap is 3T.", log=[]))
    await _stream(assistant, _ask())

    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    research = next(a for a in transcript["results"].values() if a["agent"] == "research")
    assert research["tools"] == [{"name": "tavily_tavily_search", "args": {"query": "AAPL cap"}, "result": "Apple cap 3T"}]


async def test_spoke_event_carries_the_final_entry(monkeypatch):
    _board(monkeypatch, Board())
    _, assistant = _two(monkeypatch, [], tools=True)
    chunks = await _stream(assistant, _ask())

    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    spoke = _events(chunks, "spoke")
    assert {e["agent"] for e in spoke} == {"sql", "research"}
    for event in spoke:
        task = next(t for t in transcript["tasks"] if t["agent"] == event["agent"])
        entry = {**transcript["results"][task["id"]], "withdrew": task["status"] == "withdrew"}
        assert event == {"type": "spoke", "task": task["id"], **entry}
    assert next(e for e in spoke if e["agent"] == "sql")["tools"] == [
        {"name": "run_sql", "args": {"query": "SELECT 1"}, "result": "1 row"}]


# --- the draw step: the chart agent draws from the final answer ---

TABLE = {"id": "t1", "query": "SELECT ticker, cap FROM x", "columns": ["ticker", "cap"],
         "rows": [["AAPL", 3.0], ["MSFT", 2.5]], "truncated": False}
DRAW = [AIMessage("", tool_calls=[{"name": "chart_table", "args": {
            "table_id": "t1", "type": "bar", "title": "Caps", "x_column": "ticker", "y_columns": ["cap"]}, "id": "d1"}]),
        AIMessage("Chart drawn.")]


async def test_drawer_charts_the_final_answers_table(monkeypatch):
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")], chart=DRAW)
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], table=TABLE),
                           research=Fake("research", "Notes.", log=[]))
    chunks = await _stream(assistant, _ask("chart AAPL"))

    final = (await _thread(assistant))[-1]
    charts = final.additional_kwargs["charts"]
    assert len(charts) == 1 and charts[0]["table_id"] == "t1" and final.text == "Final."
    assert [e["charts"] for e in _events(chunks, "charts")] == [charts]
    assert "chart_sql" not in {t["function"]["name"] for t in model.tools[-1]}  # no data sources: the drawer cannot query
    drawn = [r for r in final.additional_kwargs["team"]["results"].values() if r["agent"] == "chart"]
    assert len(drawn) == 1 and drawn[0]["tools"][0]["name"] == "chart_table"
    assert {e["agent"] for e in _events(chunks, "speaker")} >= {"chart"}


async def test_drawer_charts_the_table_gathered_before_a_pause(monkeypatch):
    """Regression test for the bug: a table the sql agent returned before the board asked the user a question must
    still reach the chart agent after the user answers. Currently failing."""
    _board(monkeypatch, Board(question="Which year?"), Board())
    model = per_agent(orchestrator=[AIMessage("Final.")], chart=DRAW)
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], table=TABLE),
                           research=Fake("research", "Notes.", log=[]))
    await _stream(assistant, _ask("chart AAPL"))
    assert (await assistant.aget_state(CONFIG)).interrupts
    chunks = await _stream(assistant, Command(resume="2024 please"))

    final = (await _thread(assistant))[-1]
    assert final.additional_kwargs.get("tables") == [TABLE]
    charts = final.additional_kwargs.get("charts")
    assert charts and charts[0]["table_id"] == "t1" and [e["charts"] for e in _events(chunks, "charts")] == [charts]


async def test_drawer_charts_the_table_when_the_task_runs_again_after_a_pause(monkeypatch):
    """Regression test for the bug, currently failing: the board asks the user a question, the user answers, the
    next review sends sql back for a conflict round, and that second run queries nothing new. The table it
    gathered before the question must still reach the final answer and the chart agent."""
    class TableOnce(Fake):
        async def ainvoke(self, input, config=None, **kwargs):
            out = await super().ainvoke(input, config, **kwargs)
            self.table = None
            return out

    _board(monkeypatch, Board(question="Which year?"), _conflict(["sql"], routes=("sql", "research")), Board())
    model = per_agent(orchestrator=[AIMessage("Final.")], chart=DRAW)
    sql = TableOnce("sql", "Cap.", log=[], table=TABLE)
    assistant = _assistant(monkeypatch, model, sql=sql, research=Fake("research", "Notes.", log=[]))
    await _stream(assistant, _ask("chart AAPL"))
    await _stream(assistant, Command(resume="2024 please"))

    assert sql.calls == 2  # the conflict round ran sql again
    final = (await _thread(assistant))[-1]
    assert final.additional_kwargs.get("tables") == [TABLE]
    assert final.additional_kwargs.get("charts")


async def test_drawer_failure_keeps_the_answer(monkeypatch):
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], table=TABLE),
                           research=Fake("research", "Notes.", log=[]), chart=_Failing())
    chunks = await _stream(assistant, _ask("chart AAPL"))

    final = (await _thread(assistant))[-1]
    assert final.text == "Final." and "charts" not in final.additional_kwargs and not _events(chunks, "charts")


async def test_no_drawer_no_draw(monkeypatch):
    _board(monkeypatch, Board())
    chart = Fake("chart", "drawn", log=[])
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[]), research=Fake("research", "n", log=[]),
                           chart=chart)
    chunks = await _stream(assistant, _ask())

    assert chart.calls == 0 and not _events(chunks, "charts")
    assert "drawn" not in (await _thread(assistant))[-1].additional_kwargs["team"]


async def test_charts_event_for_a_write_up_that_already_has_charts(monkeypatch):
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], chart="chart-sql"),
                           research=Fake("research", "Notes.", log=[]))
    chunks = await _stream(assistant, _ask())
    assert [e["charts"] for e in _events(chunks, "charts")] == [[{"id": "chart-sql"}]]


async def test_single_speaker_answer_stands_then_chart_draws(monkeypatch):
    _research_does_not_join(monkeypatch)
    _board(monkeypatch, Board())
    model = per_agent(chart=DRAW)  # no orchestrator script: a call to it fails the test
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap is 3T.", log=[], table=TABLE))
    chunks = await _stream(assistant, _ask(SQL_CHART))

    final = (await _thread(assistant))[-1]
    assert (final.name, final.text) == ("sql_agent", "Cap is 3T.")
    assert final.additional_kwargs["charts"][0]["table_id"] == "t1" and _events(chunks, "charts")
    assert not _events(chunks, "synthesize")


async def test_answer_without_data_draws_nothing(monkeypatch):
    _research_does_not_join(monkeypatch)
    _board(monkeypatch, Board())
    model = per_agent(chart=[AIMessage("The answer holds no data to chart.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "No rows.", log=[]))
    chunks = await _stream(assistant, _ask(SQL_CHART))

    final = (await _thread(assistant))[-1]
    assert final.text == "No rows." and "charts" not in final.additional_kwargs and not _events(chunks, "charts")


def test_draw_message_formats_with_language():
    from app.orchestration.prompts import DRAW_MESSAGE

    text = DRAW_MESSAGE.format(language="English")
    assert "English" in text and "{" not in text and "}" not in text


# --- open table (Goal 1: no source named -> every available agent takes part) ---

GAVE_UP = "I couldn't find anything about that."  # a short "nothing found" reply: the agent withdraws (Goal 2)


def test_spec_names_is_a_pattern_or_none():
    """Interfaces: AgentSpec.names is a compiled pattern (or None)."""
    import re

    assert all(spec.names is None or isinstance(spec.names, re.Pattern) for spec in AGENTS)
    assert all(get(route).names is not None for route in ("sql", "docs", "research", "chart"))


def test_names_match_the_explicit_phrases():
    """Goal 1 'specifying a source': naming an agent or its source, English and Vietnamese."""
    for route, phrases in {"sql": ["SQL agent", "the database"], "docs": ["docs agent", "my documents"],
                           "research": ["research agent", "search the web"],
                           "chart": ["chart agent", "draw a chart", "biểu đồ"]}.items():
        for phrase in phrases:
            assert get(route).names.search(f"please use {phrase} now"), (route, phrase)


def test_topic_words_and_tickers_do_not_name_a_source():
    """Goal 1: topic words such as 'stocks', or a ticker, do not count as specifying a source."""
    for route in ("sql", "docs", "research", "chart"):
        for message in ("evaluate top 3 stocks", "What is the AAPL market cap?"):
            assert not get(route).names.search(message), (route, message)


def test_no_source_named_brings_in_every_available_speaker():
    """Goal 1: an ambiguous question without a source -> all available agents; docs only with files."""
    for message in ("find in different sources that you can use", "evaluate top 3 stocks", QUESTION):
        assert _routes(message) == ["docs", "sql", "research"], message
        assert _routes(message, docs=False) == ["sql", "research"], message


def test_naming_a_source_restricts_to_it():
    """Goal 1: naming an agent or its source takes part alone (research may join, as today); others stay out."""
    for message in ("Ask the SQL agent about it", "look in the database", "Ask the SQL agent: what is AAPL?"):
        routes = _routes(message)
        assert "sql" in routes and "docs" not in routes, message
    for message in ("ask the docs agent about it", "search my documents", "what does handbook.pdf say"):
        routes = _routes(message)
        assert "docs" in routes and "sql" not in routes, message
    for message in ("ask the research agent about it", "search the web for AAPL news"):
        routes = _routes(message)
        assert "research" in routes and "sql" not in routes and "docs" not in routes, message


def test_docs_mentions_such_as_resume_words_name_the_docs_source():
    """Goal 1 + Interfaces: docs is named through RouteInfo.mentions (resume/CV words), not only file names."""
    available = [(spec, RouteInfo(mentions=lambda m: "resume" in m) if spec.route == "docs" else RouteInfo())
                 for spec in AGENTS]
    routes = [s.route for s in participants(available, "summarise my resume for me please")]
    assert "docs" in routes and "sql" not in routes


def test_named_source_of_an_unavailable_agent_does_not_bring_it_in():
    """Goal 1 'available with permission': docs without files never joins."""
    assert "docs" not in _routes("ask the docs agent about it", docs=False)
    assert "docs" not in _routes("search my documents", docs=False)


def test_the_chart_drawer_takes_part_only_when_asked():
    """Goal 3: chart joins only when the message asks for a chart; then sql feeds it."""
    for message in (QUESTION, "find in different sources that you can use", "evaluate top 3 stocks"):
        assert "chart" not in _routes(message), message
    for message in ("draw a chart of the top stocks", "ask the chart agent for a view", "vẽ biểu đồ giá AAPL"):
        assert {"sql", "chart"} <= set(_routes(message)), message


def test_users_message_naming_sql_and_chart_agents():
    """Goal 1 + 3: the user's own message names the SQL agent and the Chart agent."""
    message = ("SQL Agent, please pull the top 3 stocks by growth and their key metrics. "
               "Chart Agent, draw a chart that compares them.")
    routes = _routes(message)
    assert "sql" in routes and "chart" in routes and "docs" not in routes
    assert routes.index("sql") < routes.index("chart")  # registry order


def test_users_message_find_in_different_sources():
    """Goal 1: the user's own message names no source, so every available agent takes part, docs included."""
    assert _routes("find in different sources that you can use") == ["docs", "sql", "research"]


def test_small_talk_is_rule_based():
    """Goal 1 'small talk': a short message with no question and no data words goes to the orchestrator."""
    for message in ("hi", "thanks", "ok", "ok cảm ơn", "cảm ơn bạn", "xin chào", "write a short poem"):
        assert team.is_small_talk(message), message
        assert _routes(message) == [], message


def test_not_small_talk():
    """Interfaces: more than 5 words, a '?', or a name/hint/route match is not small talk."""
    for message in ("find in different sources that you can use", "ok?", "What is the AAPL market cap?",
                    "SQL agent", "draw a chart", "ask the docs agent", "evaluate top 3 stocks"):
        assert not team.is_small_talk(message), message
    assert _routes("evaluate top 3 stocks") != []


async def test_small_talk_goes_to_the_orchestrator_alone(monkeypatch):
    """Goal 1 'small talk': orchestrator alone, no rounds, one model call."""
    model = per_agent(orchestrator=[AIMessage("You are welcome.")])
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",))
    chunks = await _stream(assistant, _ask("ok cảm ơn"))

    assert not _events(chunks, "wave") and not _events(chunks, "review") and len(model.prompts) == 1
    assert (await _thread(assistant))[-1].text == "You are welcome."


async def test_open_table_runs_every_available_agent_in_one_round(monkeypatch):
    """Goal 1 end to end: no source named -> docs, sql and research all speak in round 1."""
    log = []
    _board(monkeypatch, Board())
    model, assistant, agents = _three(monkeypatch, log)
    chunks = await _stream(assistant, _ask("find in different sources that you can use"))

    assert [(e["wave"], sorted(e["agents"])) for e in _events(chunks, "wave")] == [(1, ["docs", "research", "sql"])]
    assert all(a.calls == 1 for a in agents.values())


# --- withdrawal (Goal 2) ---

def _boards_log(monkeypatch, *boards, then=None):
    """Scripted boards that also record which agents each board call saw."""
    seen = []
    queue = itertools.chain(boards, itertools.repeat(then) if then else ())

    async def fake(messages, answers, config, user_id=None, inputs=None, agents=None):
        seen.append([a["agent"] for a in answers])
        return next(queue)

    monkeypatch.setattr(team, "ask_board", fake)
    return seen


def _entry(final, agent):
    team = final.additional_kwargs["team"]
    task = next(t for t in team["tasks"] if t["agent"] == agent)
    return {**team["results"][task["id"]], "withdrew": task["status"] == "withdrew"}


async def test_one_agent_withdraws_and_the_others_continue(monkeypatch):
    """Goal 2: an agent that cannot answer from its source withdraws; the others go on to board and write-up."""
    log = []
    seen = _boards_log(monkeypatch, Board(agreements=["same"]))
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",),
                           docs=Fake("docs", "DOCS-LEAK " + GAVE_UP, log=log), sql=Fake("sql", "Cap is 3T.", log=log),
                           research=Fake("research", "Notes: 3T.", log=log))
    chunks = await _stream(assistant, _ask(QUESTION))

    final = (await _thread(assistant))[-1]
    assert final.text == "Final."
    assert seen == [["sql", "research"]]  # the board sees no withdrawn agent
    (writeup,) = _writeups(model)
    assert "Cap is 3T." in writeup and "Notes: 3T." in writeup and "DOCS-LEAK" not in writeup
    assert _withdrawn(final.additional_kwargs["team"]) == ["docs"]
    assert (_entry(final, "docs")["withdrew"], _entry(final, "sql")["withdrew"],
            _entry(final, "research")["withdrew"]) == (True, False, False)
    assert {e["agent"]: e["withdrew"] for e in _events(chunks, "spoke")} == {"docs": True, "sql": False, "research": False}


async def test_transcript_withdrawn_is_empty_when_everyone_answers(monkeypatch):
    """Interfaces: the transcript always carries `withdrawn`."""
    _board(monkeypatch, Board())
    _, assistant = _two(monkeypatch, [])
    await _stream(assistant, _ask())
    assert _withdrawn((await _thread(assistant))[-1].additional_kwargs["team"]) == []


async def test_withdrawn_agent_is_left_out_of_conflict_rounds(monkeypatch):
    """Goal 2 + Goal 4: a fixable conflict re-runs its routes as today, but a withdrawn agent is left out and its round-1 answer never reaches the round."""
    log = []
    conflict = Conflict(routes=["sql", "research", "docs"], claim="CAP-CLAIM", fixable_by=["sql", "docs"], issue="differ")
    seen = _boards_log(monkeypatch, Board(conflicts=[conflict]), Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    docs = Fake("docs", "DOCS-LEAK " + GAVE_UP, log=log)
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",), docs=docs,
                           sql=Fake("sql", "sql v1", "sql v2", log=log), research=Fake("research", "notes", log=log))
    chunks = await _stream(assistant, _ask(QUESTION))

    rounds = _events(chunks, "wave")
    assert [(e["wave"], e["kind"], e["agents"]) for e in rounds] == [
        (1, "plan", ["docs", "sql", "research"]), (2, "conflict", ["sql", "research"])]
    assert docs.calls == 1
    assert seen == [["sql", "research"], ["sql", "research"]]
    second = [e["last"] for e in log if e["event"] == "start" and e["agent"] == "sql"][1]
    assert "CAP-CLAIM" in second.text and "DOCS-LEAK" not in second.text
    assert all("DOCS-LEAK" not in w for w in _writeups(model))


async def test_conflict_only_a_withdrawn_agent_could_fix_goes_to_write_up(monkeypatch):
    """Goal 2: a withdrawn agent is not a fixer, so the conflict is stated in the write-up (no conflict round)."""
    log = []
    conflict = Conflict(routes=["sql", "research", "docs"], claim="CAP-CLAIM", fixable_by=["docs"], issue="differ")
    _boards_log(monkeypatch, Board(conflicts=[conflict]))
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",), docs=Fake("docs", GAVE_UP, log=log),
                           sql=Fake("sql", "a", log=log), research=Fake("research", "b", log=log))
    chunks = await _stream(assistant, _ask(QUESTION))

    assert [(e["wave"], e["agents"]) for e in _events(chunks, "wave")] == [(1, ["docs", "sql", "research"])]
    assert "CAP-CLAIM" in _writeups(model)[0]


async def test_all_but_one_withdraw_skips_the_board_and_that_answer_stands(monkeypatch):
    """Goal 2 + Interfaces: at most one non-withdrawn answer -> no board call, no write-up; it stands."""
    log = []
    seen = _boards_log(monkeypatch, then=Board(agreements=["unused"]))
    model = per_agent(sql=[AIMessage("unused")])  # no orchestrator script: a call to it fails the test
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",), docs=Fake("docs", GAVE_UP, log=log),
                           sql=Fake("sql", "Cap is 3T.", log=log), research=Fake("research", "", log=log))
    chunks = await _stream(assistant, _ask(QUESTION))

    assert seen == []  # the board model got 0 calls
    final = (await _thread(assistant))[-1]
    assert (final.name, final.text) == ("sql_agent", "Cap is 3T.")
    assert not model.prompts and not _events(chunks, "synthesize")
    assert _withdrawn(final.additional_kwargs["team"]) == ["docs", "research"]


async def test_research_withdraws_on_no_findings_not_on_its_messages(monkeypatch):
    """Review Focus: research keeps findings outside `messages`; it withdraws on empty findings only."""
    class FindingsOnly(Fake):
        async def ainvoke(self, input, config=None, **kwargs):
            await super().ainvoke(input, config, **kwargs)
            return {"messages": list(input["messages"]), "findings": self.texts[0]}  # no AIMessage at all

    seen = _boards_log(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap is 3T.", log=[]),
                           research=FindingsOnly("research", "Notes: 3T.", log=[]))
    await _stream(assistant, _ask(QUESTION))

    final = (await _thread(assistant))[-1]
    assert seen == [["sql", "research"]] and _withdrawn(final.additional_kwargs["team"]) == []
    assert _entry(final, "research")["withdrew"] is False


async def test_every_agent_withdraws_the_orchestrator_answers_and_names_them(monkeypatch):
    """Goal 2: if every agent withdraws, the orchestrator answers as in normal mode and says the others found nothing."""
    log = []
    seen = _boards_log(monkeypatch, then=Board())
    model = per_agent(orchestrator=[AIMessage("Final answer.")])
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",),
                           docs=Fake("docs", "DOCS-LEAK " + GAVE_UP, log=log),
                           sql=Fake("sql", "SQL-LEAK " + GAVE_UP, log=log), research=Fake("research", "", log=log))
    await _stream(assistant, _ask(QUESTION))

    assert seen == []
    final = (await _thread(assistant))[-1]
    assert (final.name, final.text) == ("orchestrator", "Final answer.")
    (prompt,) = model.prompts
    extra = " ".join(m.text for m in prompt[1:] if m.text != QUESTION).lower()  # all but the system prompt and the question
    assert all(route in extra for route in ("docs", "sql", "research"))
    assert "LEAK" not in " ".join(m.text for m in prompt)


async def test_every_agent_withdraws_then_the_drawer_still_draws(monkeypatch):
    """Goal 2 + plan: after the orchestrator's normal-mode answer, `draw` runs when a drawer was asked."""
    _boards_log(monkeypatch, then=Board())
    chart = Fake("chart", "drawn", log=[])
    model = per_agent(orchestrator=[AIMessage("Final answer.")])
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",), docs=Fake("docs", GAVE_UP, log=[]),
                           sql=Fake("sql", GAVE_UP, log=[]), research=Fake("research", "", log=[]), chart=chart)
    chunks = await _stream(assistant, _ask("draw a chart of the top stocks"))
    assert _events(chunks, "wave")[0]["agents"] == ["docs", "sql", "research"] and chart.calls == 1


async def test_reply_to_a_question_applies_withdrawal_to_the_new_round(monkeypatch):
    """Review Focus: after a pause, the agent brought in by the reply may withdraw; it stays out of the board."""
    seen = _boards_log(monkeypatch, Board(question="Which data?"), Board())
    log = []
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",),
                           docs=Fake("docs", "DOCS-LEAK " + GAVE_UP, log=log), sql=Fake("sql", "rows", log=log),
                           research=Fake("research", "notes", log=log))
    await _stream(assistant, _ask(SQL_QUESTION))
    second = await _stream(assistant, Command(resume="check handbook.pdf"))

    assert [(e["wave"], e["agents"]) for e in _events(second, "wave")] == [(2, ["docs"])]
    assert seen == [["sql", "research"], ["sql", "research"]]
    assert _withdrawn((await _thread(assistant))[-1].additional_kwargs["team"]) == ["docs"]
    assert all("DOCS-LEAK" not in w for w in _writeups(model))


def test_named_source_gives_exactly_that_source_plus_research():
    """Goal 1 (naming a source restricts to it) + Goal 4 (research still joins whoever else takes part)."""
    assert _routes("Ask the SQL agent about it") == ["sql", "research"]
    assert _routes("Ask the docs agent about it") == ["docs", "research"]


def test_two_named_speakers_run_together_with_research():
    """Goal 1: naming two sources restricts to those two; Goal 4: research joins."""
    assert _routes("Ask the docs agent and the SQL agent about it") == ["docs", "sql", "research"]


async def test_every_agent_withdraws_and_the_orchestrator_hands_off(monkeypatch):
    """Goal 2 + Interfaces: if every speaker withdraws the orchestrator answers as in normal mode, and a handoff
    it chooses is run as in the nobody-mentioned path: that agent answers on the thread, with no error."""
    _boards_log(monkeypatch, then=Board())
    log = []
    sql = Fake("sql", GAVE_UP, "Apple's market cap is $3T.", log=log)  # withdraws in the round, answers when handed the thread
    model = per_agent(orchestrator=[AIMessage("", tool_calls=[{"name": "handoff", "args": {"agent": "sql"}, "id": "h1"}])])
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",), docs=Fake("docs", GAVE_UP, log=log),
                           sql=sql, research=Fake("research", "", log=log))
    await _stream(assistant, _ask(QUESTION))

    assert sql.calls == 2
    messages = await _thread(assistant)
    assert [type(m) for m in messages] == [HumanMessage, AIMessage]
    assert messages[-1].text == "Apple's market cap is $3T."


async def test_speaker_that_never_used_its_source_withdraws(monkeypatch):
    """Goal 2 + Task 6: a speaker that made no call to a source tool gave no answer from its source, so it withdraws,
    even when its text does not look like "nothing found"; one that ran its tool and answered stays."""
    log = []
    seen = _boards_log(monkeypatch, Board(agreements=["same"]))
    model = per_agent(orchestrator=[AIMessage("Final.")])
    no_data = "DOCS-LEAK I do not have access to real-time market data, so I cannot give you the current figures for this."
    assistant = _assistant(monkeypatch, model, documents=("handbook.pdf",), docs=Fake("docs", no_data, log=log, tools=False),
                           sql=Fake("sql", "Cap is 3T.", log=log, tools=True),
                           research=Fake("research", "Notes: 3T.", log=log, tools=True))
    chunks = await _stream(assistant, _ask(QUESTION))

    final = (await _thread(assistant))[-1]
    spoke = {e["agent"]: e["withdrew"] for e in _events(chunks, "spoke")}
    assert spoke == {"docs": True, "sql": False, "research": False}
    assert _withdrawn(final.additional_kwargs["team"]) == ["docs"]
    assert seen == [["sql", "research"]]
    assert all("DOCS-LEAK" not in w for w in _writeups(model))


# --- diagnosis of the 2026-10-26 chat (uncommitted) ---

class _Seeing(Fake):
    """A Fake that keeps every message it was sent."""

    async def ainvoke(self, input, config=None, **kwargs):
        self.seen = [m.text for m in input["messages"]]
        return await super().ainvoke(input, config, **kwargs)


async def test_docs_speaker_is_told_the_users_file_names(monkeypatch):
    """Bug 1: 'top growing stocks' names no file, so docs (which never sees the file list) did not search."""
    _board(monkeypatch, Board())
    docs = _Seeing("docs", "From the file.", log=[])
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, documents=("top_50_stocks_2024.pdf",), docs=docs,
                           sql=Fake("sql", "Rows.", log=[]), research=Fake("research", "Notes.", log=[]))
    await _stream(assistant, _ask("top growing stocks"))

    assert any("top_50_stocks_2024.pdf" in t for t in docs.seen)


async def test_orchestrator_cannot_chart_in_the_write_up_when_a_drawer_draws(monkeypatch):
    """Bug 2: the write-up kept chart_data, so it drew chart #1 and the chart agent drew chart #2."""
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")], chart=DRAW)
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], table=TABLE),
                           research=Fake("research", "Notes.", log=[]))
    await _stream(assistant, _ask("chart AAPL"))

    offered = {t["function"]["name"] for prompt, tools in zip(model.prompts, model.tools)
               if "You are smallie" in prompt[0].content for t in tools}
    assert offered.isdisjoint({"chart_table", "chart_data"})


# --- the drawer charts the answer's table (fix plan 2026-10-26-citation-ids-and-drawer-table, goal B) ---

CHART_DATA_CALL = AIMessage("", tool_calls=[{"name": "chart_data", "args": {
    "type": "bar", "title": "Caps", "labels": ["AAPL", "MSFT"], "series": [{"name": "cap", "values": [3.0, 2.5]}]}, "id": "d2"}])


def _recording_per_agent(**scripts):
    """per_agent that also keeps the `tool_choice` of each model call (None = not given)."""
    from tests.scripted import PROMPT_PHRASES, PerAgentModel

    class Recording(PerAgentModel):
        choices: list = []

        def bind_tools(self, tools, **kwargs):
            self.choices.append(kwargs.get("tool_choice"))
            return super().bind_tools(tools, **kwargs)

    return Recording(replies=iter(()), prompts=[], tools=[], choices=[],
                     scripts={PROMPT_PHRASES[n]: iter(r) for n, r in scripts.items()})


def _offered(model):
    return {t["function"]["name"] for tools in model.tools for t in tools}


def _unforced(model):
    return all(c in (None, "auto", "none") for c in model.choices)


async def test_drawer_cannot_copy_numbers_when_the_answer_has_a_table(monkeypatch):
    """Goal B: the final answer carries this turn's table, so the drawer is not offered `chart_data` (a call is
    refused: no chart from it), and it is never forced to call a tool."""
    _research_does_not_join(monkeypatch)
    _board(monkeypatch, Board())
    model = _recording_per_agent(chart=[CHART_DATA_CALL, AIMessage("Done.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], table=TABLE))
    chunks = await _stream(assistant, _ask(SQL_CHART))

    assert "chart_table" in _offered(model) and "chart_data" not in _offered(model)
    assert _unforced(model)
    final = (await _thread(assistant))[-1]
    assert "charts" not in final.additional_kwargs and not _events(chunks, "charts")


async def test_drawer_may_chart_inline_numbers_when_the_answer_has_no_table(monkeypatch):
    """Goal B: without a table on the final answer `chart_data` stays offered (figures written in the answer are fine);
    the drawer's first call is not forced."""
    _research_does_not_join(monkeypatch)
    _board(monkeypatch, Board())
    model = _recording_per_agent(chart=[CHART_DATA_CALL, AIMessage("Done.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "AAPL 3.0, MSFT 2.5.", log=[]))
    chunks = await _stream(assistant, _ask(SQL_CHART))

    assert "chart_data" in _offered(model) and _unforced(model)
    assert (await _thread(assistant))[-1].additional_kwargs["charts"] and _events(chunks, "charts")


async def test_a_table_from_an_earlier_turn_does_not_count(monkeypatch):
    """Review Focus B: only a table on THIS turn's final answer takes `chart_data` away."""
    class TableOnce(Fake):
        async def ainvoke(self, input, config=None, **kwargs):
            out = await super().ainvoke(input, config, **kwargs)
            self.table = None
            return out

    _research_does_not_join(monkeypatch)
    _board(monkeypatch, then=Board())
    model = _recording_per_agent(chart=[CHART_DATA_CALL, AIMessage("Done.")])
    assistant = _assistant(monkeypatch, model, sql=TableOnce("sql", "Cap.", log=[], table=TABLE))
    await _stream(assistant, _ask(SQL_QUESTION))  # turn 1: a table, no drawer
    assert "tables" in (await _thread(assistant))[-1].additional_kwargs
    await _stream(assistant, _ask(SQL_CHART))  # turn 2: no table

    assert "chart_data" in _offered(model)


# --- the task graph: plan, waves, workspace ---

def _docs_sql(monkeypatch, plan, log, *, docs=None, sql=None, research=None, final="Final.", board=True):
    """docs, sql and research are all available (the user has a file); `plan` is what the plan call returns."""
    _plan(monkeypatch, plan)
    if board:
        _board(monkeypatch, then=Board())
    agents = {"docs": docs or Fake("docs", "DOCS TEXT", log=log), "sql": sql or Fake("sql", "SQL TEXT", log=log),
              "research": research or Fake("research", "RESEARCH TEXT", log=log)}
    model = per_agent(orchestrator=[AIMessage(final)])
    return model, _assistant(monkeypatch, model, documents=("handbook.pdf",), **agents), agents


def _starts(log, agent):
    return [e for e in log if e["event"] == "start" and e["agent"] == agent]


async def test_dependent_task_waits_for_its_input(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="extract"), Task(id="t2", agent="sql", goal="check", depends_on=["t1"])])
    _, assistant, _ = _docs_sql(monkeypatch, plan, log, docs=Fake("docs", "DOCS TEXT", log=log, delay=0.1))
    await _stream(assistant, _ask(THREE))

    at = {(e["agent"], e["event"]): e["at"] for e in log}
    assert at[("docs", "end")] <= at[("sql", "start")]
    note = _starts(log, "sql")[0]["last"].text
    assert "DOCS TEXT" in note and "t1" in note


async def test_planned_tasks_without_inputs_wait_for_the_docs_task(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="read the file"), Task(id="t2", agent="research", goal="look up")])
    _, assistant, _ = _docs_sql(monkeypatch, plan, log)
    chunks = await _stream(assistant, _ask(THREE))

    (event,) = _events(chunks, "plan")
    assert {t["id"]: t["depends_on"] for t in event["tasks"]} == {"t1": [], "t2": ["t1"]}


async def test_the_fallback_runs_every_agent_in_wave_one(monkeypatch):
    log = []
    _, assistant, _ = _docs_sql(monkeypatch, Plan(tasks=[]), log)

    async def broken(messages, available, config, user_id=None):
        raise ValueError("no plan")

    monkeypatch.setattr(team, "ask_plan", broken)
    chunks = await _stream(assistant, _ask(THREE))

    (event,) = _events(chunks, "plan")
    assert event["fallback"] is True
    assert [sorted(e["agents"]) for e in _events(chunks, "wave")][0] == ["docs", "research", "sql"]


async def test_independent_tasks_share_a_wave(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="a"), Task(id="t2", agent="sql", goal="b", depends_on=["t1"]),
                       Task(id="t3", agent="research", goal="c", depends_on=["t1"])])
    _, assistant, _ = _docs_sql(monkeypatch, plan, log, sql=Fake("sql", "s", log=log, delay=0.2),
                                research=Fake("research", "r", log=log, delay=0.2))
    chunks = await _stream(assistant, _ask(THREE))

    at = {(e["agent"], e["event"]): e["at"] for e in log}
    assert at[("sql", "start")] < at[("research", "end")] and at[("research", "start")] < at[("sql", "end")]
    assert [(e["wave"], e["tasks"]) for e in _events(chunks, "wave")] == [(1, ["t1"]), (2, ["t2", "t3"])]


async def test_task_note_carries_input_tables(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="a"), Task(id="t2", agent="sql", goal="b", depends_on=["t1"])])
    table = {"id": "tbl1", "query": "q", "columns": ["a"], "rows": [[1]], "truncated": False}
    _, assistant, _ = _docs_sql(monkeypatch, plan, log, docs=Fake("docs", "D", log=log, table=table))
    await _stream(assistant, _ask(THREE))

    assert "tbl1" in _starts(log, "sql")[0]["last"].text


async def test_task_note_names_the_language(monkeypatch):
    log = []
    message = "Vốn hóa của AAPL là bao nhiêu?"
    plan = Plan(tasks=[Task(id="t1", agent="sql", goal="lấy vốn hóa")])
    _, assistant, _ = _docs_sql(monkeypatch, plan, log)
    await _stream(assistant, _ask(message))

    note = _starts(log, "sql")[0]["last"].text
    assert reply_language([HumanMessage(message)]) == "Vietnamese" and "Vietnamese" in note


async def test_two_tasks_of_one_agent_stream_apart(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="sql", goal="a"), Task(id="t2", agent="sql", goal="b")])
    _, assistant, agents = _docs_sql(monkeypatch, plan, log)
    chunks = await _stream(assistant, _ask(THREE))

    speakers = _events(chunks, "speaker")
    assert [e["agent"] for e in speakers] == ["sql", "sql"] and agents["sql"].calls == 2
    assert {e["task"] for e in speakers} == {"t1", "t2"} and len({e["ns"] for e in speakers}) == 2


async def test_empty_plan_orchestrator_answers_alone(monkeypatch):
    log = []
    model, assistant, agents = _docs_sql(monkeypatch, Plan(), log, final="Alone.")
    chunks = await _stream(assistant, _ask(THREE))

    assert not _events(chunks, "wave") and len(model.prompts) == 1
    assert [(type(m), m.name, m.text) for m in await _thread(assistant)][1:] == [(AIMessage, "orchestrator", "Alone.")]
    assert not log


async def test_plan_failure_falls_back_to_participants(monkeypatch):
    _board(monkeypatch, Board())
    _, assistant = _two(monkeypatch, [])
    chunks = await _stream(assistant, _ask())

    (plan,) = _events(chunks, "plan")
    assert plan["fallback"] is True and [t["agent"] for t in plan["tasks"]] == ["sql", "research"]


async def test_one_named_speaker_skips_the_plan_call(monkeypatch):
    _research_does_not_join(monkeypatch)
    asked = _plan(monkeypatch)  # a call would raise StopIteration
    _board(monkeypatch, Board())
    log = []
    assistant = _assistant(monkeypatch, per_agent(sql=[AIMessage("unused")]), sql=Fake("sql", "Cap is 3T.", log=log))
    chunks = await _stream(assistant, _ask(SQL_QUESTION))

    assert asked == [] and [t["agent"] for t in _events(chunks, "plan")[0]["tasks"]] == ["sql"]
    assert (await _thread(assistant))[-1].text == "Cap is 3T."


async def test_task_with_all_inputs_dead_is_skipped(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="a"), Task(id="t2", agent="sql", goal="b", depends_on=["t1"])])
    _, assistant, agents = _docs_sql(monkeypatch, plan, log, docs=Fake("docs", "nothing", log=log, tools=False), final="Nothing found.")
    await _stream(assistant, _ask(THREE))

    assert agents["sql"].calls == 0
    status = {t["id"]: t["status"] for t in (await _thread(assistant))[-1].additional_kwargs["team"]["tasks"]}
    assert status == {"t1": "withdrew", "t2": "skipped"}


async def test_task_with_some_inputs_dead_runs_and_is_told(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="a"), Task(id="t2", agent="sql", goal="b"),
                       Task(id="t3", agent="research", goal="c", depends_on=["t1", "t2"])])
    _, assistant, agents = _docs_sql(monkeypatch, plan, log, docs=Fake("docs", "nothing", log=log, tools=False))
    await _stream(assistant, _ask(THREE))

    note = _starts(log, "research")[0]["last"].text
    assert agents["research"].calls == 1
    assert "t1" in note.split("MISSING INPUTS")[1] and "SQL TEXT" in note.split("MISSING INPUTS")[0]


async def test_no_review_for_a_wave_without_inputs_or_meeting(monkeypatch):
    log, reviewed = [], []

    async def fake(messages, answers, config, user_id=None, inputs=None, agents=None):
        reviewed.append([a["agent"] for a in answers])
        return Board()

    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="a"), Task(id="t2", agent="sql", goal="b", depends_on=["t1"])])
    _, assistant, _ = _docs_sql(monkeypatch, plan, log, board=False)
    monkeypatch.setattr(team, "ask_board", fake)
    await _stream(assistant, _ask(THREE))

    assert reviewed == [["docs", "sql"]]  # the last wave only; it reads every live task


async def test_second_message_starts_an_empty_workspace(monkeypatch):
    log = []
    _plan(monkeypatch,
          Plan(tasks=[Task(id="t1", agent="sql", goal="a"), Task(id="t2", agent="research", goal="b")]),
          Plan(tasks=[Task(id="t1", agent="sql", goal="c")]))
    _board(monkeypatch, then=Board())
    model = per_agent(orchestrator=[AIMessage("F1"), AIMessage("F2")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "d1", "d2", log=log), research=Fake("research", "n1", log=log))
    await _stream(assistant, _ask())
    await _stream(assistant, _ask("And the AAPL market cap in euro?"))

    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert [(t["id"], t["agent"]) for t in transcript["tasks"]] == [("t1", "sql")]
    assert {k: v["text"] for k, v in transcript["results"].items()} == {"t1": "d2"}
    assert all(e.get("task") in (None, "t1") for e in transcript["history"])


async def test_transcript_is_the_workspace(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="sql", goal="a"), Task(id="t2", agent="research", goal="b")])
    _, assistant, _ = _docs_sql(monkeypatch, plan, log)
    await _stream(assistant, _ask(THREE))

    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert {"tasks", "results", "history", "synthesis"} <= set(transcript)
    assert {t["id"]: t["status"] for t in transcript["tasks"]} == {"t1": "done", "t2": "done"}
    assert transcript["results"]["t1"]["text"] == "SQL TEXT" and transcript["results"]["t1"]["agent"] == "sql"
    assert [e["type"] for e in transcript["history"] if e["type"] == "wave"] == ["wave"]
    assert "rounds_at" not in transcript


def test_task_message_formats():
    from app.orchestration.prompts import TASK_MESSAGE

    text = TASK_MESSAGE.format(goal="G", inputs="none", missing="none", language="Vietnamese", drawing="")
    assert "Vietnamese" in text and "{" not in text


# --- the chart task: the chart agent is a task like any other ---

def _chart_plan(monkeypatch, *, table, log, chart_depends=("t1",), with_research=False):
    tasks = [Task(id="t1", agent="sql", goal="get caps")]
    if with_research:
        tasks.append(Task(id="t2", agent="research", goal="background"))
    tasks.append(Task(id="t3" if with_research else "t2", agent="chart", goal="chart the caps", depends_on=list(chart_depends)))
    _plan(monkeypatch, Plan(tasks=tasks))
    _board(monkeypatch, then=Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    return model, _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log, table=table),
                             research=Fake("research", "Notes.", log=log),
                             chart=Fake("chart", "drawn", log=log, chart="chart-1"))


async def test_chart_task_charts_its_inputs_table(monkeypatch):
    log = []
    _, assistant = _chart_plan(monkeypatch, table={**TABLE, "id": "tbl1"}, log=log)
    chunks = await _stream(assistant, _ask())

    (start,) = _starts(log, "chart")
    assert start["sources"] == team.NO_SOURCES and start["deny"] == ("chart_data",)
    assert len(_waves((await _thread(assistant))[-1].additional_kwargs["team"])) == 1  # the chart task takes no wave
    assert (await _thread(assistant))[-1].additional_kwargs["charts"] == [{"id": "chart-1"}]
    assert [e["charts"] for e in _events(chunks, "charts")] == [[{"id": "chart-1"}]]
    assert "chart" in {e["agent"] for e in _events(chunks, "speaker")}


async def test_chart_task_without_table_may_use_chart_data(monkeypatch):
    log = []
    _, assistant = _chart_plan(monkeypatch, table=None, log=log)
    await _stream(assistant, _ask())

    (start,) = _starts(log, "chart")
    assert start["sources"] == team.NO_SOURCES and start["deny"] == ()


async def test_write_up_cannot_chart_when_a_chart_task_exists(monkeypatch):
    log = []
    model, assistant = _chart_plan(monkeypatch, table={**TABLE, "id": "tbl1"}, log=log, with_research=True)
    await _stream(assistant, _ask())

    offered = {t["function"]["name"] for prompt, tools in zip(model.prompts, model.tools)
               if "You are smallie" in prompt[0].content for t in tools}
    assert _writeups(model) and offered.isdisjoint({"chart_table", "chart_data"})


async def test_fallback_plan_makes_chart_depend_on_the_answer(monkeypatch):
    _board(monkeypatch, then=Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    log = []
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log), research=Fake("research", "n", log=log),
                           chart=Fake("chart", "drawn", log=log))
    chunks = await _stream(assistant, _ask("chart AAPL"))

    (plan,) = _events(chunks, "plan")
    tasks = {t["agent"]: t for t in plan["tasks"]}
    assert plan["fallback"] is True and tasks["chart"]["depends_on"] == ["answer"]


async def test_dependency_on_a_chart_task_is_dropped(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="sql", goal="get caps"), Task(id="t2", agent="chart", goal="chart", depends_on=["t1"]),
                       Task(id="t3", agent="research", goal="background", depends_on=["t2"])])
    _plan(monkeypatch, plan)
    _board(monkeypatch, then=Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log), research=Fake("research", "Notes.", log=log),
                           chart=Fake("chart", "drawn", log=log, chart="chart-1"))
    chunks = await _stream(assistant, _ask())

    tasks = {t["agent"]: t for t in _events(chunks, "plan")[0]["tasks"]}
    assert tasks["research"]["depends_on"] == []
    assert len(_starts(log, "research")) == 1


async def test_task_note_says_the_chart_agent_draws(monkeypatch):
    log = []
    _plan(monkeypatch, Plan(tasks=[Task(id="t1", agent="sql", goal="get caps"), Task(id="t2", agent="chart", goal="chart")]))
    _board(monkeypatch, then=Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log), chart=Fake("chart", "drawn", log=log, chart="chart-1"))
    await _stream(assistant, _ask())
    assert "Never say a chart can't be drawn" in _starts(log, "sql")[0]["last"].text

    log.clear()
    _plan(monkeypatch, Plan(tasks=[Task(id="t1", agent="sql", goal="get caps")]))
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]), sql=Fake("sql", "Cap.", log=log))
    await _stream(assistant, _ask(), {"configurable": {"thread_id": "no-drawer"}})
    assert "can't be drawn" not in _starts(log, "sql")[0]["last"].text


# --- hand-off challenges and per-meeting-point rounds ---

def _challenge(to="t1", by="t2", question="Q-SPLIT?", evidence="EV-CLOSE"):
    return Review(challenges=[Challenge(to_task=to, by_task=by, question=question, evidence=evidence)])


def _docs_then_sql(extra=()):
    return Plan(tasks=[Task(id="t1", agent="docs", goal="extract"), Task(id="t2", agent="sql", goal="check", depends_on=["t1"]), *extra])


async def test_challenge_gives_the_producer_one_round(monkeypatch):
    log = []
    _, assistant, agents = _docs_sql(monkeypatch, _docs_then_sql(), log, board=False)
    _board(monkeypatch, _challenge(), then=Board())
    chunks = await _stream(assistant, _ask(THREE))

    assert agents["docs"].calls == 2
    second = _starts(log, "docs")[1]["last"]
    assert second.name == "team" and "Q-SPLIT?" in second.text and "EV-CLOSE" in second.text and "DOCS TEXT" in second.text
    assert [(e["kind"], e["tasks"]) for e in _events(chunks, "wave")][-1] == ("challenge", ["t1"])


async def test_revised_output_reruns_only_its_users(monkeypatch):
    log = []
    plan = _docs_then_sql([Task(id="t3", agent="research", goal="background")])
    _, assistant, agents = _docs_sql(monkeypatch, plan, log, board=False)
    _board(monkeypatch, _challenge(), Review(outcomes=[Outcome(task="t1", revised=True)]), then=Board())
    await _stream(assistant, _ask(THREE))

    assert [agents[n].calls for n in ("docs", "sql", "research")] == [2, 2, 2]


async def test_output_that_stands_becomes_an_open_point(monkeypatch):
    log = []
    model, assistant, agents = _docs_sql(monkeypatch, _docs_then_sql(), log, board=False)
    _board(monkeypatch, _challenge(), Review(outcomes=[Outcome(task="t1", revised=False)]), then=Board())
    await _stream(assistant, _ask(THREE))

    assert [agents[n].calls for n in ("docs", "sql")] == [2, 1]
    assert "Q-SPLIT?" in _writeups(model)[0]


async def test_a_meeting_point_gets_at_most_two_rounds(monkeypatch):
    log = []
    model, assistant, agents = _docs_sql(monkeypatch, _docs_then_sql(), log, board=False)
    _board(monkeypatch, then=_challenge())
    await _stream(assistant, _ask(THREE))

    assert agents["docs"].calls == 1 + 2 == 1 + get_settings().team_max_rounds
    final = (await _thread(assistant))[-1]
    assert final.additional_kwargs["team"]["synthesis"]["forced"] is True
    assert len(_writeups(model)) == 1 and "Q-SPLIT?" in _writeups(model)[0]


async def test_cap_counts_per_meeting_point(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="a"), Task(id="t2", agent="research", goal="b"),
                       Task(id="t3", agent="sql", goal="c", depends_on=["t1", "t2"])])
    _, assistant, agents = _docs_sql(monkeypatch, plan, log, board=False)
    both = Review(challenges=[Challenge(to_task="t1", by_task="t3", question="Q1", evidence="e"),
                              Challenge(to_task="t2", by_task="t3", question="Q2", evidence="e")])
    _board(monkeypatch, then=both)
    await _stream(assistant, _ask(THREE))

    assert agents["docs"].calls == agents["research"].calls == 1 + get_settings().team_max_rounds


async def test_meeting_conflict_follows_board_rules(monkeypatch):
    undated = Difference(routes=["sql", "docs"], explanation="e", sides=[Side(route="sql", value="1", source="", as_of="")])
    assert len(clean(Board(differences=[undated]), ["sql", "docs"]).conflicts) == 1

    review = Review(differences=[undated], conflicts=[Conflict(routes=["sql", "docs"], claim="CAP-CLAIM", fixable_by=["sql"], issue="i")])
    _board(monkeypatch, review, then=Board())
    _, assistant, agents = _three(monkeypatch, [])
    chunks = await _stream(assistant, _ask(THREE))

    assert [(e["kind"], e["agents"]) for e in _events(chunks, "wave")] == [("plan", ["docs", "sql", "research"]), ("conflict", ["docs", "sql"])]
    assert len(_events(chunks, "review")[0]["conflicts"]) == 2


def test_cause_defaults_to_unknown():
    assert Conflict(routes=[], claim="c").cause == "unknown" and Difference(routes=[], explanation="e").cause == "unknown"


def test_clean_drops_challenges_of_tasks_that_are_not_live():
    review = Review(challenges=[Challenge(to_task="t1", by_task="t2", question="q", evidence="e"),
                                Challenge(to_task="t1", by_task="t9", question="q", evidence="e")],
                    outcomes=[Outcome(task="t1", revised=True), Outcome(task="t9", revised=True)])
    cleaned = clean(review, ["sql"], ["t1", "t2"])
    assert [c.by_task for c in cleaned.challenges] == ["t2"] and [o.task for o in cleaned.outcomes] == ["t1"]


async def test_ask_board_lists_what_each_task_received(monkeypatch):
    seen = []

    async def ainvoke(messages, config):
        seen.append(messages[1].content)
        return Review()

    monkeypatch.setattr(team, "get_chat_model", lambda model=None, **kw: SimpleNamespace(
        with_structured_output=lambda schema, method: SimpleNamespace(ainvoke=ainvoke)))
    answers = [{"task": "t1", "agent": "docs", "text": "D"}, {"task": "t2", "agent": "sql", "text": "S"}]
    await team.ask_board([HumanMessage("q?")], answers, {"callbacks": []}, user_id="u", inputs={"t2": ["t1"]})
    assert "t2 (sql) received: t1" in seen[0]


def test_review_prompt_and_challenge_message_format():
    from app.orchestration import prompts
    assert "Vietnamese" in prompts.BOARD_PROMPT.format(language="Vietnamese")
    text = prompts.CHALLENGE_MESSAGE.format(question="Q", evidence="E", own="O", language="Vietnamese")
    assert "{" not in text and "Vietnamese" in text


# --- follow-ups ---

def _follow(goal="check the claim", agent="sql", depends_on=("t1",)):
    return Review(follow_ups=[Task(id="x", agent=agent, goal=goal, depends_on=list(depends_on))])


async def test_follow_up_runs_with_its_inputs(monkeypatch):
    log = []
    _, assistant, agents = _docs_sql(monkeypatch, Plan(tasks=[Task(id="t1", agent="docs", goal="extract")]), log, board=False)
    _board(monkeypatch, _follow(), then=Board())
    chunks = await _stream(assistant, _ask(THREE))

    assert [e["wave"] for e in _events(chunks, "wave")] == [1, 2]
    assert [t["id"] for t in _events(chunks, "plan_updated")[0]["tasks"]] == ["t2"]
    note = _starts(log, "sql")[0]["last"].text
    assert "DOCS TEXT" in note and "check the claim" in note
    final = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert [(t["id"], t["origin"]) for t in final["tasks"]] == [("t1", "plan"), ("t2", "follow_up")]


async def test_follow_up_does_not_wait_on_a_failed_task(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="a"), Task(id="t2", agent="research", goal="b")])
    _, assistant, agents = _docs_sql(monkeypatch, plan, log, docs=_Failing(), board=False)
    _board(monkeypatch, _follow(depends_on=("t1", "t2")), then=Board())
    await _stream(assistant, _ask(THREE))

    assert agents["sql"].calls == 1
    tasks = (await _thread(assistant))[-1].additional_kwargs["team"]["tasks"]
    follow = next(t for t in tasks if t["origin"] == "follow_up")
    assert follow["status"] == "done" and follow["depends_on"] == ["t2"]


async def test_follow_up_whose_only_input_failed_still_runs(monkeypatch):
    class FailsOnce(Fake):
        async def ainvoke(self, input, config=None, **kwargs):
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("boom")
            return await super().ainvoke(input, config, **kwargs)

    log = []
    plan = Plan(tasks=[Task(id="t1", agent="sql", goal="a"), Task(id="t2", agent="research", goal="b"),
                       Task(id="t3", agent="research", goal="c", depends_on=["t1", "t2"])])
    _, assistant, agents = _docs_sql(monkeypatch, plan, log, sql=FailsOnce("sql", "SQL TEXT", log=log), board=False)
    _board(monkeypatch, _follow(depends_on=("t1",)), then=Board())
    await _stream(assistant, _ask(THREE))

    assert agents["sql"].calls == 2
    tasks = (await _thread(assistant))[-1].additional_kwargs["team"]["tasks"]
    follow = next(t for t in tasks if t["origin"] == "follow_up")
    assert follow["status"] == "done" and follow["depends_on"] == []


async def test_follow_ups_past_the_cap_are_named(monkeypatch):
    monkeypatch.setattr(get_settings(), "team_max_tasks", 2)
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="docs", goal="a"), Task(id="t2", agent="research", goal="b")])
    model, assistant, agents = _docs_sql(monkeypatch, plan, log, board=False)
    _board(monkeypatch, _follow(), then=Board())
    chunks = await _stream(assistant, _ask(THREE))

    assert agents["sql"].calls == 0 and not _events(chunks, "plan_updated")
    assert "check the claim" in _writeups(model)[0]
    assert (await _thread(assistant))[-1].additional_kwargs["team"]["synthesis"]["not_done"] == ["check the claim"]


async def test_repeated_follow_up_is_dropped(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="sql", goal="Extract Revenue"), Task(id="t2", agent="research", goal="b")])
    _, assistant, agents = _docs_sql(monkeypatch, plan, log, board=False)
    _board(monkeypatch, then=_follow(goal="  extract   revenue ", depends_on=()))
    chunks = await _stream(assistant, _ask(THREE))

    assert agents["sql"].calls == 1 and not _events(chunks, "plan_updated")


async def test_follow_up_for_an_unavailable_agent_is_dropped(monkeypatch):
    log = []
    _plan(monkeypatch, Plan(tasks=[Task(id="t1", agent="sql", goal="a"), Task(id="t2", agent="research", goal="b")]))
    _board(monkeypatch, _follow(agent="docs", depends_on=()), then=Board())
    docs = Fake("docs", "DOCS TEXT", log=log)
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]), docs=docs,
                           sql=Fake("sql", "SQL TEXT", log=log), research=Fake("research", "RESEARCH TEXT", log=log))
    chunks = await _stream(assistant, _ask(THREE))

    assert docs.calls == 0 and not _events(chunks, "plan_updated")
    assert all(t["agent"] != "docs" for t in (await _thread(assistant))[-1].additional_kwargs["team"]["tasks"])


async def test_last_wave_with_one_task_is_reviewed_and_may_grow_the_plan_below_the_cap(monkeypatch):
    for cap, sql_runs, plan_updates in ((6, 1, 1), (1, 0, 0)):
        monkeypatch.setattr(get_settings(), "team_max_tasks", cap)
        log, reviewed = [], []

        async def fake(messages, answers, config, user_id=None, inputs=None, agents=None):
            reviewed.append(answers)
            return _follow(goal="more", depends_on=()) if len(reviewed) == 1 else Review()

        _, assistant, agents = _docs_sql(monkeypatch, Plan(tasks=[Task(id="t1", agent="docs", goal="a")]), log, board=False)
        monkeypatch.setattr(team, "ask_board", fake)
        chunks = await _stream(assistant, _ask(THREE))

        assert len(reviewed[0]) == 1  # a lone task is reviewed, at the cap too
        assert agents["sql"].calls == sql_runs and len(_events(chunks, "plan_updated")) == plan_updates


async def test_failed_review_adds_no_follow_ups(monkeypatch):
    async def broken(messages, answers, config, user_id=None, inputs=None, agents=None):
        raise RuntimeError("boom")

    log = []
    model, assistant, _ = _docs_sql(monkeypatch, Plan(tasks=[Task(id="t1", agent="docs", goal="a")]), log, board=False)
    monkeypatch.setattr(team, "ask_board", broken)
    chunks = await _stream(assistant, _ask(THREE))

    assert not _events(chunks, "plan_updated") and (await _thread(assistant))[-1].text == "Final."


async def test_chart_follow_up_waits_for_the_answer(monkeypatch):
    log = []
    _plan(monkeypatch, Plan(tasks=[Task(id="t1", agent="sql", goal="get caps"), Task(id="t2", agent="research", goal="b")]))
    follow = Review(follow_ups=[Task(id="x", agent="chart", goal="chart the caps", depends_on=["t1"])])
    _board(monkeypatch, follow, then=Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log, table={**TABLE, "id": "tbl1"}),
                           research=Fake("research", "Notes.", log=log), chart=Fake("chart", "drawn", log=log, chart="chart-1"))
    chunks = await _stream(assistant, _ask())

    (event,) = _events(chunks, "plan_updated")
    assert [(t["agent"], t["depends_on"]) for t in event["tasks"]] == [("chart", ["answer"])]
    assert len(_starts(log, "chart")) == 1 and [e["wave"] for e in _events(chunks, "wave")] == [1]
    assert (await _thread(assistant))[-1].additional_kwargs["charts"] == [{"id": "chart-1"}]


async def test_review_follow_up_does_not_add_a_second_chart(monkeypatch):
    log = []
    _plan(monkeypatch, Plan(tasks=[Task(id="t1", agent="sql", goal="get caps"), Task(id="t2", agent="research", goal="b"),
                                   Task(id="t3", agent="chart", goal="chart the caps", depends_on=["t1"])]))
    follow = Review(follow_ups=[Task(id="x", agent="chart", goal="draw a bar chart of the market caps", depends_on=["t1"])])
    _board(monkeypatch, follow, then=Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log, table={**TABLE, "id": "tbl1"}),
                           research=Fake("research", "Notes.", log=log), chart=Fake("chart", "drawn", log=log, chart="chart-1"))
    await _stream(assistant, _ask())

    assert len(_starts(log, "chart")) == 1
    assert len((await _thread(assistant))[-1].additional_kwargs["charts"]) == 1


# --- write-up rules and budget stop ---

class _OverBudget(Runnable):
    def invoke(self, input, config=None, **kwargs):
        raise NotImplementedError

    async def ainvoke(self, input, config=None, **kwargs):
        from app.agents.remote import CallBudgetExceeded
        raise CallBudgetExceeded("spent")


async def test_write_up_names_causes_open_points_and_not_done(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="sql", goal="get caps"), Task(id="t2", agent="research", goal="b")])
    model, assistant, _ = _docs_sql(monkeypatch, plan, log, board=False)
    _board(monkeypatch, Review(conflicts=[Conflict(routes=["sql", "research"], claim="CAP-CLAIM", cause="date")],
                               challenges=[Challenge(to_task="t1", by_task="t2", question="OPEN-QUESTION", evidence="e")]),
           then=Board())
    monkeypatch.setattr(get_settings(), "team_max_rounds", 0)  # the challenge cannot run: it stays open
    await _stream(assistant, _ask(THREE))

    note = _writeups(model)[0]
    assert "date" in note and "OPEN-QUESTION" in note and "observed" in note
    assert "OPEN POINTS" in note and "WORK NOT DONE" in note


async def test_write_up_names_the_language(monkeypatch):
    _board(monkeypatch, Board(conflicts=[Conflict(routes=["sql", "research"], claim="X", cause="error")]))
    model, assistant = _two(monkeypatch, [])
    await _stream(assistant, _ask("Vốn hóa của AAPL là bao nhiêu?"))
    assert "Vietnamese" in _writeups(model)[0]


async def test_budget_stops_new_waves(monkeypatch):
    log = []
    plan = Plan(tasks=[Task(id="t1", agent="sql", goal="get caps"), Task(id="t2", agent="docs", goal="read notes"),
                       Task(id="t3", agent="research", goal="compare both", depends_on=["t1", "t2"])])
    model, assistant, agents = _docs_sql(monkeypatch, plan, log, sql=_OverBudget(), board=False)
    _board(monkeypatch, _follow(agent="research", goal="check the date", depends_on=()), then=Board())
    await _stream(assistant, _ask(THREE))

    assert agents["research"].calls == 0
    note = _writeups(model)[0]
    assert "compare both" in note and "check the date" in note


def test_team_message_formats():
    from app.orchestration.prompts import TEAM_MESSAGE

    text = TEAM_MESSAGE.format(answers="a", differences="d", conflicts="c", open_points="o", not_done="n",
                               language="Vietnamese", drawing=" DRAW")
    assert "Vietnamese" in text and "DRAW" in text and "{" not in text


async def test_review_records_evidence_from_sides_and_challenges(monkeypatch):
    first = Review(differences=[Difference(routes=["sql", "research"], explanation="dates", sides=SIDES)],
                   challenges=[Challenge(to_task="t1", by_task="t2", question="recheck?", evidence="research saw 2.9T")])
    _board(monkeypatch, first, then=Review())
    model, assistant = _two(monkeypatch, [])
    await _stream(assistant, _ask())

    team_ws = (await _thread(assistant))[-1].additional_kwargs["team"]
    ids = {t["agent"]: t["id"] for t in team_ws["tasks"]}
    assert team_ws["evidence"] == [
        {"task": ids["sql"], "claim": "v1", "source": "db", "as_of": "2024-01-01"},
        {"task": ids["research"], "claim": "v2", "source": "a page", "as_of": "2024-02-01"},
        {"task": "t2", "claim": "research saw 2.9T", "source": "", "as_of": ""}]


async def test_evidence_resets_per_message(monkeypatch):
    first = Review(challenges=[Challenge(to_task="t1", by_task="t2", question="recheck?", evidence="old claim")])
    _board(monkeypatch, first, then=Review())
    model = per_agent(orchestrator=[AIMessage("Final."), AIMessage("Again.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap is 3T.", log=[]), research=Fake("research", "Notes: 3T.", log=[]))
    await _stream(assistant, _ask())
    await _stream(assistant, _ask("And again?"))

    assert (await _thread(assistant))[-1].additional_kwargs["team"]["evidence"] == []


async def test_retry_follow_up_reruns_the_previous_team_turn(monkeypatch):
    """"try again" after a team turn re-runs the same agents on the earlier question; "thanks" stays alone."""
    _board(monkeypatch, Board())
    log = []
    model = per_agent(orchestrator=[AIMessage("Final."), AIMessage("Final again."), AIMessage("You are welcome.")])
    sql, research = Fake("sql", "Cap is 3T.", log=log), Fake("research", "Notes: 3T.", log=log)
    assistant = _assistant(monkeypatch, model, sql=sql, research=research)
    await _stream(assistant, _ask())
    seen = len(log)

    chunks = await _stream(assistant, _ask("try again"))
    assert [(e["wave"], sorted(e["agents"])) for e in _events(chunks, "wave")] == [(1, ["research", "sql"])]
    assert sql.calls == 2 and research.calls == 2
    assert all(QUESTION in e["last"].text for e in log[seen:] if e["event"] == "start")

    chunks = await _stream(assistant, _ask("thanks"))
    assert not _events(chunks, "wave") and sql.calls == 2


async def test_one_named_speaker_and_reply_tasks_are_medium_default(monkeypatch):
    _research_does_not_join(monkeypatch)
    _plan(monkeypatch)
    _board(monkeypatch, Board())
    assistant = _assistant(monkeypatch, per_agent(sql=[AIMessage("unused")]), sql=Fake("sql", "Cap is 3T.", log=[]))
    await _stream(assistant, _ask(SQL_QUESTION))
    tasks = (await _thread(assistant))[-1].additional_kwargs["team"]["tasks"]
    assert [(x["difficulty"], x["difficulty_source"]) for x in tasks] == [("medium", "default")]

    assistant, _, _ = await _chart_asks(monkeypatch, "check handbook.pdf")
    tasks = (await _thread(assistant))[-1].additional_kwargs["team"]["tasks"]
    docs = next(x for x in tasks if x["origin"] == "reply")
    assert (docs["difficulty"], docs["difficulty_source"]) == ("medium", "default")


def test_retry_keeps_old_difficulty():
    spec, info = SimpleNamespace(route="sql"), RouteInfo()
    old = {"goal": "g", "tasks": [
        {"id": "t1", "agent": spec.route, "goal": "x", "depends_on": [], "status": "done", "origin": "plan",
         "difficulty": "hard", "difficulty_reason": "why", "difficulty_source": "plan"},
        {"id": "t2", "agent": spec.route, "goal": "y", "depends_on": [], "status": "done", "origin": "plan"}]}
    messages = [HumanMessage("q"), AIMessage("a", additional_kwargs={"team": old}), HumanMessage("try again")]
    out = team.retry_tasks(messages, [(spec, info)])
    assert [(x["difficulty"], x["difficulty_reason"], x["difficulty_source"]) for x in out] == [
        ("hard", "why", "plan"), ("medium", "", "default")]


# --- each task runs on its tier and records its model ---

SECRET = "sk-SECRET-KEY"


def _profile(monkeypatch, profile):
    saved = ModelSettings("http://x", "std-model", SECRET, small_model="small-model", strong_model="strong-model", profile=profile)
    monkeypatch.setattr(team, "load_settings", lambda user_id: saved)


def _rated(*levels):
    agents = ["sql", "research"]
    return Plan(tasks=[Task(id=f"t{i + 1}", agent=agents[i], goal=f"g{i}", difficulty=d) for i, d in enumerate(levels)])


async def test_task_runs_on_its_tier(monkeypatch):
    _profile(monkeypatch, "economy")
    _plan(monkeypatch, _rated("medium", "medium"))
    _board(monkeypatch, then=Board())
    log = []
    _, assistant = _two(monkeypatch, log)
    await _stream(assistant, _ask())
    assert {e["agent"]: e["tier"] for e in log if e["event"] == "start"} == {"sql": "small", "research": "small"}


async def test_parallel_tasks_get_their_own_tier(monkeypatch):
    _profile(monkeypatch, "balanced")
    _plan(monkeypatch, _rated("easy", "hard"))
    _board(monkeypatch, then=Board())
    log = []
    _, assistant = _two(monkeypatch, log)
    await _stream(assistant, _ask())
    assert {e["agent"]: e["tier"] for e in log if e["event"] == "start"} == {"sql": "small", "research": "strong"}


async def test_spoke_carries_trace(monkeypatch):
    _profile(monkeypatch, "economy")
    _plan(monkeypatch, _rated("medium", "hard"))
    _board(monkeypatch, then=Board())
    _, assistant = _two(monkeypatch, [])
    chunks = await _stream(assistant, _ask())
    spoke = {e["agent"]: e for e in _events(chunks, "spoke")}
    assert (spoke["sql"]["tier"], spoke["sql"]["model"]) == ("small", "small-model")
    assert (spoke["research"]["tier"], spoke["research"]["model"]) == ("standard", "std-model")
    assert spoke["sql"]["reason"] and spoke["sql"]["fallback"] is None
    assert set(spoke["sql"]["usage"]) == {"model_calls", "input_tokens", "output_tokens", "cost"}
    assert SECRET not in json.dumps([e for _, e in chunks if isinstance(e, dict)], default=str)
    plan = _events(chunks, "plan")[0]
    assert [t["difficulty"] for t in plan["tasks"]] == ["medium", "hard"]
    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    result = next(r for r in transcript["results"].values() if r["agent"] == "sql")
    assert result["difficulty"] == "medium" and result["difficulty_source"] == "plan" and result["profile"] == "economy"
    assert result["model"] == "small-model" and result["duration_s"] >= 0
    assert SECRET not in json.dumps(transcript, default=str)


async def test_fallback_reaches_trace(monkeypatch):
    _profile(monkeypatch, "economy")
    _plan(monkeypatch, _rated("easy", "easy"))
    _board(monkeypatch, then=Board())
    fell = {"from_model": "small-model", "category": "model_not_found"}
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], meta={"fallback": fell}),
                           research=Fake("research", "Notes.", log=[]))
    chunks = await _stream(assistant, _ask())
    spoke = {e["agent"]: e for e in _events(chunks, "spoke")}
    assert spoke["sql"]["fallback"] == fell and spoke["research"]["fallback"] is None
    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert next(r for r in transcript["results"].values() if r["agent"] == "sql")["fallback"] == fell


async def test_task_start_event_carries_the_trace_before_spoke(monkeypatch):
    _profile(monkeypatch, "economy")
    _plan(monkeypatch, _rated("medium", "hard"))
    _board(monkeypatch, then=Board())
    _, assistant = _two(monkeypatch, [])
    chunks = await _stream(assistant, _ask())
    started = {e["agent"]: e for e in _events(chunks, "task_started")}
    assert set(started) == {"sql", "research"}
    assert (started["sql"]["difficulty"], started["sql"]["tier"], started["sql"]["model"]) == ("medium", "small", "small-model")
    assert (started["research"]["difficulty"], started["research"]["tier"], started["research"]["model"]) == ("hard", "standard", "std-model")
    assert started["sql"]["task"] and started["sql"]["reason"] and SECRET not in json.dumps(started)
    order = [(c["type"], c["task"]) for mode, c in chunks if mode == "custom" and c["type"] in ("task_started", "spoke")]
    for event in started.values():
        assert order.index(("task_started", event["task"])) < order.index(("spoke", event["task"]))


async def test_chart_task_records_and_announces_its_trace(monkeypatch):
    _profile(monkeypatch, "economy")
    _plan(monkeypatch, Plan(tasks=[Task(id="t1", agent="sql", goal="a", difficulty="easy"),
                                   Task(id="t2", agent="chart", goal="c", difficulty="hard")]))
    _board(monkeypatch, then=Board())
    fell = {"from_model": "small-model", "to_model": "std-model", "category": "rate_limit"}
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=[], table=TABLE),
                           research=Fake("research", "Notes.", log=[]),
                           chart=Fake("chart", "Drawn.", log=[], tools=False, meta={"fallback": fell}))
    chunks = await _stream(assistant, _ask("chart AAPL"))
    started = next(e for e in _events(chunks, "task_started") if e["agent"] == "chart")
    assert (started["difficulty"], started["tier"], started["model"]) == ("hard", "standard", "std-model")
    spoke = next(e for e in _events(chunks, "spoke") if e["agent"] == "chart")
    assert (spoke["task"], spoke["difficulty"], spoke["tier"], spoke["model"]) == ("t2", "hard", "standard", "std-model")
    assert spoke["fallback"] == fell and spoke["duration_s"] >= 0 and "model_calls" in spoke["usage"]
    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    saved = transcript["results"]["t2"]
    assert (saved["difficulty"], saved["tier"], saved["model"], saved["profile"]) == ("hard", "standard", "std-model", "economy")
    assert saved["fallback"] == fell and "usage" in saved and SECRET not in json.dumps(transcript, default=str)


async def test_writeup_and_draw_use_standard(monkeypatch):
    _profile(monkeypatch, "economy")
    _plan(monkeypatch, Plan(tasks=[Task(id="t1", agent="sql", goal="a", difficulty="easy"),
                                   Task(id="t2", agent="research", goal="b", difficulty="easy"),
                                   Task(id="t3", agent="chart", goal="c", difficulty="easy")]))
    _board(monkeypatch, then=Board())
    log, model = [], per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap is 3T.", log=log),
                           research=Fake("research", "Cap is 2T.", log=log), chart=Fake("chart", "Drawn.", log=log))
    tiers = []
    real = factory.get_chat_model
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: (tiers.append(k.get("tier", "standard")), real(*a, **k))[1])
    config = {"configurable": {"thread_id": "t9"}}
    async for _ in assistant.astream(_ask("chart AAPL"), config, context=replace(TEAM_CONTEXT, tier="strong"),
                                     stream_mode=["custom"], subgraphs=True):
        pass
    assert tiers == ["standard"]  # the write-up
    assert [e["tier"] for e in log if e["event"] == "start" and e["agent"] == "chart"] == ["standard"]



class _Counting(Fake):
    """An in-process agent whose model calls reach the callbacks, as a real one's do."""

    async def ainvoke(self, input, config=None, **kwargs):
        for handler in config["callbacks"].handlers if hasattr(config["callbacks"], "handlers") else config["callbacks"]:
            if isinstance(handler, UsageCounter):
                for _ in range(2):
                    handler.count(AIMessage("x", usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}))
        return await super().ainvoke(input, config, **kwargs)


class _Server:
    async def ainvoke(self, input, config=None, **kwargs):
        usage = {"model_calls": 3, "input_tokens": 100, "output_tokens": 5, "cost": None}
        return {"messages": [*input["messages"], AIMessage("Cap is 3T.")], "findings": "x", "usage": usage}


async def test_task_usage_not_saved_twice(monkeypatch):
    _plan(monkeypatch, _rated("medium", "medium"))
    _board(monkeypatch, then=Board())
    monkeypatch.setattr(team, "load_settings", lambda user_id: None)
    remote = RemoteAgent(SQL, "http://sql:8000", service_token="t", timeout_s=5, remote=_Server())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=remote, research=_Counting("research", "Notes.", log=[]))
    request = UsageCounter()
    config = {**CONFIG, "callbacks": [request]}
    chunks = await _stream(assistant, _ask(), config)

    # remote task: 3 calls from the server; in-process task: 2 calls. Plus the write-up call (1, no tokens reported).
    assert (request.model_calls, request.input_tokens, request.output_tokens) == (6, 120, 7)
    store = InMemoryStore()
    await add_usage(store, "u1", request)
    assert (await get_usage(store, "u1"))["input_tokens"] == 120
    spoke = {e["agent"]: e["usage"] for e in _events(chunks, "spoke")}
    assert spoke["sql"]["model_calls"] == 3 and spoke["research"]["model_calls"] == 2
