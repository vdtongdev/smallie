import re
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.base import AgentSpec
from app.agents.registry import AGENTS, MESSAGE_ROUTES
from app.agents.docs import agent as docs_agent
from app.agents.sql import agent as sql_agent
from app.core.memory import AgentContext
from app.orchestration import router as router_module
from app.orchestration.router import available_agents, obvious_route, parse_route, previous_route, transcript

ROUTES = ("docs", "sql")


def test_parse_route_takes_first_known_word():
    assert parse_route("sql", ROUTES, "docs") == "sql"
    assert parse_route(" Docs.\n", ROUTES, "sql") == "docs"
    assert parse_route("I'd say SQL, not docs", ROUTES, "docs") == "sql"


def test_parse_route_falls_back_to_default():
    assert parse_route("", ROUTES, "docs") == "docs"
    assert parse_route("database", ROUTES, "sql") == "sql"


def test_previous_route_comes_from_last_named_ai_message():
    messages = [
        HumanMessage("hi"), AIMessage("hello", name="docs_agent"),
        HumanMessage("top stocks?"), AIMessage("", name="sql_agent"), ToolMessage("rows", tool_call_id="1"),
    ]
    assert previous_route(messages, MESSAGE_ROUTES) == "sql"
    assert previous_route([HumanMessage("first message")], MESSAGE_ROUTES) is None


def test_transcript_skips_tool_messages_and_ends_with_latest_message():
    messages = [
        HumanMessage("What is AAPL's price?"),
        AIMessage("", name="sql_agent", tool_calls=[{"name": "run_sql", "args": {}, "id": "1"}]),
        ToolMessage("| close |", tool_call_id="1"),
        AIMessage("AAPL closed at 336.64.", name="sql_agent"),
        HumanMessage("and MSFT?"),
    ]
    text = transcript(messages, MESSAGE_ROUTES)
    assert "Assistant (sql): AAPL closed at 336.64." in text
    assert "| close |" not in text
    assert text.endswith("Latest message:\nand MSFT?")


def _runtime(**context):
    return SimpleNamespace(context=AgentContext(user_id="u1", **context))


class UnusedModel:
    async def ainvoke(self, messages):
        raise AssertionError("the router should not call the model here")


class RecordingModel:
    def __init__(self, reply: str):
        self.reply, self.prompts = reply, []

    async def ainvoke(self, messages):
        self.prompts.append(messages[0].content)
        return AIMessage(self.reply)


async def test_forced_agent_skips_the_model(monkeypatch):
    monkeypatch.setattr(router_module, "get_chat_model", lambda *a, **k: UnusedModel())
    router = router_module.build_router()
    state = {"messages": [HumanMessage("anything")]}
    assert await router(state, _runtime(agent="docs")) == {"route": "docs"}


async def test_no_documents_routes_to_sql_without_a_model_call(monkeypatch):
    monkeypatch.setattr(router_module, "get_chat_model", lambda *a, **k: UnusedModel())
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: [])
    router = router_module.build_router()
    state = {"messages": [HumanMessage("what does my contract say?")]}
    assert await router(state, _runtime()) == {"route": "sql"}


async def test_router_asks_the_model_when_documents_exist(monkeypatch):
    model = RecordingModel("docs")
    monkeypatch.setattr(router_module, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["handbook.pdf"])
    router = router_module.build_router()
    state = {"messages": [HumanMessage("how many vacation days do I get?")]}
    assert await router(state, _runtime()) == {"route": "docs"}
    assert "Reply with exactly one word: docs or sql." in model.prompts[0]
    assert "The user's uploaded documents: handbook.pdf" in model.prompts[0]


async def test_obvious_route_uses_keywords_tickers_and_file_names(monkeypatch):
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"AAPL", "MSFT"}))
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["handbook.pdf", "Q3 report.docx"])
    available = await available_agents("u1", AGENTS)
    assert obvious_route("What did AAPL close at?", available) == "sql"
    assert obvious_route("giá cổ phiếu Apple hôm nay?", available) == "sql"
    assert obvious_route("Summarize the handbook", available) == "docs"
    assert obvious_route("Tóm tắt tài liệu này", available) == "docs"
    # Both sides or neither: let the model decide.
    assert obvious_route("Does the Q3 report mention MSFT?", available) is None
    assert obvious_route("how many vacation days do I get?", available) is None
    assert obvious_route("Is IT OK?", available) is None


async def test_obvious_message_skips_the_model(monkeypatch):
    monkeypatch.setattr(router_module, "get_chat_model", lambda *a, **k: UnusedModel())
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["handbook.pdf"])
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"AAPL"}))
    router = router_module.build_router()
    state = {"messages": [HumanMessage("AAPL dividend yield?")]}
    assert await router(state, _runtime()) == {"route": "sql"}


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
    monkeypatch.setattr(router_module, "get_chat_model", lambda *a, **k: UnusedModel())
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: ["handbook.pdf"])
    router = router_module.build_router((*AGENTS, WEB))
    assert await router({"messages": [HumanMessage("any news today?")]}, _runtime()) == {"route": "web"}
    assert await router({"messages": [HumanMessage("hi")]}, _runtime(agent="web")) == {"route": "web"}


async def test_model_is_offered_only_available_agents(monkeypatch):
    model = RecordingModel("web")
    monkeypatch.setattr(router_module, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: [])
    router = router_module.build_router((*AGENTS, WEB))
    messages = [HumanMessage("hi"), AIMessage("hello", name="web_agent"), HumanMessage("what is smallie?")]
    assert await router({"messages": messages}, _runtime()) == {"route": "web"}
    prompt = model.prompts[0]
    assert "Reply with exactly one word: sql or web." in prompt
    assert f"- web: {WEB.description}" in prompt
    assert "previous turn (web)" in prompt
    assert "- docs:" not in prompt  # no documents, so not offered
