import re
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.base import AgentSpec
from app.agents.registry import AGENTS, MESSAGE_ROUTES
from app.agents.docs import agent as docs_agent
from app.agents.research import agent as research_agent
from app.agents.sql import agent as sql_agent
from app.core.memory import AgentContext
from app.orchestration import router as router_module
from app.orchestration.orchestrator import MESSAGE_ROUTES as ALL_MESSAGE_ROUTES
from app.orchestration.router import ORCHESTRATOR, available_agents, obvious_route, previous_route


def test_previous_route_comes_from_last_named_ai_message():
    messages = [
        HumanMessage("hi"), AIMessage("hello", name="docs_agent"),
        HumanMessage("top stocks?"), AIMessage("", name="sql_agent"), ToolMessage("rows", tool_call_id="1"),
    ]
    assert previous_route(messages, MESSAGE_ROUTES) == "sql"
    assert previous_route([HumanMessage("first message")], MESSAGE_ROUTES) is None


def _runtime(**context):
    return SimpleNamespace(context=AgentContext(user_id="u1", **context))


async def test_forced_agent_is_used(monkeypatch):
    router = router_module.build_router()
    state = {"messages": [HumanMessage("anything")]}
    assert await router(state, _runtime(agent="docs")) == {"route": "docs", "direct": False}  # forced: no fallback


async def test_obvious_route_uses_keywords_tickers_and_file_names(monkeypatch):
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"AAPL", "MSFT"}))
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["handbook.pdf", "Q3 report.docx"])
    available = await available_agents("u1", AGENTS)
    assert obvious_route("What did AAPL close at?", available) == "sql"
    assert obvious_route("giá cổ phiếu Apple hôm nay?", available) == "sql"
    assert obvious_route("Summarize the handbook", available) == "docs"
    assert obvious_route("Tóm tắt tài liệu này", available) == "docs"
    # Both sides or neither: the orchestrator decides.
    assert obvious_route("Does the Q3 report mention MSFT?", available) is None
    assert obvious_route("how many vacation days do I get?", available) is None
    assert obvious_route("Is IT OK?", available) is None


async def test_obvious_message_goes_straight_to_its_agent(monkeypatch):
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["handbook.pdf"])
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"AAPL"}))
    router = router_module.build_router()
    state = {"messages": [HumanMessage("AAPL dividend yield?")]}
    assert await router(state, _runtime()) == {"route": "sql", "direct": True}


# A third agent plugs in through its spec alone.
WEB = AgentSpec(
    route="web",
    description="answers from a web search: news and anything not in the files or the database.",
    labels={"en": "Web agent", "vi": "Trợ lý web"},
    build=lambda: None,
    message_names=("web_agent",),
    hints=re.compile(r"\b(news|internet)\b", re.IGNORECASE),
)


async def test_new_agent_routes_by_its_hints_or_when_forced(monkeypatch):
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["handbook.pdf"])
    router = router_module.build_router((*AGENTS, WEB))
    assert await router({"messages": [HumanMessage("any news today?")]}, _runtime()) == {"route": "web", "direct": True}
    assert await router({"messages": [HumanMessage("hi")]}, _runtime(agent="web")) == {"route": "web", "direct": False}


async def test_research_hints_route_to_research(monkeypatch):
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"NVDA"}))
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["handbook.pdf"])
    available = await available_agents("u1", AGENTS)
    assert obvious_route("Research the EV battery market", available) == "research"
    assert obvious_route("latest news about OpenRouter?", available) == "research"
    assert obvious_route("Tìm hiểu về LangGraph giúp tôi", available) == "research"
    assert obvious_route("any arxiv papers on RAG evaluation?", available) == "research"
    # A ticker and a research word: both fit, so the orchestrator decides.
    assert obvious_route("latest news about NVDA", available) is None


async def test_everything_else_goes_to_the_orchestrator(monkeypatch):
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: [])
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"AAPL"}))
    router = router_module.build_router()
    for message in ("write a poem about autumn", "how many vacation days do I get?", "and last year?"):
        assert await router({"messages": [HumanMessage(message)]}, _runtime()) == {"route": ORCHESTRATOR, "direct": False}


def test_orchestrator_and_old_writer_answers_have_a_route():
    messages = [HumanMessage("research X"), AIMessage("## X ...", name="report_agent"), HumanMessage("shorter")]
    assert previous_route(messages, ALL_MESSAGE_ROUTES) == ORCHESTRATOR
    assert ALL_MESSAGE_ROUTES["orchestrator"] == ORCHESTRATOR


async def test_a_mention_of_another_agent_leaves_the_message_to_the_orchestrator(monkeypatch):
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"NVDA"}))
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: [
        "Eric-Jorgenson_The-Almanack-of-Naval-Ravikant_Final.pdf", "SWE_Resume_Tong.pdf"])
    available = await available_agents("u1", AGENTS)
    # A ticker or a stock word alone still goes straight to sql.
    assert obvious_route("What did NVDA close at?", available) == "sql"
    # ...but not when the message also points to the user's files.
    assert obvious_route("Does my resume fit a job at NVDA?", available) is None
    assert obvious_route("Does the Almanack say anything about market caps?", available) is None
    assert obvious_route("Đánh giá CV của tôi cho vị trí ở NVDA", available) is None
    # Generic file-name words ("final") don't count as a mention.
    assert obvious_route("Final NVDA close?", available) == "sql"
    assert router_module.mentioned_routes("What does Naval say about wealth?", available) == ("docs",)


async def test_chart_words_win_over_a_ticker_but_not_over_a_mention_of_files(monkeypatch):
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"AAPL", "MSFT", "NVDA"}))
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["SWE_Resume_Tong.pdf"])
    available = await available_agents("u1", AGENTS)
    assert obvious_route("Chart AAPL vs MSFT closing prices this year", available) == "chart"
    assert obvious_route("vẽ biểu đồ vốn hóa top 10 công ty công nghệ", available) == "chart"
    assert obvious_route("plot the stock prices of NVDA", available) == "chart"
    assert obvious_route("What is AAPL's dividend yield?", available) == "sql"  # a ticker alone stays with sql
    assert obvious_route("vẽ một con mèo", available) is None  # drawing a picture isn't a chart
    assert obvious_route("Chart my resume's skills against NVDA", available) is None  # files too: orchestrator
    assert obvious_route("plot that", available) == "chart"
