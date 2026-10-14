"""A specialist the router picked that finds no answer hands the message to the orchestrator,
which can't send it back or ask the same thing again and again."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.docs.agent import SPEC as DOCS
from app.agents.sql.agent import SPEC as SQL
from app.orchestration.fallback import attempt_summary
from tests.scripted import PROMPT_PHRASES, per_agent
from tests.test_orchestrator import _assistant, _call, _run, _streamed, _thread


def _tool_turn(name: str, result: str, reply: str) -> list:
    return [_call(name, {"query": "q"}), ToolMessage(result, name=name, tool_call_id="c1"), AIMessage(reply)]


def test_gave_up_on_empty_results_no_reply_or_a_not_found_reply():
    assert SQL.gave_up(_tool_turn("run_sql", "| ticker |\n| --- |\n\n(0 rows; table t1)", "Nothing matched."))
    assert SQL.gave_up(_tool_turn("run_sql", "Error: column x does not exist", "The query failed."))
    assert DOCS.gave_up(_tool_turn("search_documents", "No matching passages in the user's documents.", "Sorry."))
    assert DOCS.gave_up(_tool_turn("search_documents", "[cv.pdf, p. 1]\nPython, SQL",
                                   "I couldn't find your salary in the uploaded documents."))
    assert DOCS.gave_up(_tool_turn("search_documents", "[cv.pdf]\nx", "Tài liệu không đề cập đến mức lương."))
    assert SQL.gave_up([_call("run_sql", {"query": "q"})])  # ran out of model calls mid-way
    assert SQL.gave_up([AIMessage("Model call limits exceeded: run limit (5/5)")])
    assert SQL.gave_up([])


def test_answers_negative_answers_and_partial_answers_count():
    table = "| ticker | close |\n| --- | --- |\n| AAPL | 230 |\n(1 rows; table t1)"
    assert not SQL.gave_up(_tool_turn("run_sql", table, "AAPL closed at $230."))
    # A failed query that was fixed in the next step.
    fixed = [*_tool_turn("run_sql", "Error: bad column", "")[:2], _call("run_sql", {"query": "q2"}, "c2"),
             ToolMessage(table, name="run_sql", tool_call_id="c2"), AIMessage("AAPL closed at $230.")]
    assert not SQL.gave_up(fixed)
    assert not SQL.gave_up(_tool_turn("run_sql", table, "AAPL pays no dividend."))
    assert not SQL.gave_up([AIMessage("You asked about AAPL earlier: it closed at $230.")])  # from the chat
    long = "Your resume lists Python, SQL and five years at Acme. " * 12 + "I couldn't find your salary."
    assert not DOCS.gave_up(_tool_turn("search_documents", "[cv.pdf]\nPython", long))


def test_the_attempt_summary_lists_tool_calls_results_and_reply():
    summary = attempt_summary(_tool_turn("search_documents", "No matching passages.", "Sorry, nothing."))
    assert summary == '- search_documents({"query": "q"}) -> No matching passages.\nIts reply: Sorry, nothing.'


async def test_a_specialist_that_finds_nothing_falls_back_to_the_orchestrator(monkeypatch):
    model = per_agent(
        docs=[AIMessage("I couldn't find anything about salaries in your documents.")],
        orchestrator=[AIMessage("Salaries are not in the handbook; typical ranges are…")],
    )
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "what does the handbook say about salaries?")

    kinds = [(e["type"], e.get("name") or e.get("agent")) for e in events if e["type"] in ("agent", "fallback")]
    assert kinds == [("agent", "docs"), ("fallback", "docs"), ("agent", "orchestrator")]
    assert _streamed(events).endswith("Salaries are not in the handbook; typical ranges are…")
    orchestrator_prompt = model.prompts[-1]
    note = orchestrator_prompt[-1]
    assert note.name == "failed_attempt" and "the docs specialist already worked" in note.content
    assert "I couldn't find anything about salaries" in note.content
    # It can't hand the message back.
    assert "handoff" not in [t["function"]["name"] for t in model.tools[-1]]
    # The failed attempt is not kept in the thread.
    messages = await _thread(assistant)
    assert [(type(m), m.name) for m in messages] == [(HumanMessage, None), (AIMessage, "orchestrator")]

    # The next message routes normally again (the fallback state was cleared).
    model.scripts[PROMPT_PHRASES["docs"]] = iter([AIMessage("The handbook covers leave.")])
    events = await _run(assistant, "summarize the handbook")
    assert [e["type"] for e in events if e["type"] == "fallback"] == []


async def test_an_answer_or_a_handed_off_specialist_does_not_fall_back(monkeypatch):
    model = per_agent(sql=[AIMessage("AAPL closed at $230.")])
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "AAPL close?")
    assert not any(e["type"] == "fallback" for e in events)

    # Handed off by the orchestrator: its "nothing found" is the answer (no loop back).
    model = per_agent(orchestrator=[_call("handoff", {"agent": "sql"})],
                      sql=[AIMessage("I couldn't find that company in the database.")])
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "how big is Acme Corp?")
    assert not any(e["type"] == "fallback" for e in events)
    assert len(model.prompts) == 2


async def test_the_orchestrator_cannot_repeat_calls_or_keep_asking_a_specialist(monkeypatch):
    ask = lambda q, id: _call("ask_docs", {"question": q}, id)  # noqa: E731
    model = per_agent(
        docs=[AIMessage("I couldn't find salaries in your documents."),  # the router's direct attempt
              AIMessage("I couldn't find a pay table in handbook.pdf.")],  # the one retry
        orchestrator=[ask("Pay table in handbook.pdf?", "a1"),
                      ask("pay table in  handbook.pdf?", "a2"),  # the same call again
                      ask("Salary bands in handbook.pdf?", "a3"),  # a third ask
                      AIMessage("The handbook has no salary information.")],
    )
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "what does the handbook say about salaries?")

    results = [e["content"] for e in events if e["type"] == "tool_result" and e["name"] == "ask_docs"]
    assert results[0] == "I couldn't find a pay table in handbook.pdf."
    assert results[1].startswith("Not run: this exact call")
    assert results[2].startswith("Not run: this specialist was already asked")
    assert _streamed(events).endswith("The handbook has no salary information.")
    docs_calls = [p for p in model.prompts if "answers questions using the user's uploaded documents" in p[0].content]
    assert len(docs_calls) == 2  # the direct attempt and one retry
