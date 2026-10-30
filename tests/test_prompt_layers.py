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
