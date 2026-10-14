"""The research workflow inside the real assistant graph, with a scripted model instead of OpenRouter."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.agents import factory
from app.agents.research import agent as research_agent
from app.agents.sql import agent as sql_agent
from app.api.routes_chat import _events_from
from app.core.memory import AgentContext
from app.core.prompts import reply_language
from app.orchestration.graph import build_assistant
from tests.scripted import scripted as _turn

NOTES = "FINDINGS:\n- LangGraph is a library for agent graphs [source: https://example.com/lg]\nGAPS: none"


def _assistant(monkeypatch, model):
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(sql_agent, "describe_schema", lambda: "")
    monkeypatch.setattr(research_agent, "describe_schema", lambda: "")
    monkeypatch.setattr("app.agents.docs.agent.document_names", lambda user_id: [])
    return build_assistant(InMemorySaver(), InMemoryStore())


async def _run(assistant, message: str, thread: str = "t1") -> list[dict]:
    config = {"configurable": {"thread_id": thread}}
    context = AgentContext(user_id="u1", agent="research")
    stream = assistant.astream(
        {"messages": [HumanMessage(message)]}, config, context=context,
        stream_mode=["messages", "updates"], subgraphs=True,
    )
    return [event async for event in _events_from(stream)]


async def test_findings_flow_to_the_orchestrator_and_only_the_answer_is_kept(monkeypatch):
    model = _turn(
        AIMessage("", tool_calls=[{"name": "load_skill", "args": {"name": "academic"}, "id": "c1"}]),
        AIMessage(NOTES),  # researcher's notes: must not stream
        AIMessage("LangGraph builds agent graphs [example](https://example.com/lg)."),  # orchestrator
    )
    assistant = _assistant(monkeypatch, model)
    events = await _run(assistant, "Tìm hiểu về LangGraph")

    assert events[0] == {"type": "agent", "name": "research"}
    kinds = [e["type"] for e in events]
    assert kinds.index("tool_call") < kinds.index("token")
    assert {"type": "tool_call", "name": "load_skill", "args": {"name": "academic"}} in events
    assert any(e["type"] == "tool_result" and e["name"] == "load_skill" for e in events)
    streamed = "".join(e["text"] for e in events if e["type"] == "token")
    assert "LangGraph builds agent graphs" in streamed
    assert "FINDINGS" not in streamed

    # The orchestrator saw the notes as its last message and got the writing rules, in Vietnamese,
    # without the research tool: the research is done.
    prompt = model.prompts[-1]
    offered = [t["function"]["name"] for t in model.tools[-1]]
    assert "ask_research" not in offered and "ask_sql" in offered
    assert "RESEARCH FINDINGS" in prompt[-1].content and NOTES in prompt[-1].content
    assert "Write it in Vietnamese" in prompt[-1].content
    assert "CITATIONS" in prompt[0].content and "Write the whole answer in Vietnamese" in prompt[0].content

    # The thread keeps only the question and the orchestrator's answer.
    messages = (await assistant.aget_state({"configurable": {"thread_id": "t1"}})).values["messages"]
    assert [type(m) for m in messages] == [HumanMessage, AIMessage]
    assert messages[-1].name == "orchestrator"
    assert not any(isinstance(m, ToolMessage) for m in messages)


async def test_researcher_does_not_carry_old_tool_output_into_the_next_turn(monkeypatch):
    model = _turn(AIMessage(NOTES), AIMessage("First answer."), AIMessage(NOTES), AIMessage("Second answer."))
    assistant = _assistant(monkeypatch, model)
    await _run(assistant, "research A")
    await _run(assistant, "and B?")
    # An English question names English outright, next to the findings and in the writing rules.
    first_write_up = model.prompts[1]
    assert "Write it in English" in first_write_up[-1].content
    assert "Write the whole answer in English" in first_write_up[0].content
    second_research_prompt = model.prompts[2]  # [system prompt, *conversation]
    assert [m.content for m in second_research_prompt[1:]] == ["research A", "First answer.", "and B?"]
    # The first turn's findings were used up: the second write-up only sees the new ones.
    assert sum("RESEARCH FINDINGS" in m.content for m in model.prompts[3]) == 1


def test_cut_short_research_hands_over_raw_tool_results():
    messages = [
        HumanMessage("q"),
        AIMessage("", tool_calls=[{"name": "knowledge_wikipedia_page", "args": {}, "id": "1"}]),
        ToolMessage(content=[{"type": "text", "text": "Article text"}], name="knowledge_wikipedia_page", tool_call_id="1"),
    ]
    findings = research_agent.findings_from(messages)
    assert "cut short" in findings and "[knowledge_wikipedia_page]\nArticle text" in findings
    assert research_agent.findings_from([HumanMessage("q"), AIMessage(NOTES)]) == NOTES
    # The call-limit middleware's closing message is not notes.
    stopped = research_agent.findings_from([*messages, AIMessage("Model call limits exceeded: run limit (8/8)")])
    assert "cut short" in stopped and "Article text" in stopped


def test_sources_lists_loaded_servers_then_files_and_stocks():
    text = research_agent.sources(["tavily", "exa"])
    lines = text.splitlines()
    assert lines[0].startswith("- Web search")
    assert lines[1] == "- Tools of the 'exa' MCP server (exa_* tools)."
    assert "search_documents" in lines[2] and "run_sql" in lines[3]


WIKI_RESULT = """Nguyễn Nhật Ánh (https://vi.wikipedia.org/wiki/Nguyễn_Nhật_Ánh)

Nguyễn Nhật Ánh là một nam nhà văn.

## Cuộc đời và sự nghiệp (https://vi.wikipedia.org/wiki/Nguyễn_Nhật_Ánh#Cuộc_đời_và_sự_nghiệp)
Nguyễn Nhật Ánh sinh ngày 7 tháng 5 năm 1955
tại làng Đo Đo, xã Bình Quế.

## Đời tư (https://vi.wikipedia.org/wiki/Nguyễn_Nhật_Ánh#Đời_tư)
Ông có con gái tên Nguyễn Nhật Quỳnh Anh."""
PAGE = "https://vi.wikipedia.org/wiki/Nguyễn_Nhật_Ánh"


def _tool(name: str, text: str) -> ToolMessage:
    return ToolMessage(content=[{"type": "text", "text": text}], name=name, tool_call_id=name)


TAVILY_RESULT = json.dumps({"results": [
    {"url": "https://zalopay.vn/sach-hay-1407", "title": "Sách hay",
     "raw_content": "## Mắt Biếc - Trên 300.000 Bản\nCâu chuyện về Ngạn và Hà Lan."},
    {"url": "https://example.com/other", "title": "Other", "content": "Unrelated text."},
]}, ensure_ascii=False)
PAGES = research_agent.pages_from([_tool("knowledge_wikipedia_page", WIKI_RESULT), _tool("tavily_tavily_extract", TAVILY_RESULT)])


def test_pages_come_from_tavily_json_and_wikipedia_text():
    assert [p.url for p in PAGES] == [PAGE, "https://zalopay.vn/sach-hay-1407", "https://example.com/other"]
    assert "Ngạn và Hà Lan" in PAGES[1].text


# The whole article, as with_wikipedia_articles adds it (the researcher read the page via web search).
ARTICLE = research_agent.Page(PAGE, WIKI_RESULT, "Nguyễn Nhật Ánh", article=True)
SECTION = f"{PAGE}#Cuộc_đời_và_sự_nghiệp"


def test_paragraph_sources_cite_the_section_and_list_sources_the_passage():
    escaped = "https://vi.wikipedia.org/wiki/Nguy%E1%BB%85n_Nh%E1%BA%ADt_%C3%81nh"
    findings = (
        f'- Born 1955 [source: {escaped} | "Nguyễn Nhật Ánh sinh ngày 7 tháng 5 năm 1955 tại làng Đo Đo, xã Bình Quế."]\n'
        f'- Born in Bình Quế [source: Wikipedia | {PAGE} | "tại làng Đo Đo, xã Bình Quế"]\n'
        f'- Daughter [source: Wikipedia | {PAGE} | "Ông có con gái tên Nguyễn Nhật Quỳnh Anh và hai cháu."]\n'
        f'- Intro [source: {PAGE} | "Nguyễn Nhật Ánh là một nam nhà văn."]\n'
        f"- No quote [source: Wikipedia | {PAGE}]"
    )
    lines = research_agent.link_sources(findings, [*PAGES, ARTICLE]).splitlines()
    # Facts from one section share its URL; the label is the article title and section name,
    # and the passage link highlights the quote (a start,end range for a long one).
    assert lines[0].endswith(
        f"[source: Nguyễn Nhật Ánh – Cuộc đời và sự nghiệp | {SECTION} | "
        f"passage: {SECTION}:~:text=Nguyễn%20Nhật%20Ánh%20sinh,Đo%2C%20xã%20Bình%20Quế]")
    assert f"[source: Nguyễn Nhật Ánh – Cuộc đời và sự nghiệp | {SECTION} | passage: " in lines[1]
    # A paraphrased ending: the passage uses the quote's first words.
    assert lines[2].endswith(f"| {PAGE}#Đời_tư | passage: {PAGE}#Đời_tư:~:text=Ông%20có%20con%20gái%20tên%20Nguyễn]")
    # Before the first heading, or without a quote: the article itself.
    assert lines[3].endswith(f"[source: Nguyễn Nhật Ánh | {PAGE} | passage: {PAGE}#:~:text=Nguyễn%20Nhật%20Ánh%20là%20một%20nam%20nhà%20văn]")
    assert lines[4].endswith(f"[source: Nguyễn Nhật Ánh | {PAGE}]")


def test_other_sites_keep_their_url_and_use_the_page_title():
    findings = (
        '- Mắt biếc sold 300,000 copies [source: Zalopay article, "Mắt Biếc - Trên 300.000 Bản"]\n'
        "- Another book [source: Zalopay article]\n"
        '- Made up [source: Site | https://example.com/a | "words that appear nowhere at all"]\n'
        "- Two links [source: Site | https://example.com/b | https://example.com/c]\n"
        "- Unknown [source: some magazine]"
    )
    lines = research_agent.link_sources(findings, PAGES).splitlines()
    # Missing URL, found by its quote: that page and its title, plus the highlighted passage.
    assert lines[0].endswith("[source: Sách hay | https://zalopay.vn/sach-hay-1407 | "
                             "passage: https://zalopay.vn/sach-hay-1407#:~:text=Mắt%20Biếc%20%2D%20Trên%20300.000%20Bản]")
    # Missing URL, no quote: the only page whose address matches the label.
    assert lines[1].endswith("[source: Sách hay | https://zalopay.vn/sach-hay-1407]")
    # A quote that isn't on any page gets no passage link.
    assert lines[2].endswith("[source: Site | https://example.com/a]")
    assert lines[3].endswith("[source: Site | https://example.com/b | https://example.com/c]")
    assert lines[4].endswith("[source: some magazine]")


async def test_cited_wikipedia_articles_are_fetched_once(monkeypatch):
    fetched = []

    async def fake_article(url):
        fetched.append(url)
        return WIKI_RESULT

    monkeypatch.setattr(research_agent, "wikipedia_article", fake_article)
    findings = f"- a [source: {PAGE}#x]\n- b [source: {PAGE}]\n- c [source: https://example.com/a]"
    pages = await research_agent.with_wikipedia_articles(findings, PAGES)
    assert fetched == [PAGE]
    assert pages[-1] == ARTICLE


def test_reply_language_follows_the_latest_question():
    assert reply_language([HumanMessage("hello"), HumanMessage("nguyễn nhật ánh là ai")]) == "Vietnamese"
    assert reply_language([HumanMessage("nguyễn nhật ánh là ai"), HumanMessage("do research on him")]) == "English"


def test_web_search_alongside_wikipedia_only_when_web_search_loaded(monkeypatch):
    prompts = []
    monkeypatch.setattr(research_agent, "describe_schema", lambda: "")
    monkeypatch.setattr(research_agent, "build_agent", lambda **kw: prompts.append(kw["system_prompt"]))
    monkeypatch.setattr(research_agent, "_mcp_servers", ["tavily", "knowledge"])
    research_agent.build_research_workflow()
    monkeypatch.setattr(research_agent, "_mcp_servers", ["knowledge"])
    research_agent.build_research_workflow()
    assert "Whenever you search Wikipedia, also run a web search" in prompts[0]
    assert "Whenever you search Wikipedia" not in prompts[1]


WIKI_SECTION = (
    "Python (programming language), section History (https://en.wikipedia.org/wiki/Python_(programming_language)#History)\n\n"
    "Python was conceived in the late 1980s by Guido van Rossum.\n\n"
    "Articles linked from this section:\n"
    "- Guido van Rossum (https://en.wikipedia.org/wiki/Guido_van_Rossum)"
)
TAVILY_JSON = json.dumps({"results": [{"url": "https://example.com/news", "title": "Battery news",
                                        "content": "Solid-state cells entered pilot production in 2026."}]})


async def test_tool_results_list_source_ids_that_the_notes_cite_and_code_expands():
    results = [_tool("tavily_tavily_search", TAVILY_JSON), _tool("knowledge_wikipedia_page", WIKI_SECTION)]
    index = research_agent.source_index(results)
    by_url = {url: (sid, title) for sid, (url, title) in index.items()}
    history = "https://en.wikipedia.org/wiki/Python_(programming_language)#History"
    assert set(by_url) == {"https://example.com/news", history, "https://en.wikipedia.org/wiki/Guido_van_Rossum"}
    assert by_url[history][1] == "Python (programming language), section History"  # ")" of the title kept
    assert by_url["https://example.com/news"] == (research_agent.source_id("https://example.com/news"), "Battery news")

    # The researcher sees the ids at the end of each result; page parsing ignores them.
    async def handler(request):
        return results[0]

    with_ids = await research_agent.cite_by_id.awrap_tool_call(None, handler)
    assert with_ids.text.endswith(f"{by_url['https://example.com/news'][0]} = Battery news (https://example.com/news)")
    assert research_agent.pages_from([with_ids]) == research_agent.pages_from([results[0]])

    news, guido = by_url["https://example.com/news"][0], by_url["https://en.wikipedia.org/wiki/Guido_van_Rossum"][0]
    notes = (f'- Pilot production in 2026 [source: {news} | "entered pilot production in 2026"]\n'
             f'- Created by van Rossum [source: {guido}]\n'
             f'- Unknown id [source: Sffff | "conceived in the late 1980s"]\n'
             "- From a file [source: report.pdf, p. 2]")
    expanded = research_agent.expand_source_ids(notes, index).splitlines()
    assert expanded[0].endswith('[source: Battery news | https://example.com/news | "entered pilot production in 2026"]')
    assert expanded[1].endswith("[source: Guido van Rossum | https://en.wikipedia.org/wiki/Guido_van_Rossum]")
    assert expanded[2].endswith('[source: "conceived in the late 1980s"]')  # link_sources finds it by the quote
    assert expanded[3] == notes.splitlines()[3]
    linked = research_agent.link_sources("\n".join(expanded), research_agent.pages_from(results)).splitlines()
    assert "passage: https://example.com/news#:~:text=" in linked[0]
    assert f"| {history} |" in linked[2] or history in linked[2]


def test_ids_are_expanded_however_the_model_wrote_them_and_unknown_ones_never_reach_the_writer():
    a, b = "https://a.com/x", "https://b.com/y"
    ia, ib = research_agent.source_id(a), research_agent.source_id(b)
    index = {ia: (a, "Page [A]"), ib: (b, "Page B")}
    expand = lambda notes: research_agent.expand_source_ids(notes, index)  # noqa: E731
    assert expand(f'- f [source: {ia} | "five words of the quote"]') == f'- f [source: Page  A | {a} | "five words of the quote"]'
    assert expand(f"- f [{ia}]") == f"- f [source: Page  A | {a}]"
    assert expand(f'- f ({ia} | "q")') == f'- f [source: Page  A | {a} | "q"]'
    assert expand(f"- f [source {ia}, {ib}]") == f"- f [source: Page  A | {a} | {b}]"
    assert expand(f"- f {ia.upper()}") == f"- f [source: Page  A | {a}]"
    assert expand('- f [source: Sffff | "q"]') == '- f [source: "q"]'
    assert expand("- f [Sffff] Sffff") == "- f  "
    assert expand("- SDEFA rose [source: report.pdf, p. 2]") == "- SDEFA rose [source: report.pdf, p. 2]"
