"""Tools carry a source tag; a request's `sources` hides the tools of other sources."""

from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage

from app.agents import factory
from app.agents.chart.tools import chart_data, chart_sql, chart_table
from app.agents.docs.tools import search_documents
from app.agents.research import agent as research_agent
from app.agents.sql.tools import run_sql
from app.core.memory import MEMORY_TOOLS, TOOL_SOURCE, AgentContext
from tests.scripted import scripted


def _tagged(name: str, source: str | None):
    @tool(name, description=name)
    def fn() -> str:
        return name

    if source:
        fn.metadata = {TOOL_SOURCE: source}
    return fn


async def _bound_tools(monkeypatch, sources) -> set[str]:
    model = scripted(AIMessage(content="ok"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(
        name="t", system_prompt="x",
        tools=[_tagged("db", "market_db"), _tagged("web", "web"), _tagged("plain", None)])
    await agent.ainvoke({"messages": [HumanMessage("hi")]}, context=AgentContext(user_id="u", sources=sources))
    return {t["function"]["name"] for t in model.tools[-1]}


async def test_tools_outside_sources_are_hidden(monkeypatch):
    names = await _bound_tools(monkeypatch, ("web",))
    assert "db" not in names and {"web", "plain"} <= names
    assert {t.name for t in MEMORY_TOOLS} <= names


async def test_no_sources_hides_nothing(monkeypatch):
    assert {"db", "web", "plain"} <= await _bound_tools(monkeypatch, ())


def test_every_data_tool_has_a_source():
    for t, source in [(run_sql, "market_db"), (chart_sql, "market_db"), (search_documents, "user_files")]:
        assert t.metadata[TOOL_SOURCE] == source
    for t in (chart_table, chart_data, *MEMORY_TOOLS):
        assert TOOL_SOURCE not in (t.metadata or {})
    tagged = {t.name: (t.metadata or {}).get(TOOL_SOURCE) for t in research_agent.tag_mcp(
        [_tagged("tavily_tavily_search", None), _tagged("knowledge_arxiv_search", None), _tagged("other_tool", None)])}
    assert tagged == {"tavily_tavily_search": "web", "knowledge_arxiv_search": "web", "other_tool": None}
