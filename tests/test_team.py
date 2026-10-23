import itertools
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable
from langgraph.types import Command
from pydantic import ValidationError

from app.agents.base import RouteInfo
from app.agents.registry import AGENTS, get
from app.core.memory import AgentContext
from app.orchestration import graph as graph_module
from app.orchestration import team
from app.orchestration.router import build_router
from app.orchestration.team import Verdict, allowed_critics, coerce, verdict_model
from tests.scripted import per_agent
from tests.test_orchestrator import _assistant

SQL, DOCS, RESEARCH = get("sql"), get("docs"), get("research")


def test_allowed_critics_skip_author_unavailable_and_reviewless():
    available = [(replace(s, review="" if s.route == "chart" else s.review), RouteInfo())
                 for s in AGENTS if s.route != "docs"]
    routes = [s.route for s in allowed_critics(available, author="sql")]
    assert routes == [s.route for s in AGENTS if s.route not in ("sql", "docs", "chart")]


def test_coerce_replaces_unknown_or_self_critic():
    allowed = [DOCS, RESEARCH]
    for critic in ("sql", "nobody", None):
        assert coerce(Verdict(action="critique", critic=critic), allowed).critic == "docs"
        assert coerce(Verdict(action="revise", critic=critic), allowed).critic == "docs"
    assert coerce(Verdict(action="critique", critic="research"), allowed).critic == "research"
    kept = coerce(Verdict(action="approve", critic="docs"), allowed)
    assert (kept.action, kept.critic) == ("approve", None)


def test_coerce_without_reviewers():
    assert coerce(Verdict(action="critique", critic="docs"), []).action == "approve"
    revised = coerce(Verdict(action="revise", critic="docs", issues=["x"]), [])
    assert (revised.action, revised.critic, revised.issues) == ("revise", None, ["x"])


def test_coerce_needs_human_without_question_approves():
    assert coerce(Verdict(action="needs_human", question=""), [DOCS]).action == "approve"
    asked = coerce(Verdict(action="needs_human", question="Which year?", critic="docs"), [DOCS])
    assert (asked.action, asked.question, asked.critic) == ("needs_human", "Which year?", None)


def test_verdict_model_limits_critic_to_allowed():
    model = verdict_model([DOCS])
    assert model(action="critique", critic="docs").critic == "docs"
    with pytest.raises(ValidationError):
        model(action="critique", critic="sql")
    assert verdict_model([]) is Verdict


def test_issues_capped_at_five():
    assert len(Verdict(action="revise", issues=[str(i) for i in range(9)]).issues) == 5


async def test_ask_judge_calls_the_model_once_and_coerces(monkeypatch):
    calls, schemas = [], []

    async def ainvoke(messages, config):
        calls.append((messages, config))
        return Verdict(action="critique", critic="sql")  # the author: must be replaced

    monkeypatch.setattr(team, "get_chat_model", lambda model=None: SimpleNamespace(
        with_structured_output=lambda schema, method: schemas.append((schema, method)) or SimpleNamespace(ainvoke=ainvoke)))
    state = {"draft": "D", "critiques": [{"critic": "docs", "text": "T", "turn": 1}], "turns": 1}
    config = {"callbacks": []}
    verdict = await team.ask_judge([HumanMessage("question?")], state, [DOCS], config)
    assert (verdict.action, verdict.critic) == ("critique", "docs")
    assert len(calls) == 1 and calls[0][1] is config and schemas[0][1] == "function_calling"
    human = calls[0][0][1].content
    assert "question?" in human and "D" in human and "T" in human


# --- the team subgraph in the real assistant graph, with scripted models and a scripted judge ---

QUESTION = "What is the AAPL market cap?"  # obvious for sql
TEAM_CONTEXT = AgentContext(user_id="u1", agent="team")
CONFIG = {"configurable": {"thread_id": "t1"}}


def _judge(monkeypatch, *verdicts, then=None):
    """Scripted judge: the verdicts in order, then `then` forever; the real coerce still applies."""
    queue = itertools.chain(verdicts, itertools.repeat(then) if then else ())

    async def fake(messages, state, allowed, config):
        return coerce(next(queue), allowed)

    monkeypatch.setattr(team, "ask_judge", fake)


async def _stream(assistant, first, config=CONFIG):
    return [(mode, chunk) async for _, mode, chunk in assistant.astream(
        first, config, context=TEAM_CONTEXT, stream_mode=["messages", "updates", "custom"], subgraphs=True)]


def _ask(message=QUESTION):
    return {"messages": [HumanMessage(message)]}


def _events(chunks, kind):
    return [c for mode, c in chunks if mode == "custom" and c["type"] == kind]


def _phases(chunks):
    return [e["phase"] for e in _events(chunks, "team")]


async def _thread(assistant, config=CONFIG):
    return (await assistant.aget_state(config)).values["messages"]


async def test_router_sends_team_mode_to_team():
    router = build_router()
    state = {"messages": [HumanMessage(QUESTION)]}
    assert await router(state, SimpleNamespace(context=TEAM_CONTEXT)) == {"route": "team", "direct": False}


async def test_approve_at_first_judge_skips_critique(monkeypatch):
    _judge(monkeypatch, Verdict(action="approve"))
    assistant = _assistant(monkeypatch, per_agent(sql=[AIMessage("Cap is 3T.")], orchestrator=[AIMessage("Final 3T.")]))
    chunks = await _stream(assistant, _ask())

    assert _phases(chunks) == ["delegate", "synthesize"]
    assert [e["action"] for e in _events(chunks, "verdict")] == ["approve"]
    messages = await _thread(assistant)
    assert [type(m) for m in messages] == [HumanMessage, AIMessage]
    assert messages[1].text == "Final 3T." and messages[1].additional_kwargs["team"]["critiques"] == []


async def test_one_round_then_approve(monkeypatch):
    _judge(monkeypatch, Verdict(action="critique", critic="research"),
           Verdict(action="revise", critic="research", issues=["wrong year"]), Verdict(action="approve"))
    model = per_agent(sql=[AIMessage("draft"), AIMessage("better draft")],
                      research=[AIMessage("notes 1"), AIMessage("notes 2")], orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model)
    chunks = await _stream(assistant, _ask())

    assert _phases(chunks) == ["delegate", "critique", "refine", "critique", "synthesize"]
    assert [e["agent"] for e in _events(chunks, "team")][1] == "research"
    kwargs = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert (kwargs["turns"], kwargs["author"], kwargs["draft"]) == (3, "sql", "better draft")
    assert [c["text"] for c in kwargs["critiques"]] == ["notes 1", "notes 2"]


async def test_cap_forces_synthesis(monkeypatch):
    _judge(monkeypatch, Verdict(action="critique", critic="research"),
           then=Verdict(action="revise", critic="research", issues=["still wrong"]))
    model = per_agent(sql=[AIMessage("d0"), AIMessage("d1"), AIMessage("d2")],
                      research=[AIMessage("n1"), AIMessage("n2")], orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model)
    chunks = await _stream(assistant, _ask())

    assert _phases(chunks) == ["delegate", "critique", "refine", "critique", "refine", "synthesize"]
    assert _events(chunks, "verdict")[-1]["forced"] is True
    team_state = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert team_state["turns"] == 4 and team_state["verdicts"][-1]["forced"] is True
    assert "not reviewed" in model.prompts[-1][-1].content  # the write-up is told what stayed open


async def test_needs_human_pauses_and_resume_continues(monkeypatch):
    _judge(monkeypatch, Verdict(action="needs_human", question="Which year?"), Verdict(action="approve"))
    assistant = _assistant(monkeypatch, per_agent(sql=[AIMessage("draft")], orchestrator=[AIMessage("Final 2024.")]))
    first = await _stream(assistant, _ask())

    assert _events(first, "needs_human") == [{"type": "needs_human", "question": "Which year?"}]
    assert (await assistant.aget_state(CONFIG)).interrupts
    # The parent's thread only gets the subgraph's messages when it finishes; while paused they are
    # in the subgraph's own snapshot.
    paused = await assistant.aget_state(CONFIG, subgraphs=True)
    assert [m.text for m in paused.tasks[0].state.values["messages"]] == [QUESTION, "Which year?"]

    second = await _stream(assistant, Command(resume="2024"))
    assert _phases(second) == ["synthesize"]  # delegate does not run again
    assert not (await assistant.aget_state(CONFIG)).interrupts
    messages = await _thread(assistant)
    assert [(type(m), m.text) for m in messages] == [
        (HumanMessage, QUESTION), (AIMessage, "Which year?"), (HumanMessage, "2024"), (AIMessage, "Final 2024.")]


async def test_no_reviewers_approves_without_critique(monkeypatch):
    monkeypatch.setattr(graph_module, "AGENTS", [replace(spec, review="") for spec in AGENTS])
    _judge(monkeypatch, Verdict(action="critique", critic="research"))
    assistant = _assistant(monkeypatch, per_agent(sql=[AIMessage("draft")], orchestrator=[AIMessage("Final.")]))
    chunks = await _stream(assistant, _ask())

    assert _phases(chunks) == ["delegate", "synthesize"]
    assert [e["action"] for e in _events(chunks, "verdict")] == ["approve"]


class _Failing(Runnable):
    """A research agent that always fails."""

    def invoke(self, input, config=None, **kwargs):
        raise RuntimeError("boom")

    async def ainvoke(self, input, config=None, **kwargs):
        raise RuntimeError("boom")


async def test_failed_critic_still_synthesizes(monkeypatch):
    _judge(monkeypatch, Verdict(action="critique", critic="research"))
    model = per_agent(sql=[AIMessage("draft")], orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model, research=_Failing())
    chunks = await _stream(assistant, _ask())

    assert _phases(chunks) == ["delegate", "critique", "synthesize"]
    last = (await _thread(assistant))[-1]
    assert last.text == "Final."
    assert last.additional_kwargs["team"]["critiques"][0]["text"] == "failed: RuntimeError"
    assert _events(chunks, "verdict")[-1]["forced"] is True


async def test_second_turn_starts_a_fresh_debate(monkeypatch):
    _judge(monkeypatch, Verdict(action="critique", critic="research"), Verdict(action="approve"),
           Verdict(action="critique", critic="research"), Verdict(action="approve"))
    model = per_agent(sql=[AIMessage("d1"), AIMessage("d2")], research=[AIMessage("n1"), AIMessage("n2")],
                      orchestrator=[AIMessage("F1"), AIMessage("F2")])
    assistant = _assistant(monkeypatch, model)
    await _stream(assistant, _ask())
    await _stream(assistant, _ask("And the AAPL market cap in euro?"))

    team_state = (await _thread(assistant))[-1].additional_kwargs["team"]
    assert [c["text"] for c in team_state["critiques"]] == ["n2"]
    assert (team_state["draft"], team_state["turns"], len(team_state["verdicts"])) == ("d2", 1, 2)


async def test_only_the_answer_joins_the_thread(monkeypatch):
    _judge(monkeypatch, Verdict(action="critique", critic="research"), Verdict(action="approve"))
    model = per_agent(sql=[AIMessage("secret draft")], research=[AIMessage("secret critique")],
                      orchestrator=[AIMessage("Final.")])
    assistant = _assistant(monkeypatch, model)
    await _stream(assistant, _ask())

    messages = await _thread(assistant)
    assert [type(m) for m in messages] == [HumanMessage, AIMessage]
    assert not any("secret" in m.text for m in messages)


async def _paused(monkeypatch):
    _judge(monkeypatch, Verdict(action="needs_human", question="Which year?"), Verdict(action="approve"))
    assistant = _assistant(monkeypatch, per_agent(sql=[AIMessage("draft")], orchestrator=[AIMessage("Final 2024.")]))
    await _stream(assistant, _ask())
    return assistant


async def test_chat_input_resumes_a_paused_team_turn(monkeypatch):
    from app.api.routes_chat import _chat_input

    assistant = await _paused(monkeypatch)
    assert await _chat_input(assistant, CONFIG, "team", "2024") == Command(resume="2024")
    # Only team mode resumes; another kind sends a message.
    assert await _chat_input(assistant, CONFIG, "auto", "2024") == {"messages": [{"role": "user", "content": "2024"}]}


async def test_chat_input_is_a_message_otherwise(monkeypatch):
    from app.api.routes_chat import _chat_input

    assistant = _assistant(monkeypatch, per_agent(orchestrator=[AIMessage("Hi.")]))
    assert await _chat_input(assistant, CONFIG, "team", "hello") == {"messages": [{"role": "user", "content": "hello"}]}
