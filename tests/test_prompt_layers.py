"""The system prompt shows the user's profile facts apart from other memories."""

from langchain_core.messages import AIMessage, HumanMessage

from app.agents import factory
from app.core.memory import AgentContext
from app.core.prompts import MEMORIES_SECTION
from tests.scripted import scripted

NOTE = "A stored language preference never changes the reply language"


async def _system_prompt(monkeypatch, **context) -> str:
    model = scripted(AIMessage("ok"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[])
    await agent.ainvoke({"messages": [HumanMessage("go")]}, context=AgentContext(user_id="u", **context))
    return model.prompts[0][0].content


async def test_user_section_shows_profile_and_language_note(monkeypatch):
    prompt = await _system_prompt(monkeypatch, user="Ann (username: ann)", profile="- User prefers language Vietnamese")
    assert "YOU ARE TALKING TO: Ann (username: ann)" in prompt
    assert "- User prefers language Vietnamese" in prompt
    assert NOTE in prompt
    assert "stored language preference" not in MEMORIES_SECTION


async def test_no_profile_no_profile_lines(monkeypatch):
    prompt = await _system_prompt(monkeypatch, user="Ann (username: ann)", profile="")
    assert "- User" not in prompt
    assert "language preference" not in prompt


def test_user_facing_agents_start_with_soul():
    from app.agents.chart.prompts import CHART_SYSTEM
    from app.agents.docs.prompts import DOCS_SYSTEM
    from app.agents.sql.prompts import SQL_SYSTEM
    from app.core.prompts import SOUL
    from app.orchestration.prompts import ORCHESTRATOR_SYSTEM

    for system in (ORCHESTRATOR_SYSTEM, DOCS_SYSTEM, SQL_SYSTEM, CHART_SYSTEM):
        assert system.startswith(SOUL)


def test_researcher_has_no_soul():
    from app.agents.research.prompts import RESEARCHER_SYSTEM
    from app.core.prompts import SOUL

    assert SOUL not in RESEARCHER_SYSTEM


def test_soul_has_no_braces_and_ends_with_precedence():
    from app.core.prompts import SOUL

    assert "{" not in SOUL and "}" not in SOUL
    assert "precedence" in SOUL.rstrip().split(". ")[-1]


def test_orchestrator_no_longer_says_one_assistant():
    from app.orchestration.prompts import ORCHESTRATOR_SYSTEM

    assert "answer as one assistant" not in ORCHESTRATOR_SYSTEM
