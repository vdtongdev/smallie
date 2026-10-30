"""The orchestrator inside the real assistant graph, with scripted models instead of OpenRouter."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.agents import factory
from app.agents.docs import agent as docs_agent
from app.agents.research import agent as research_agent
from app.agents.sql import agent as sql_agent
from app.api.routes_chat import _events_from
from app.core.memory import TEAM_MODE, AgentContext
from app.core.prompts import CALL_NOTE
from app.orchestration import orchestrator as orchestrator_module
from app.orchestration.graph import build_assistant
from tests.conftest import local_agents
from tests.scripted import per_agent

CONFIG = {"configurable": {"thread_id": "t1"}}


def _call(name: str, args: dict, id: str = "c1") -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": id}])


def _assistant(monkeypatch, model, documents=("handbook.pdf",), **agents):
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(sql_agent, "describe_schema", lambda: "")
    monkeypatch.setattr(research_agent, "describe_schema", lambda: "")
    monkeypatch.setattr(sql_agent, "stock_tickers", lambda: frozenset({"AAPL"}))
    monkeypatch.setattr(docs_agent, "document_names", lambda user_id: [(name, None) for name in documents])
    return build_assistant(InMemorySaver(), InMemoryStore(), agents=local_agents(**agents))


async def _run(assistant, message: str) -> list[dict]:
    stream = assistant.astream(
        {"messages": [HumanMessage(message)]}, CONFIG, context=AgentContext(user_id="u1"),
        stream_mode=["messages", "updates"], subgraphs=True,
    )
    return [event async for event in _events_from(stream)]


def _streamed(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "token")


def tail_of(prompt) -> str:
    """The per-call note at the end of a model request ('' when there is none)."""
    last = prompt[-1]
    return last.content if last.name == CALL_NOTE else ""


async def _thread(assistant) -> list:
    return (await assistant.aget_state(CONFIG)).values["messages"]


async def test_generic_questions_are_answered_by_the_orchestrator_in_one_call(monkeypatch):
    model = per_agent(orchestrator=[AIMessage("Roses are red.")])
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "write a short poem")

    assert events[0] == {"type": "agent", "name": "orchestrator"}
    assert _streamed(events) == "Roses are red."
    assert len(model.prompts) == 1
    system = tail_of(model.prompts[0])
    assert "CITATIONS" not in system  # writing rules only once there are results
    assert "The user's uploaded documents: handbook.pdf" in system
    messages = await _thread(assistant)
    assert [(type(m), m.name) for m in messages] == [(HumanMessage, None), (AIMessage, "orchestrator")]


async def test_handoff_lets_the_specialist_reply_directly(monkeypatch):
    model = per_agent(
        orchestrator=[_call("handoff", {"agent": "sql"})],
        sql=[AIMessage("Apple's market cap is $3T.")],
    )
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "how big is the most valuable tech company?")

    agents = [e["name"] for e in events if e["type"] == "agent"]
    assert agents == ["orchestrator", "sql"]
    assert not any(e.get("name") == "handoff" for e in events if e["type"] in ("tool_call", "tool_result"))
    assert _streamed(events) == "Apple's market cap is $3T."
    # The specialist saw the user's message, not the orchestrator's tool call.
    assert [m.content for m in model.prompts[-1][1:]] == ["how big is the most valuable tech company?"]
    messages = await _thread(assistant)
    assert [(type(m), m.name) for m in messages] == [(HumanMessage, None), (AIMessage, "sql_agent")]


async def test_asking_one_specialist_first_becomes_a_handoff(monkeypatch):
    model = per_agent(
        orchestrator=[AIMessage("", tool_calls=[
            {"name": "ask_sql", "args": {"question": "Most valuable tech company?"}, "id": "s"},
            {"name": "search_memory", "args": {"query": "favourite stocks"}, "id": "m"},
        ])],
        sql=[AIMessage("NVIDIA, at $5.5T.")],
    )
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "which tech company is the most valuable?")

    assert [e["name"] for e in events if e["type"] == "agent"] == ["orchestrator", "sql"]
    assert "ask_sql" not in [e["name"] for e in events if e["type"] == "tool_call"]
    assert _streamed(events) == "NVIDIA, at $5.5T."
    # The specialist got the user's own message (with the whole chat), not a paraphrase.
    assert [m.content for m in model.prompts[-1][1:]] == ["which tech company is the most valuable?"]
    assert len(model.prompts) == 2  # one orchestrator call, one sql call


def test_only_a_first_single_delegation_is_rewritten():
    single = orchestrator_module.single_delegation
    ask = lambda *names: AIMessage("", tool_calls=[{"name": n, "args": {}, "id": n} for n in names])  # noqa: E731
    assert single(ask("ask_sql"), ["docs", "sql"]) == "sql"
    assert single(ask("ask_sql", "search_memory"), ["docs", "sql"]) == "sql"
    assert single(ask("ask_sql", "ask_docs"), ["docs", "sql"]) is None  # several: compose
    assert single(ask("ask_research"), ["docs", "sql"]) is None  # research never answers directly
    assert single(ask("tavily_tavily_search"), ["docs", "sql"]) is None
    rewritten = orchestrator_module.as_handoff(ask("ask_sql", "search_memory"), "sql")
    assert [(c["name"], c["args"]) for c in rewritten.tool_calls] == [("handoff", {"agent": "sql"})]


async def test_delegations_run_in_parallel_and_only_the_write_up_streams(monkeypatch):
    model = per_agent(
        orchestrator=[
            AIMessage("", tool_calls=[
                {"name": "ask_docs", "args": {"question": "What revenue does the handbook state?"}, "id": "d"},
                {"name": "ask_sql", "args": {"question": "What is AAPL's revenue?"}, "id": "s"},
            ]),
            AIMessage("The handbook says $1B [handbook.pdf, p. 2]; the database says $400B."),
        ],
        docs=[AIMessage("Revenue was $1B [handbook.pdf, p. 2].")],
        sql=[AIMessage("AAPL revenue: $400B (2026-10-10).")],
    )
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "compare the revenue in my report with Apple's real revenue")

    calls = [e["name"] for e in events if e["type"] == "tool_call"]
    assert set(calls) == {"ask_docs", "ask_sql"}
    results = {e["name"]: e["content"] for e in events if e["type"] == "tool_result"}
    assert results["ask_sql"] == "AAPL revenue: $400B (2026-10-10)."
    # The specialists' own answers are tool results, not streamed text.
    assert _streamed(events) == "The handbook says $1B [handbook.pdf, p. 2]; the database says $400B."

    # The specialists only saw the orchestrator's self-contained question.
    questions = {p[-1].content for p in model.prompts if "orchestrator" not in p[0].content and "You coordinate the team." not in p[0].content}
    assert questions == {"What revenue does the handbook state?", "What is AAPL's revenue?"}
    # The write-up call got the writing rules; the deciding call didn't.
    orchestrator_prompts = [p for p in model.prompts if "You coordinate the team." in p[0].content]
    assert "CITATIONS" not in tail_of(orchestrator_prompts[0])
    assert "CITATIONS" in tail_of(orchestrator_prompts[1])
    # Only the final answer is kept in the thread.
    messages = await _thread(assistant)
    assert [type(m) for m in messages] == [HumanMessage, AIMessage]
    assert not any(isinstance(m, ToolMessage) for m in messages)


async def test_unavailable_specialists_and_the_previous_answer_are_noted(monkeypatch):
    model = per_agent(orchestrator=[AIMessage("Hi!"), AIMessage("Still here.")])
    assistant = _assistant(monkeypatch, model, documents=())
    await _run(assistant, "hello")
    await _run(assistant, "and you?")
    first, second = (tail_of(p) for p in model.prompts)
    assert "- docs: not available for this user right now" in first
    assert "previous answer in this chat came from: orchestrator" in second


def test_tools_cover_every_delegable_and_handoff_agent(monkeypatch):
    monkeypatch.setattr(sql_agent, "describe_schema", lambda: "")
    monkeypatch.setattr(research_agent, "describe_schema", lambda: "")
    from app.agents.registry import AGENTS

    lines = orchestrator_module.agent_lines(AGENTS)
    assert "- sql:" in lines and "`handoff` or `ask_sql`" in lines
    assert "- research:" in lines and "Use: `ask_research`." in lines  # research is never handed off
    handoff = orchestrator_module.make_handoff_tool(["docs", "sql"])
    assert handoff.return_direct
    assert set(handoff.args["agent"]["enum"]) == {"docs", "sql"}


def test_composing_starts_with_tool_results_or_findings():
    is_composing = orchestrator_module.is_composing
    assert not is_composing([HumanMessage("hi")])
    assert is_composing([HumanMessage("q"), _call("ask_sql", {}), ToolMessage("rows", name="ask_sql", tool_call_id="c1")])
    assert is_composing([HumanMessage("q"), HumanMessage("FINDINGS", name=orchestrator_module.FINDINGS)])
    # Looking up a memory isn't something to write up.
    assert not is_composing([HumanMessage("I'm Dung"), ToolMessage("none", name="search_memory", tool_call_id="1")])
    # Results of an earlier turn don't count.
    assert not is_composing([ToolMessage("rows", name="ask_sql", tool_call_id="1"), HumanMessage("thanks")])


async def test_a_message_for_two_specialists_is_asked_of_both_and_combined(monkeypatch):
    # A file name and a ticker: the router leaves it to the orchestrator, which asks docs first.
    model = per_agent(
        orchestrator=[
            _call("ask_docs", {"question": "Does the handbook mention Apple?"}, "d"),
            _call("ask_sql", {"question": "What is AAPL's market cap?"}, "s"),
            AIMessage("The handbook mentions Apple [handbook.pdf, p. 3], now worth $4T (market DB, 2026-10-13)."),
        ],
        docs=[AIMessage("Yes: Apple is a client [handbook.pdf, p. 3].")],
        sql=[AIMessage("AAPL market cap: $4T (2026-10-13).")],
    )
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "Does the handbook mention AAPL, and how big is it?")

    # Not turned into a handoff to docs, so sql could still be asked.
    assert [e["name"] for e in events if e["type"] == "agent"] == ["orchestrator"]
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["ask_docs", "ask_sql"]
    assert _streamed(events).startswith("The handbook mentions Apple")
    first_prompt = tail_of(next(p for p in model.prompts if "You coordinate the team." in p[0].content))
    assert "touches the data of several specialists: docs, sql" in first_prompt
    messages = await _thread(assistant)
    assert [(type(m), m.name) for m in messages] == [(HumanMessage, None), (AIMessage, "orchestrator")]


def test_web_search_runs_with_wikipedia_alongside():
    rule = orchestrator_module.web_search_rule
    assert rule(["tavily_tavily_search", "knowledge_wikipedia_search"]).startswith(
        "`tavily_tavily_search` (plus `knowledge_wikipedia_search` in the same step")
    assert rule(["knowledge_wikipedia_search"]) == "`knowledge_wikipedia_search`"
    assert rule([]).startswith("nothing")


PRICE_ROWS = (["ticker", "close"], [("AAPL", 250.0), ("MSFT", 500.0)], False)


def _fake_db(monkeypatch) -> list[str]:
    """Queries hit a fake database; every table gets the id "tabcdef"."""
    from app.agents.chart import tools as chart_tools
    from app.agents.sql import tools as sql_tools

    queries, make_table = [], sql_tools.make_table

    def fetch(sql, max_rows, engine=None):
        queries.append(sql)
        return PRICE_ROWS

    monkeypatch.setattr(sql_tools, "fetch_readonly", fetch)
    monkeypatch.setattr(sql_tools, "make_table", lambda *args: {**make_table(*args), "id": "tabcdef"})
    monkeypatch.setattr(chart_tools, "query_readonly", lambda sql: sql_tools.make_table(sql, *fetch(sql, 0)))
    return queries


def _charts(events) -> list[dict]:
    return [c for e in events if e["type"] == "tool_result" for c in e.get("charts", [])]


async def test_a_chart_request_draws_once_and_plot_that_reuses_the_table(monkeypatch):
    queries = _fake_db(monkeypatch)
    chart_args = {"type": "bar", "title": "Close", "x_column": "ticker"}
    model = per_agent(chart=[
        _call("chart_sql", {"query": "SELECT ticker, close FROM market.daily_prices", **chart_args}),
        AIMessage("MSFT closed at twice AAPL's price."),
        _call("chart_table", {"table_id": "tabcdef", **chart_args, "type": "line"}),
        AIMessage("Same data as a line."),
    ])
    assistant = _assistant(monkeypatch, model)

    events = await _run(assistant, "Chart AAPL vs MSFT closing prices")
    assert [e["name"] for e in events if e["type"] == "agent"] == ["chart"]
    (chart,) = _charts(events)
    assert chart["labels"] == ["AAPL", "MSFT"] and chart["series"][0]["values"] == [250.0, 500.0]
    assert _streamed(events) == "MSFT closed at twice AAPL's price."

    events = await _run(assistant, "plot that as a line")
    assert [c["type"] for c in _charts(events)] == ["line"]
    assert len(queries) == 1  # the second chart reused the first result
    tables_note = tail_of(model.prompts[-2])  # TABLES IN THIS CHAT, with how each was charted
    assert "tabcdef: 2 rows, columns ticker, close" in tables_note
    assert 'already charted as bar "Close"' in tables_note
    # Both charts stay in the thread with their tool results, for the history view.
    messages = await _thread(assistant)
    assert len([m for m in messages if isinstance(m, ToolMessage) and m.artifact.get("charts")]) == 2


async def test_the_orchestrator_charts_a_specialist_table_and_keeps_it_on_its_answer(monkeypatch):
    _fake_db(monkeypatch)
    model = per_agent(
        orchestrator=[
            AIMessage("", tool_calls=[
                {"name": "ask_docs", "args": {"question": "What revenue does my report give?"}, "id": "d"},
                {"name": "ask_sql", "args": {"question": "AAPL and MSFT closing prices?"}, "id": "s"},
            ]),
            _call("chart_table", {"table_id": "tabcdef", "type": "bar", "title": "Close", "x_column": "ticker"}),
            AIMessage("Your report's revenue is $1B [report.pdf, p. 2]; the chart shows both closes."),
        ],
        docs=[AIMessage("Revenue was $1B [report.pdf, p. 2].")],
        sql=[_call("run_sql", {"query": "SELECT ticker, close FROM market.daily_prices"}),
             AIMessage("AAPL 250, MSFT 500.")],
    )
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "compare the revenue in my report with Apple's and Microsoft's closing prices")

    assert [e["name"] for e in events if e["type"] == "tool_call"].count("chart_table") == 1
    assert len(_charts(events)) == 1
    # The write-up call listed the sql table so it could chart it by id.
    writer_prompts = [tail_of(p) for p in model.prompts if "You coordinate the team." in p[0].content]
    assert "tabcdef: 2 rows" in writer_prompts[1]
    # Only the answer enters the thread, carrying the chart (for reload) and the table (for "plot that").
    messages = await _thread(assistant)
    answer = messages[-1]
    assert [type(m) for m in messages] == [HumanMessage, AIMessage] and answer.name == "orchestrator"
    assert [c["id"] for c in answer.additional_kwargs["charts"]] == [_charts(events)[0]["id"]]
    assert [t["id"] for t in answer.additional_kwargs["tables"]] == ["tabcdef"]


async def test_max_asks_still_caps_remote_calls(monkeypatch):
    """An orchestrator that keeps asking a remote specialist is stopped by MAX_ASKS (no loop)."""
    from app.agents.remote import RemoteAgent
    from tests.test_remote import FakeRemote

    remote = FakeRemote(output={"messages": [{"type": "human", "content": "q", "id": "q"},
                                             {"type": "ai", "content": "AAPL's P/E is 30.", "name": "sql_agent", "id": "a"}]})
    sql = RemoteAgent(sql_agent.SPEC, "http://sql:8000", service_token="t", timeout_s=5, remote=remote)
    asks = [_call("ask_sql", {"question": f"What is AAPL's P/E? Try {i}."}, id=f"c{i}") for i in range(3)]
    model = per_agent(orchestrator=[*asks, AIMessage("AAPL's P/E is 30.")])
    # Two specialists mentioned, so the first ask isn't turned into a handoff.
    events = await _run(_assistant(monkeypatch, model, sql=sql), "Compare AAPL's P/E with what handbook.pdf says")

    assert len(remote.calls) == orchestrator_module.MAX_ASKS
    assert _streamed(events) == "AAPL's P/E is 30."


async def test_orchestrator_answer_records_asked_routes(monkeypatch):
    ask = AIMessage("", tool_calls=[{"name": "ask_sql", "args": {"question": "AAPL cap?"}, "id": "s"}])
    model = per_agent(orchestrator=[ask, AIMessage("3T.")], sql=[AIMessage("Cap 3T.")])
    assistant = _assistant(monkeypatch, model)
    await _run(assistant, "What is the AAPL market cap and what does handbook.pdf say?")

    assert (await _thread(assistant))[-1].additional_kwargs["asked"] == ["sql"]


async def test_orchestrator_names_reply_language_every_call(monkeypatch):
    model = per_agent(orchestrator=[AIMessage("Neither is better.")])
    assistant = _assistant(monkeypatch, model)
    context = AgentContext(user_id="u1", memories="- User prefers_language Vietnamese")
    stream = assistant.astream(
        {"messages": [HumanMessage("Which one is better?")]}, CONFIG, context=context,
        stream_mode=["messages", "updates"], subgraphs=True,
    )
    [event async for event in _events_from(stream)]
    system = tail_of(model.prompts[0])
    assert "REPLY LANGUAGE for this message: English" in system
    assert "CITATIONS" not in system  # still the short, non-composing call


async def test_composing_call_shares_the_system_prompt(monkeypatch):
    model = per_agent(
        orchestrator=[
            AIMessage("", tool_calls=[
                {"name": "ask_docs", "args": {"question": "What revenue does the handbook state?"}, "id": "d"},
                {"name": "ask_sql", "args": {"question": "What is AAPL's revenue?"}, "id": "s"},
            ]),
            AIMessage("The handbook says $1B; the database says $400B."),
        ],
        docs=[AIMessage("Revenue was $1B [handbook.pdf, p. 2].")],
        sql=[AIMessage("AAPL revenue: $400B (2026-10-10).")],
    )
    assistant = _assistant(monkeypatch, model)
    await _run(assistant, "what is Apple's revenue?")
    orchestrator_prompts = [p for p in model.prompts if "You coordinate the team." in p[0].content]
    assert len(orchestrator_prompts) == 2
    assert orchestrator_prompts[0][0].content == orchestrator_prompts[1][0].content


async def test_team_write_up_gets_compose_rules_in_the_tail(monkeypatch):
    model = per_agent(orchestrator=[AIMessage("Done.")])
    assistant = _assistant(monkeypatch, model)
    stream = assistant.astream(
        {"messages": [HumanMessage("summarise the findings"), HumanMessage("team note", name=TEAM_MODE)]},
        CONFIG, context=AgentContext(user_id="u1"), stream_mode=["messages", "updates"], subgraphs=True,
    )
    [event async for event in _events_from(stream)]
    assert "CITATIONS" in tail_of(model.prompts[0])
    assert "CITATIONS" not in model.prompts[0][0].content
