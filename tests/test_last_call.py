"""An agent's last allowed model call writes the answer: no tools, a write-up note, tool calls dropped."""

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from app.agents import factory
from app.core.memory import AgentContext
from app.core.prompts import LAST_CALL_NOTE
from tests.scripted import scripted


def _call(query, i):
    return AIMessage("", tool_calls=[{"name": "lookup", "args": {"query": query}, "id": f"c{i}"}])


async def _run(monkeypatch, replies, **build):
    ran = []

    async def lookup(query: str) -> str:
        ran.append(query)
        return "ok"

    tool = StructuredTool.from_function(coroutine=lookup, name="lookup", description="look something up")
    model = scripted(*replies)
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[tool], **build)
    result = await agent.ainvoke({"messages": [HumanMessage("go")]}, context=AgentContext(user_id="u"))
    return ran, result, model


async def test_last_call_gets_the_write_up_note(monkeypatch):
    _, _, model = await _run(monkeypatch, [_call("a", 0), _call("b", 1), AIMessage("answer")], max_model_calls=3)
    start = LAST_CALL_NOTE.split("{")[0]
    assert start in model.prompts[2][-1].content
    assert all(start not in str(p[-1].content) for p in model.prompts[:2])


async def test_tool_calls_on_the_last_call_are_dropped(monkeypatch):
    ran, result, _ = await _run(
        monkeypatch,
        [_call("a", 0), _call("b", 1), AIMessage("answer", tool_calls=[{"name": "lookup", "args": {"query": "c"}, "id": "c2"}])],
        max_model_calls=3,
    )
    assert ran == ["a", "b"]
    assert result["messages"][-1].text == "answer" and not result["messages"][-1].tool_calls


async def test_orchestrator_keeps_its_last_call_tools(monkeypatch):
    ran, _, _ = await _run(monkeypatch, [_call("a", 0), AIMessage("done")], max_model_calls=1, last_call_writes=False)
    assert ran == ["a"]
