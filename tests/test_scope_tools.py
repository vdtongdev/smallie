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


async def test_a_call_to_a_hidden_tool_is_not_run(monkeypatch):
    """A weak model calls tools it was not offered (the prompt names them); the call must not run."""
    ran = []

    @tool("db", description="db")
    def db() -> str:
        ran.append("db")
        return "ROWS"

    db.metadata = {TOOL_SOURCE: "market_db"}
    model = scripted(AIMessage("", tool_calls=[{"name": "db", "args": {}, "id": "c1"}]), AIMessage("done"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[db, _tagged("web", "web")])
    result = await agent.ainvoke({"messages": [HumanMessage("hi")]}, context=AgentContext(user_id="u", sources=("web",)))
    assert ran == []
    assert "ROWS" not in "".join(m.text for m in result["messages"])


def test_every_data_tool_has_a_source():
    for t, source in [(run_sql, "market_db"), (chart_sql, "market_db"), (search_documents, "user_files")]:
        assert t.metadata[TOOL_SOURCE] == source
    for t in (chart_table, chart_data, *MEMORY_TOOLS):
        assert TOOL_SOURCE not in (t.metadata or {})
    tagged = {t.name: (t.metadata or {}).get(TOOL_SOURCE) for t in research_agent.tag_mcp(
        [_tagged("tavily_tavily_search", None), _tagged("knowledge_arxiv_search", None), _tagged("other_tool", None)])}
    assert tagged == {"tavily_tavily_search": "web", "knowledge_arxiv_search": "web", "other_tool": None}


def test_external_db_tools_are_tagged():
    tagged = research_agent.tag_mcp([_tagged("external_db_query", None), _tagged("external_db_schema", None)])
    assert all(t.metadata[TOOL_SOURCE] == "external_db" for t in tagged)
    assert research_agent.SPEC.sources == frozenset({"web", "external_db"})


def test_memories_section_keeps_message_language():
    from app.core.prompts import MEMORIES_SECTION

    text = MEMORIES_SECTION.lower()
    assert "language" in text and "latest message" in text


class _Recording(type(scripted())):
    """A scripted model that keeps the `tool_choice` of each model call (None = not given)."""

    choices: list = []

    def bind_tools(self, tools, **kwargs):
        self.choices.append(kwargs.get("tool_choice"))
        return super().bind_tools(tools, **kwargs)


def _forced(choice) -> bool:
    return choice not in (None, "auto", "none")


async def _choices(monkeypatch, *, agent, sources, tools):
    """The tool choice of each model call: the model calls the first tool, then answers."""
    model = _Recording(replies=iter([AIMessage("", tool_calls=[{"name": tools[0].name, "args": {}, "id": "c1"}]),
                                     AIMessage("done")]), prompts=[], tools=[], choices=[])
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    built = factory.build_agent(name="t", system_prompt="x", tools=tools)
    await built.ainvoke({"messages": [HumanMessage("hi")]}, context=AgentContext(user_id="u", agent=agent, sources=sources))
    return model.choices


async def test_team_speaker_first_call_must_use_a_source_tool(monkeypatch):
    """Goal 2 + Task 6 fix plan: in a team request with a source tool in scope the first model call requires a
    tool, so a speaker cannot answer without looking in its source; later calls in the turn are free."""
    from app.core.memory import TEAM_MODE

    tools = [_tagged("db", "market_db"), _tagged("plain", None)]
    first, second = await _choices(monkeypatch, agent=TEAM_MODE, sources=("market_db",), tools=tools)
    assert _forced(first) and not _forced(second)


async def test_first_call_is_not_forced_in_normal_mode(monkeypatch):
    tools = [_tagged("db", "market_db"), _tagged("plain", None)]
    choices = await _choices(monkeypatch, agent="", sources=("market_db",), tools=tools)
    assert not any(_forced(c) for c in choices)


async def test_first_call_is_not_forced_without_a_source_tool_in_scope(monkeypatch):
    """The drawer has no data source (sources=NO_SOURCES): it must stay free to draw nothing."""
    from app.core.memory import TEAM_MODE
    from app.orchestration.team import NO_SOURCES

    tools = [_tagged("plain", None), _tagged("db", "market_db")]
    choices = await _choices(monkeypatch, agent=TEAM_MODE, sources=NO_SOURCES, tools=tools)
    assert not any(_forced(c) for c in choices)
