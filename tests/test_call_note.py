"""The tail note: per-call text goes at the end of the model request, not into the system prompt or the saved state."""

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from app.agents import factory
from app.core.memory import AgentContext
from app.core.prompts import CALL_NOTE, LAST_CALL_NOTE
from tests.scripted import scripted


def _call(query, i):
    return AIMessage("", tool_calls=[{"name": "lookup", "args": {"query": query}, "id": f"c{i}"}])


async def _run(monkeypatch, tail, replies=None, **build):
    async def lookup(query: str) -> str:
        return "ok"

    tool = StructuredTool.from_function(coroutine=lookup, name="lookup", description="look something up")
    model = scripted(*(replies or [_call("a", 0), AIMessage("answer")]))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[tool], tail=tail, **build)
    result = await agent.ainvoke({"messages": [HumanMessage("go")]}, context=AgentContext(user_id="u"))
    return result, model


async def test_tail_is_the_last_message_of_every_call(monkeypatch):
    _, model = await _run(monkeypatch, lambda request: f"note {len(request.messages)}")
    for prompt, text in zip(model.prompts, ["note 1", "note 3"]):
        assert isinstance(prompt[-1], HumanMessage) and prompt[-1].name == CALL_NOTE
        assert prompt[-1].content == text


async def test_system_prompt_is_identical_across_calls(monkeypatch):
    _, model = await _run(monkeypatch, lambda request: f"note {len(request.messages)}")
    assert model.prompts[0][0].content == model.prompts[1][0].content


async def test_tail_is_not_saved(monkeypatch):
    result, _ = await _run(monkeypatch, lambda request: "note")
    assert all(m.name != CALL_NOTE for m in result["messages"])


async def test_empty_tail_adds_no_message(monkeypatch):
    _, model = await _run(monkeypatch, lambda request: "")
    assert model.prompts[0][-1].content == "go"


async def test_last_call_note_joins_the_tail(monkeypatch):
    _, model = await _run(monkeypatch, lambda request: "note", max_model_calls=2)
    notes = [m for m in model.prompts[-1] if m.name == CALL_NOTE]
    assert len(notes) == 1
    assert "note" in notes[0].content and LAST_CALL_NOTE.split("{")[0] in notes[0].content
