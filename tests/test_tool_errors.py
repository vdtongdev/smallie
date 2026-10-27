"""A tool that fails must not end the agent's run: the model sees the error and can retry."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from app.agents import factory
from app.core.memory import AgentContext
from tests.scripted import scripted


async def test_an_mcp_protocol_error_reaches_the_model_instead_of_ending_the_run(monkeypatch):
    """Tavily rejects a bad argument (time_range='1month') with a JSON-RPC error, which MCP raises as
    McpError from the tool call (not as an isError result)."""
    async def search(query: str) -> str:
        raise McpError(ErrorData(code=-32603, message="1 validation error for call[tavily_search] time_range"))

    web = StructuredTool.from_function(coroutine=search, name="tavily_tavily_search", description="web search")
    model = scripted(
        AIMessage("", tool_calls=[{"name": "tavily_tavily_search", "args": {"query": "nvidia"}, "id": "c1"}]),
        AIMessage("The search failed, so I could not list the events."),
    )
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[web])

    result = await agent.ainvoke({"messages": [HumanMessage("nvidia news")]}, context=AgentContext(user_id="u"))

    errors = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert errors and errors[0].status == "error" and "time_range" in errors[0].text
    assert result["messages"][-1].text.startswith("The search failed")
