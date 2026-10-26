import asyncio
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
from app.orchestration import graph as graph_module
from app.orchestration import team
from app.orchestration.router import build_router
from app.orchestration.team import Board, Conflict, Difference, Side, clean, participants
from tests.scripted import per_agent
from tests.test_orchestrator import _assistant

SQL, RESEARCH = get("sql"), get("research")

QUESTION = "What is the AAPL market cap?"  # sql; research joins
THREE = "What is the AAPL market cap and what does handbook.pdf say?"  # docs + sql; research joins
TEAM_CONTEXT = AgentContext(user_id="u1", agent="team")
CONFIG = {"configurable": {"thread_id": "t1"}}


# --- who takes part ---

def _available(*, docs=True):
    infos = {"sql": RouteInfo(matches=lambda m: "AAPL" in m), "docs": RouteInfo(matches=lambda m: "handbook.pdf" in m)}
    return [(spec, infos.get(spec.route, RouteInfo())) for spec in AGENTS if docs or spec.route != "docs"]


def _routes(message, **kw):
    return [s.route for s in participants(_available(**kw), message)]


def test_pick_mentioned_with_others_and_overrides():
    assert _routes(QUESTION) == ["sql", "research"]  # research joins whoever else takes part
    assert _routes(THREE) == ["docs", "sql", "research"]  # registry order
    assert _routes("chart AAPL") == ["sql", "research", "chart"]  # chart draws from sql, so sql joins too
    assert _routes("what does handbook.pdf say", docs=False) == []  # an unavailable agent is not picked


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
    await team.ask_board([HumanMessage("question?")], answers, config)
    assert len(calls) == 1 and calls[0][1] is config and schemas == [(Board, "function_calling")]
    human = calls[0][0][1].content
    assert "question?" in human and "SQL SAYS 3T" in human and "DOCS SAYS 2T" in human


@pytest.mark.parametrize("judge, orchestrator, expected", [
    ("judge/model", "orch/model", "judge/model"),
    ("", "orch/model", "orch/model"),
    ("", "", None),
])
async def test_board_uses_the_judge_model(monkeypatch, judge, orchestrator, expected):
    asked = []

    async def ainvoke(messages, config):
        return Board()

    def fake_model(model=None, **kwargs):
        asked.append(model)
        return SimpleNamespace(with_structured_output=lambda schema, method: SimpleNamespace(ainvoke=ainvoke))

    monkeypatch.setattr(team, "get_chat_model", fake_model)
    monkeypatch.setattr(team, "get_settings", lambda: SimpleNamespace(
        openrouter_judge_model=judge, openrouter_orchestrator_model=orchestrator))
    await team.ask_board([HumanMessage("question?")], [{"round": 1, "agent": "sql", "text": "x"}], {"callbacks": []})
    assert asked == [expected]


# --- the team subgraph in the real assistant graph, with scripted models and a scripted board ---

class Fake(Runnable):
    """An agent that logs when it runs and answers with scripted texts (the last one repeats)."""

    def __init__(self, name, *texts, log, delay=0.0, tools=False, chart=None, table=None):
        self.name, self.texts, self.log, self.delay, self.tools, self.chart = name, list(texts), log, delay, tools, chart
        self.table = table
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        raise NotImplementedError

    async def ainvoke(self, input, config=None, **kwargs):
        loop = asyncio.get_running_loop()
        self.log.append({"event": "start", "agent": self.name, "at": loop.time(), "last": input["messages"][-1],
                         "sources": kwargs["context"].sources})
        await asyncio.sleep(self.delay)
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        out = [AIMessage(text)]
        if self.tools:
            out = [AIMessage("", tool_calls=[{"name": "run_sql", "args": {"query": "SELECT 1"}, "id": "k1"}]),
                   ToolMessage("1 row", tool_call_id="k1", name="run_sql"), *out]
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

    async def fake(messages, answers, config):
        return next(queue)

    monkeypatch.setattr(team, "ask_board", fake)


async def _stream(assistant, first, config=CONFIG):
    return [(mode, chunk) async for _, mode, chunk in assistant.astream(
        first, config, context=TEAM_CONTEXT, stream_mode=["messages", "updates", "custom"], subgraphs=True)]


def _ask(message=QUESTION):
    return {"messages": [HumanMessage(message)]}


def _events(chunks, kind):
    return [c for mode, c in chunks if mode == "custom" and c["type"] == kind]


async def _thread(assistant, config=CONFIG):
    return (await assistant.aget_state(config)).values["messages"]


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
    return model, _assistant(monkeypatch, model, **agents), agents


def _research_does_not_join(monkeypatch):
    monkeypatch.setattr(graph_module, "AGENTS", tuple(replace(s, joins="mentioned") for s in AGENTS))


async def test_router_sends_team_mode_to_team():
    router = build_router()
    state = {"messages": [HumanMessage(QUESTION)]}
    assert await router(state, SimpleNamespace(context=TEAM_CONTEXT)) == {"route": "team", "direct": False}


async def test_nobody_mentioned_orchestrator_answers_alone(monkeypatch):
    model = per_agent(orchestrator=[AIMessage("Roses are red.")])
    assistant = _assistant(monkeypatch, model)
    chunks = await _stream(assistant, _ask("write a short poem"))

    assert not _events(chunks, "round") and not _events(chunks, "board")
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
    chunks = await _stream(assistant, _ask())

    reply = (await _thread(assistant))[-1]
    assert (reply.name, reply.text) == ("sql_agent", "Cap is 3T.")
    assert reply.additional_kwargs["team"]["participants"] == ["sql"]
    assert not model.prompts and not _events(chunks, "synthesize")


async def test_agreement_writes_up_once(monkeypatch):
    log = []
    _board(monkeypatch, Board(agreements=["both say 3T"]))
    model, assistant = _two(monkeypatch, log)
    chunks = await _stream(assistant, _ask())

    assert [e["round"] for e in _events(chunks, "round")] == [1] and len(_events(chunks, "board")) == 1
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

    rounds = _events(chunks, "round")
    assert [(e["round"], e["kind"]) for e in rounds] == [(1, "answer"), (2, "conflict")]
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

        assert [e["round"] for e in _events(chunks, "round")] == [1]
        assert "CAP-CLAIM" in _writeups(model)[0]
        assert (await _thread(assistant))[-1].additional_kwargs["team"]["forced"] is False


async def test_cap_forces_write_up_after_the_conflict_round_limit(monkeypatch):
    _board(monkeypatch, then=_conflict(["sql"]))
    model, assistant, _ = _three(monkeypatch, [])
    chunks = await _stream(assistant, _ask(THREE))

    limit = get_settings().team_max_rounds  # conflict rounds after round 1
    assert [e["round"] for e in _events(chunks, "round")] == list(range(1, limit + 2))
    assert all(e["max_rounds"] == limit + 1 for e in _events(chunks, "round"))
    assert _events(chunks, "board")[-1]["forced"] is True
    assert (await _thread(assistant))[-1].additional_kwargs["team"]["forced"] is True
    assert len(_events(chunks, "synthesize")) == 1 and len(_writeups(model)) == 1


async def test_failed_speaker_forces_write_up(monkeypatch):
    log = []
    _board(monkeypatch, Board())
    model = per_agent(orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap 3T.", log=log), research=_Failing())
    await _stream(assistant, _ask())

    last = (await _thread(assistant))[-1]
    assert last.text == "Final." and last.additional_kwargs["team"]["forced"] is True
    answers = last.additional_kwargs["team"]["rounds"][0]["answers"]
    assert {a["agent"]: a["text"] for a in answers}["research"] == "failed: RuntimeError"


async def test_failed_board_forces_write_up(monkeypatch):
    async def broken(messages, answers, config):
        raise RuntimeError("boom")

    monkeypatch.setattr(team, "ask_board", broken)
    model, assistant = _two(monkeypatch, [])
    chunks = await _stream(assistant, _ask())

    last = (await _thread(assistant))[-1]
    assert last.text == "Final." and last.additional_kwargs["team"]["forced"] is True
    assert _events(chunks, "board")[-1]["forced"] is True
    assert "Cap is 3T." in _writeups(model)[0]


async def test_failed_board_rate_limit_still_propagates(monkeypatch):
    import httpx
    import openai

    async def limited(messages, answers, config):
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
    await _stream(assistant, _ask())
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

    async def fake(messages, answers, config):
        boards.append([a["agent"] for a in answers])
        return Board()

    monkeypatch.setattr(team, "ask_board", fake)
    model = per_agent(orchestrator=[AIMessage("Final.")])
    chart = Fake("chart", "drawn", log=log)
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "Cap.", log=log), research=Fake("research", "n", log=log),
                           chart=chart)
    chunks = await _stream(assistant, _ask("chart AAPL"))

    assert [e["agents"] for e in _events(chunks, "round")] == [["sql", "research"]]
    assert boards == [["sql", "research"]] and chart.calls == 1  # the draw only
    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert transcript["drawers"] == ["chart"] and transcript["participants"] == ["sql", "research"]


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
    assert not _events(second, "round") and sql.calls == 1  # the participants do not run again
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
    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Final.")]), **{**agents, **kw})
    await _stream(assistant, _ask("plot a chart of prices"))
    return assistant, agents, await _stream(assistant, Command(resume=reply))


async def test_reply_to_question_naming_a_new_agent_brings_it_in(monkeypatch):
    assistant, agents, second = await _chart_asks(monkeypatch, "check handbook.pdf")

    assert [(e["round"], e["kind"], e["agents"]) for e in _events(second, "round")] == [(2, "answer", ["docs"])]
    assert agents["docs"].calls == 1 and agents["sql"].calls == 1
    team_record = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert team_record["participants"] == ["sql", "docs"] and team_record["drawers"] == ["chart"]


async def test_reply_without_new_agent_goes_straight_to_judge(monkeypatch):
    assistant, agents, second = await _chart_asks(monkeypatch, "2024 please")

    assert not _events(second, "round") and agents["sql"].calls == 1 and agents["chart"].calls == 1  # the draw only
    assert all(a["agent"] != "chart" for r in (await _thread(assistant))[-1].additional_kwargs["team"]["rounds"] for a in r["answers"])


async def test_reply_naming_an_agent_already_taking_part_does_not_run_it_again(monkeypatch):
    assistant, agents, second = await _chart_asks(monkeypatch, "make it a bar chart")

    assert not _events(second, "round") and agents["chart"].calls == 1  # the draw only
    assert all(a["agent"] != "chart" for r in (await _thread(assistant))[-1].additional_kwargs["team"]["rounds"] for a in r["answers"])
    assert (await _thread(assistant))[-1].additional_kwargs["team"]["drawers"] == ["chart"]


async def test_reply_never_brings_in_an_unavailable_agent(monkeypatch):
    docs = Fake("docs", "from file", log=[])
    assistant, agents, second = await _chart_asks(monkeypatch, "check the uploaded file", documents=(), docs=docs)

    assert not _events(second, "round") and docs.calls == 0


async def test_second_turn_starts_fresh(monkeypatch):
    log = []
    _board(monkeypatch, then=Board())
    model = per_agent(orchestrator=[AIMessage("F1"), AIMessage("F2")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "d1", "d2", log=log),
                           research=Fake("research", "n1", "n2", log=log))
    await _stream(assistant, _ask())
    await _stream(assistant, _ask("And the AAPL market cap in euro?"))

    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert len(transcript["rounds"]) == 1 and len(transcript["boards"]) == 1
    assert {a["agent"]: a["text"] for a in transcript["rounds"][0]["answers"]} == {"sql": "d2", "research": "n2"}


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
    assert transcript["participants"] == ["sql", "research"] and transcript["forced"] is False
    (first,) = transcript["rounds"]
    assert (first["round"], first["kind"]) == (1, "answer")
    sql = next(a for a in first["answers"] if a["agent"] == "sql")
    assert sql["text"] == "Cap is 3T."
    assert sql["tools"] == [{"name": "run_sql", "args": {"query": "SELECT 1"}, "result": "1 row"}]
    assert transcript["boards"][0]["agreements"] == ["same figure"]
    assert transcript["boards"][0]["differences"][0]["explanation"] == "as-of date"


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
    prompts.TEAM_MESSAGE.format(answers="", differences="none", conflicts="none", language="English", drawing="")


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
    research = next(a for a in transcript["rounds"][0]["answers"] if a["agent"] == "research")
    assert research["tools"] == [{"name": "tavily_tavily_search", "args": {"query": "AAPL cap"}, "result": "Apple cap 3T"}]


async def test_spoke_event_carries_the_final_entry(monkeypatch):
    _board(monkeypatch, Board())
    _, assistant = _two(monkeypatch, [], tools=True)
    chunks = await _stream(assistant, _ask())

    transcript = (await _thread(assistant))[-1].additional_kwargs["team"]
    spoke = _events(chunks, "spoke")
    assert {e["agent"] for e in spoke} == {"sql", "research"}
    for event in spoke:
        entry = next(a for a in transcript["rounds"][0]["answers"] if a["agent"] == event["agent"])
        assert event == {"type": "spoke", "round": 1, **entry}
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
    drawn = final.additional_kwargs["team"]["drawn"]
    assert [d["agent"] for d in drawn] == ["chart"] and drawn[0]["tools"][0]["name"] == "chart_table"
    assert {e["agent"] for e in _events(chunks, "speaker")} >= {"chart"}


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
    chunks = await _stream(assistant, _ask("chart AAPL"))

    final = (await _thread(assistant))[-1]
    assert (final.name, final.text) == ("sql_agent", "Cap is 3T.")
    assert final.additional_kwargs["charts"][0]["table_id"] == "t1" and _events(chunks, "charts")
    assert not _events(chunks, "synthesize")


async def test_answer_without_data_draws_nothing(monkeypatch):
    _research_does_not_join(monkeypatch)
    _board(monkeypatch, Board())
    model = per_agent(chart=[AIMessage("The answer holds no data to chart.")])
    assistant = _assistant(monkeypatch, model, sql=Fake("sql", "No rows.", log=[]))
    chunks = await _stream(assistant, _ask("chart AAPL"))

    final = (await _thread(assistant))[-1]
    assert final.text == "No rows." and "charts" not in final.additional_kwargs and not _events(chunks, "charts")


def test_draw_message_formats_with_language():
    from app.orchestration.prompts import DRAW_MESSAGE

    text = DRAW_MESSAGE.format(language="English")
    assert "English" in text and "{" not in text and "}" not in text
