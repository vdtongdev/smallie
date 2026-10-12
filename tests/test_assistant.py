from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import assistant
from app.assistant import parse_route, previous_route, transcript
from app.memory import AgentContext


def test_parse_route_takes_first_known_word():
    assert parse_route("sql", "docs") == "sql"
    assert parse_route(" Docs.\n", "sql") == "docs"
    assert parse_route("I'd say SQL, not docs", "docs") == "sql"


def test_parse_route_falls_back_to_default():
    assert parse_route("", "docs") == "docs"
    assert parse_route("database", "sql") == "sql"


def test_previous_route_comes_from_last_named_ai_message():
    messages = [
        HumanMessage("hi"), AIMessage("hello", name="docs_agent"),
        HumanMessage("top stocks?"), AIMessage("", name="sql_agent"), ToolMessage("rows", tool_call_id="1"),
    ]
    assert previous_route(messages) == "sql"
    assert previous_route([HumanMessage("first message")]) is None


def test_transcript_skips_tool_messages_and_ends_with_latest_message():
    messages = [
        HumanMessage("What is AAPL's price?"),
        AIMessage("", name="sql_agent", tool_calls=[{"name": "run_sql", "args": {}, "id": "1"}]),
        ToolMessage("| close |", tool_call_id="1"),
        AIMessage("AAPL closed at 336.64.", name="sql_agent"),
        HumanMessage("and MSFT?"),
    ]
    text = transcript(messages)
    assert "Assistant (sql): AAPL closed at 336.64." in text
    assert "| close |" not in text
    assert text.endswith("Latest message:\nand MSFT?")


def _runtime(**context):
    return SimpleNamespace(context=AgentContext(user_id="u1", **context))


class UnusedModel:
    def with_fallbacks(self, fallbacks):
        return self

    async def ainvoke(self, messages):
        raise AssertionError("the router should not call the model here")


async def test_forced_agent_skips_the_model(monkeypatch):
    monkeypatch.setattr(assistant, "get_chat_model", lambda *a, **k: UnusedModel())
    router = assistant.build_router()
    state = {"messages": [HumanMessage("anything")]}
    assert await router(state, _runtime(agent="docs")) == {"route": "docs"}


async def test_no_documents_routes_to_sql_without_a_model_call(monkeypatch):
    monkeypatch.setattr(assistant, "get_chat_model", lambda *a, **k: UnusedModel())
    monkeypatch.setattr(assistant, "document_names", lambda user_id: [])
    router = assistant.build_router()
    state = {"messages": [HumanMessage("what does my contract say?")]}
    assert await router(state, _runtime()) == {"route": "sql"}


async def test_router_asks_the_model_when_documents_exist(monkeypatch):
    prompts = []

    class Model:
        def with_fallbacks(self, fallbacks):
            return self

        async def ainvoke(self, messages):
            prompts.append(messages[0].content)
            return AIMessage("docs")

    monkeypatch.setattr(assistant, "get_chat_model", lambda *a, **k: Model())
    monkeypatch.setattr(assistant, "document_names", lambda user_id: ["handbook.pdf"])
    router = assistant.build_router()
    state = {"messages": [HumanMessage("how many vacation days do I get?")]}
    assert await router(state, _runtime()) == {"route": "docs"}
    assert "handbook.pdf" in prompts[0]


def test_obvious_route_uses_keywords_tickers_and_file_names(monkeypatch):
    monkeypatch.setattr(assistant, "stock_tickers", lambda: frozenset({"AAPL", "MSFT"}))
    names = ["handbook.pdf", "Q3 report.docx"]
    assert assistant.obvious_route("What did AAPL close at?", names) == "sql"
    assert assistant.obvious_route("giá cổ phiếu Apple hôm nay?", names) == "sql"
    assert assistant.obvious_route("Summarize the handbook", names) == "docs"
    assert assistant.obvious_route("Tóm tắt tài liệu này", names) == "docs"
    # Both sides or neither: let the model decide.
    assert assistant.obvious_route("Does the Q3 report mention MSFT?", names) is None
    assert assistant.obvious_route("how many vacation days do I get?", names) is None
    assert assistant.obvious_route("Is IT OK?", names) is None


async def test_obvious_message_skips_the_model(monkeypatch):
    monkeypatch.setattr(assistant, "get_chat_model", lambda *a, **k: UnusedModel())
    monkeypatch.setattr(assistant, "document_names", lambda user_id: ["handbook.pdf"])
    monkeypatch.setattr(assistant, "stock_tickers", lambda: frozenset({"AAPL"}))
    router = assistant.build_router()
    state = {"messages": [HumanMessage("AAPL dividend yield?")]}
    assert await router(state, _runtime()) == {"route": "sql"}
