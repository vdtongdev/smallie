"""In a 1-on-1 chat an agent speaks as "I", never "we" for itself; team mode keeps the team voice."""

import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents import factory
from app.core.memory import TEAM_MODE, AgentContext
from tests.scripted import scripted

WE = re.compile(r"\b(we|us|our|ours)\b", re.I)
SPEAK_AS_I = re.compile(r"""(say|speak|write)[^.\n]*["']I["']|first person singular""", re.I)


def _systems():
    from app.agents.sql.prompts import SQL_SYSTEM
    from app.orchestration.prompts import ORCHESTRATOR_SYSTEM

    return {"orchestrator": ORCHESTRATOR_SYSTEM, "specialist": SQL_SYSTEM}


async def _request_text(monkeypatch, system: str, **context) -> str:
    model = scripted(AIMessage("ok"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt=system, tools=[])
    await agent.ainvoke({"messages": [HumanMessage("hi")]}, context=AgentContext(user_id="u", **context))
    return "\n".join(str(m.content) for m in model.prompts[0])


@pytest.mark.parametrize("who", ["orchestrator", "specialist"])
async def test_solo_request_has_no_we_wording_and_says_i(monkeypatch, who):
    text = await _request_text(monkeypatch, _systems()[who])
    assert not WE.findall(text), WE.findall(text)
    assert SPEAK_AS_I.search(text)


def test_solo_bootstrap_has_no_we_wording():
    from app.orchestration.prompts import BOOTSTRAP_SECTION

    assert not WE.findall(BOOTSTRAP_SECTION)


@pytest.mark.parametrize("who", ["orchestrator", "specialist"])
async def test_team_request_keeps_team_voice(monkeypatch, who):
    from app.core.prompts import TEAM_VOICE

    text = await _request_text(monkeypatch, _systems()[who], agent=TEAM_MODE)
    assert TEAM_VOICE in text
    assert 'Say "we"' in text
