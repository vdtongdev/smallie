from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from app.agents.base import RouteInfo
from app.agents.registry import AGENTS, get
from app.orchestration import team
from app.orchestration.team import Verdict, allowed_critics, coerce, verdict_model

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
