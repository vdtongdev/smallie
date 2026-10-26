"""Research: a researcher gathers findings with its tools; the orchestrator writes the answer.

    START -> gather (research_agent) -> END

`gather` leaves the linked findings in `findings`, never in the messages: routed here by the
router, the assistant graph continues to the orchestrator, which composes the answer from them;
called as the orchestrator's `research` tool, the findings are the tool result. The researcher's
tool calls show in the UI as progress, but its notes and tool output stay out of the shared history.
"""

import asyncio
import hashlib
import json
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, TypedDict
from urllib.parse import unquote, urlparse

from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from app.agents.base import LIMIT_MESSAGE, AgentSpec
from app.agents.docs.tools import search_documents
from app.agents.factory import build_agent
from app.agents.research.prompts import (
    DOCUMENTS_LINE,
    MARKET_LINE,
    RESEARCHER_SYSTEM,
    ROUTE_DESCRIPTION,
    SCHEMA_HEADING,
    SOURCE_LINES,
    SOURCES_HEADING,
    REVIEW,
    TOOL_DESCRIPTION,
    WEB_ALONGSIDE_WIKI,
)
from app.agents.skills import load_skills, make_load_skill_tool, skills_section
from app.agents.sql.tools import describe_schema, run_sql
from app.core.config import get_settings
from app.core.mcp import open_mcp_tools
from app.core.memory import TOOL_SOURCE, AgentContext
from app.core.prompts import MEMORY_RULE
from app.features.research.mcp_servers.knowledge import wikipedia_article
from app.features.research.sources import local_servers, remote_servers

log = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent / "skills"
RESEARCHER_SKILLS = load_skills(SKILLS_DIR / "researcher")
RAW_RESULTS = 4  # tool results handed on when the researcher ran out of steps
RAW_RESULT_CHARS = 1500
# Tavily tools that spend many credits per call (the free plan has 1,000/month); search and
# extract are enough, and fewer tools are easier for small models to choose from.
SKIPPED_TOOLS = frozenset({"tavily_tavily_crawl", "tavily_tavily_map", "tavily_tavily_research"})

HINTS = re.compile(
    r"\b(research|look (?:it )?up online|search (?:the )?(?:web|internet|online)|latest news|news (?:about|on)|"
    r"arxiv|wikipedia|nghiên cứu|tìm hiểu|tra cứu|tin tức|báo cáo)\b",
    re.IGNORECASE,
)

NAMES = re.compile(
    r"\bresearch(?:er)? agent\b|\b(?:search|look (?:it )?up|browse) (?:the )?(?:web|internet|online)\b|\b(?:web|internet) search\b"
    r"|\bwikipedia\b|\barxiv\b|\btrợ lý nghiên cứu\b|tìm (?:trên )?(?:web|mạng|internet)",
    re.IGNORECASE,
)

# MCP tools loaded by `prepare` at startup, and the servers they came from; `build` runs after it.
_mcp_tools: list[BaseTool] = []
_mcp_servers: list[str] = []


def mcp_tools() -> list[BaseTool]:
    """The MCP tools loaded at startup, also offered to the orchestrator (e.g. a quick web search)."""
    return list(_mcp_tools)


def tag_mcp(tools: list[BaseTool]) -> list[BaseTool]:
    """Copies of the Tavily and knowledge (Wikipedia, arXiv) tools tagged as web, and of the
    external database tools tagged external_db, so `scope_tools` can hide them; the loaded objects
    stay untouched."""
    def source(name: str) -> str | None:
        return "web" if name.startswith(("tavily_", "knowledge_")) else "external_db" if name.startswith("external_db_") else None

    return [t.model_copy(update={"metadata": {**(t.metadata or {}), TOOL_SOURCE: source(t.name)}}) if source(t.name) else t
            for t in tools]


async def prepare(stack: AsyncExitStack) -> None:
    remote, local = remote_servers(), local_servers()
    tools = await open_mcp_tools(stack, remote, local)
    _mcp_tools[:] = tag_mcp([tool for tool in tools if tool.name not in SKIPPED_TOOLS])
    # Tool names are "<server>_<tool>", so a server that failed to load has no tools here.
    _mcp_servers[:] = [name for name in [*remote, *local] if any(t.name.startswith(f"{name}_") for t in _mcp_tools)]


class ResearchState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    findings: str


# The `TOOL_SOURCE` tag of each MCP server's tools and of the market-data skill.
SERVER_SOURCE = {"tavily": "web", "knowledge": "web", "external_db": "external_db"}
SKILL_SOURCE = {"market-data": "market_db"}


def sources(servers: list[str], allowed: tuple[str, ...] = ()) -> str:
    """The researcher's source list: loaded MCP servers, then the user's files and the stock data.
    `allowed` (a team request's sources) leaves out the others; empty lists all."""
    def ok(tag: str | None) -> bool:
        return not allowed or tag is None or tag in allowed

    lines = [SOURCE_LINES.get(name, f"- Tools of the {name!r} MCP server ({name}_* tools).")
             for name in servers if ok(SERVER_SOURCE.get(name))]
    return "\n".join([*lines, *([DOCUMENTS_LINE] if ok("user_files") else []), *([MARKET_LINE] if ok("market_db") else [])])


def research_prompt_extra(schema: str):
    """The per-request prompt part: the sources, skills and market schema the request may use.
    Appended, not formatted into a template, because the schema contains braces."""
    def extra(request) -> str:
        allowed = request.runtime.context.sources
        skills = {n: s for n, s in RESEARCHER_SKILLS.items()
                  if not allowed or SKILL_SOURCE.get(n) is None or SKILL_SOURCE[n] in allowed}
        text = SOURCES_HEADING + sources(_mcp_servers, allowed) + "\n\n" + skills_section(skills)
        return text + (SCHEMA_HEADING + schema if not allowed or "market_db" in allowed else "")
    return extra




def findings_from(messages: list[AnyMessage]) -> str:
    """The researcher's notes, or its latest raw tool results if it ran out of steps first."""
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.text and not last.tool_calls and not last.text.startswith(LIMIT_MESSAGE):
        return last.text
    results = [m for m in messages if isinstance(m, ToolMessage)][-RAW_RESULTS:]
    if not results:
        return "NO FINDINGS: the research step produced nothing."
    raw = "\n\n".join(f"[{m.name}]\n{(m.text or str(m.content))[:RAW_RESULT_CHARS]}" for m in results)
    return f"The research was cut short; these are its latest raw tool results:\n\n{raw}\nGAPS: research incomplete."


# A finding's source: [source: <label> | <URL> | "<quote>"], any part may be missing.
_SOURCE = re.compile(r"\[source:\s*([^\]]*)\]")
_URL = re.compile(r"https?://[^\s|\]]+")
_QUOTE = re.compile(r"[\"“]([^\"”]{8,})[\"”]")
# A section heading in wikipedia_page/wikipedia_article output: "## History (https://…/Page#History)".
_SECTION_HEADING = re.compile(r"^#{2,6} (.+) \((https?://\S+#\S+)\)\s*$", re.MULTILINE)
_TITLE_LINE = re.compile(r"^(.+) \((https?://\S+)\)\s*$")
MAX_ARTICLES = 3  # Wikipedia articles fetched per answer to find the sections facts are in
PREFIX_WORDS = 6  # when a quote isn't found whole, its first words may still be
# Characters a text fragment must escape (its own syntax, plus what breaks a Markdown link).
_FRAGMENT_ESCAPES = {c: f"%{ord(c):02X}" for c in " %&,-#()[]<>\"'"}
FRAGMENT_WORDS = 4  # longer quotes become a start,end range, so footnote marks in between don't matter


@dataclass(frozen=True)
class Page:
    """One page the researcher read: its URL (None if unknown), title and the text it got back.
    `article` marks a whole Wikipedia article with its section headings (`wikipedia_article`)."""
    url: str | None
    text: str
    title: str = ""
    article: bool = False


def pages_from(messages: list[AnyMessage]) -> list[Page]:
    """The pages in the researcher's tool results. Tavily returns JSON with one entry per page;
    knowledge_wikipedia_page returns "Title (URL)" and then the text; anything else is kept
    as one page without a URL (still useful for checking quotes)."""
    pages = []
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        text = page_text(m)
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            pages += [Page(r.get("url"), f"{r.get('title') or ''}\n{r.get('raw_content') or r.get('content') or ''}",
                           r.get("title") or "")
                      for r in data["results"] if isinstance(r, dict)]
            continue
        head = _TITLE_LINE.match(text.split("\n", 1)[0]) if (m.name or "").endswith("wikipedia_page") else None
        pages.append(Page(head[2], text, head[1]) if head else Page(None, text))
    return pages


# Source ids: the researcher cites a page by a short id instead of writing its URL (URLs are the
# longest part of its notes, and writing is its slowest step). `cite_by_id` lists the ids of every
# URL in a tool result at its end; `expand_source_ids` turns them back into "title | URL" before
# `link_sources`. An id is a hash of the URL, so a page always gets the same one.
SOURCE_IDS = "\n\n[source ids]"  # starts the list; `page_text` cuts it off again
_SOURCE_ID = re.compile(r"\bS[0-9a-f]{4}\b", re.IGNORECASE)
# Any bracket around an id, however the model wrote it: [S3fa2], (S3fa2 | "quote"), [source S3fa2].
_BRACKETED_ID = re.compile(r"[\[(]([^\[\]()]*\bS[0-9a-f]{4}\b[^\[\]()]*)[\])]", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"https?://\S+")
# An id-like token of any shape (letters and digits, optional hyphens, no spaces), e.g. "cf61ec-00".
_ID_LIKE = re.compile(r"(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def source_id(url: str) -> str:
    return "S" + hashlib.sha1(url.encode()).hexdigest()[:4]


def _clean_url(url: str) -> str:
    """A URL found in text, without trailing punctuation or the ")" around it, but keeping the
    parentheses of titles like Python_(programming_language)."""
    url = url.rstrip(".,;:'\"")
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1].rstrip(".,;:'\"")
    return url


def page_text(message: ToolMessage) -> str:
    """A tool result's text, without its source-id list."""
    return (message.text or str(message.content)).split(SOURCE_IDS, 1)[0]


def source_index(messages: list[AnyMessage]) -> dict[str, tuple[str, str]]:
    """id -> (URL, title) for every URL in the researcher's tool results: Tavily's JSON entries,
    and in text results every URL with the text before it on its line as the title ("Title (URL)",
    "## Section (URL)", "- Linked article (URL)")."""
    index: dict[str, tuple[str, str]] = {}
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        text = page_text(m)
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            entries = [(r["url"], r.get("title") or "") for r in data["results"] if isinstance(r, dict) and r.get("url")]
        else:
            entries = []
            for line in text.splitlines():
                for match in _URL_IN_TEXT.finditer(line):
                    title = line[:match.start()].strip().lstrip("#-* ").rstrip("( ").strip()
                    entries.append((_clean_url(match[0]), title[:100]))
        for url, title in entries:
            index.setdefault(source_id(url), (url, title))
    return index


def source_legend(index: dict[str, tuple[str, str]]) -> str:
    lines = [f"{sid} = {title or url} ({url})" for sid, (url, title) in index.items()]
    return SOURCE_IDS + " cite these pages by id in your notes:\n" + "\n".join(lines)


@wrap_tool_call
async def cite_by_id(request, handler):
    """Lists the ids of the URLs in each tool result at its end, for the researcher to cite."""
    result = await handler(request)
    if isinstance(result, ToolMessage) and (index := source_index([result])):
        legend = source_legend(index)
        content = (result.content + legend if isinstance(result.content, str)
                   else [*result.content, {"type": "text", "text": legend}])
        result = result.model_copy(update={"content": content})
    return result


def _normal_id(sid: str) -> str:
    return "S" + sid[1:].lower()


def _as_label(title: str) -> str:
    return re.sub(r"[\[\]|]", " ", title).strip()


def expand_source_ids(findings: str, index: dict[str, tuple[str, str]]) -> str:
    """[source: S3fa2 | "quote"] -> [source: <title> | <URL> | "quote"], also when the model wrote
    the id another way ([S3fa2], (S3fa2), a bare S3fa2). Unknown ids are dropped (`link_sources`
    still finds the page by its quote), so no id ever reaches the writer, who would invent a URL."""
    def as_source(body: str) -> str:
        body = re.sub(r"^\s*source\s*:?\s*", "", body, flags=re.IGNORECASE)
        ids = [_normal_id(sid) for sid in _SOURCE_ID.findall(body)]
        known = [index[sid] for sid in ids if sid in index]
        rest = _SOURCE_ID.sub("", body).strip(" |,")
        parts = ([f"{_as_label(known[0][1])} | {known[0][0]}"] if known else []) + [url for url, _ in known[1:]]
        return "[source: " + " | ".join(p for p in [*parts, rest] if p.strip(" |")) + "]"

    def without_unknown_ids(m: re.Match) -> str:
        """A whole `|` part of a [source: ...] that looks like an id but is not a known one."""
        parts = m[1].split("|")
        kept = [p for p in parts if not _ID_LIKE.fullmatch(p.strip()) or _normal_id(p.strip()) in index]
        return m[0] if len(kept) == len(parts) else "[source: " + " | ".join(p.strip() for p in kept) + "]"

    findings = _SOURCE.sub(without_unknown_ids, findings)
    findings = _SOURCE.sub(lambda m: as_source(m[1]) if _SOURCE_ID.search(m[1]) else m[0], findings)
    # Ids outside a [source: ...]: in another bracket, or bare.
    findings = _BRACKETED_ID.sub(lambda m: as_source(m[1]) if not m[0].startswith("[source:") else m[0], findings)
    def bare(m: re.Match) -> str:
        if _normal_id(m[0]) in index:
            return as_source(m[0])
        return "" if re.fullmatch(r"S[0-9a-f]{4}", m[0]) else m[0]  # an unknown id; keep words like SDEFA
    findings = _SOURCE_ID.sub(bare, findings)
    return findings.replace("[source: ]", "")


def _is_wikipedia(url: str) -> bool:
    return urlparse(url).netloc.endswith("wikipedia.org")


async def with_wikipedia_articles(findings: str, pages: list[Page]) -> list[Page]:
    """`pages` plus the whole article (with its section headings) of each Wikipedia page the
    notes cite, so every fact can be matched to its section."""
    wanted = list(dict.fromkeys(url.split("#")[0] for url in _URL.findall(findings) if _is_wikipedia(url)))
    texts = await asyncio.gather(*(wikipedia_article(url) for url in wanted[:MAX_ARTICLES]), return_exceptions=True)
    articles = []
    for url, text in zip(wanted, texts):
        if isinstance(text, BaseException):
            log.warning("Could not fetch %s: %s", url, text)
        elif text and (head := _TITLE_LINE.match(text.split("\n", 1)[0])):
            articles.append(Page(head[2], text, head[1], article=True))
    return [*pages, *articles]


def _fragment(text: str) -> str:
    return "".join(_FRAGMENT_ESCAPES.get(c, c) for c in text)


def text_fragment_url(url: str, quote: str) -> str:
    """`url` scrolled to and highlighting `quote` (a `#:~:text=` link), keeping its section anchor."""
    words = quote.strip(" .…").split()
    if not words:
        return url
    if len(words) <= 2 * FRAGMENT_WORDS:
        directive = _fragment(" ".join(words))
    else:
        directive = f"{_fragment(' '.join(words[:FRAGMENT_WORDS]))},{_fragment(' '.join(words[-FRAGMENT_WORDS:]))}"
    return f"{url}{':~:' if '#' in url else '#:~:'}text={directive}"


def _same_page(a: str, b: str) -> bool:
    return unquote(a.split("#")[0]).rstrip("/") == unquote(b.split("#")[0]).rstrip("/")


def _without_text_fragment(url: str) -> str:
    """`url` minus any `:~:text=` part, keeping a section anchor ("#History")."""
    base, _, fragment = url.partition("#")
    anchor = fragment.split(":~:")[0]
    return f"{base}#{anchor}" if anchor else base


def _readable(url: str) -> str:
    """Wikipedia URLs with their title unescaped (…/wiki/Nguyễn_Nhật_Ánh): shorter, so the writer
    copies them correctly. Other sites keep their escaping (it may matter in a query string)."""
    if not _is_wikipedia(url):
        return url
    base, sep, fragment = url.partition("#")
    return unquote(base).replace(" ", "_") + sep + fragment


def _find_quote(quote: str, pages: list[Page], url: str | None) -> tuple[Page, int, str] | None:
    """(page, position, matched text) of `quote`, ignoring whitespace and case differences and
    preferring the page at `url`; falls back to the quote's first words, since models often
    paraphrase the end."""
    words = quote.strip(" .…").split()
    ordered = sorted(pages, key=lambda p: not (url and p.url and _same_page(p.url, url)))
    for candidate in (words, words[:PREFIX_WORDS]):
        if not candidate:
            continue
        pattern = re.compile(r"\s+".join(map(re.escape, candidate)), re.IGNORECASE)
        for page in ordered:
            if m := pattern.search(page.text):
                return page, m.start(), " ".join(m[0].split())
    return None


def _page_by_label(label: str, pages: list[Page]) -> str | None:
    """The URL of the only page whose address contains a word of `label` ("Zalopay article"
    -> https://zalopay.vn/...), for sources the researcher wrote down without their URL."""
    words = {w.casefold() for w in re.findall(r"\w{4,}", label)}
    urls = {p.url for p in pages if p.url and any(w in urlparse(p.url).netloc.casefold() for w in words)}
    return urls.pop() if len(urls) == 1 else None


def link_sources(findings: str, pages: list[Page]) -> str:
    """Rewrite every [source: …] in the notes as [source: <label> | <URL> | passage: <URL>]:
    - <URL> is what paragraphs cite: the Wikipedia section the fact's quote is in (so facts from
      one section share it), or the page itself;
    - the passage link (lists cite it) also highlights the quote, when the quote is on the page;
    - <label> is the page's own title (plus the section name for Wikipedia), so it is in the
      page's language, falling back to the researcher's label.
    A missing URL comes from the page that contains the quote (or whose address matches the
    label). A quote that isn't on any page is dropped, because a wrong fragment highlights nothing."""
    by_url = {p.url.split("#")[0]: p for p in pages if p.url and not p.article}
    articles = {p.url: p for p in pages if p.article}

    def link(m: re.Match) -> str:
        body = m[1]
        url, *more_urls = _URL.findall(body) or [None]
        quote = (_QUOTE.search(body) or [None, None])[1]
        label = _QUOTE.sub("", _URL.sub("", body)).replace("|", " ").strip(" ,–-")
        # Quotes across table cells ("Born | 1955 |") never match the rendered page.
        quote = quote if quote and "|" not in quote else None
        found = _find_quote(quote, pages, url) if quote else None
        if found and found[0].url and not (url and _same_page(url, found[0].url)):
            url = found[0].url
        url = url or _page_by_label(label, pages)
        if not url:
            return f"[source: {label}]" if label else m[0]

        main, passage = _without_text_fragment(url), None
        article = next((a for a in articles.values() if _same_page(a.url, url)), None)
        if article:
            label = article.title
            in_article = _find_quote(quote, [article], None) if quote else None
            if in_article:
                _, pos, words = in_article
                sections = list(_SECTION_HEADING.finditer(article.text, 0, pos))
                if sections:
                    label, main = f"{article.title} – {sections[-1][1]}", sections[-1][2]
                passage = text_fragment_url(main, words)
        else:
            label = (by_url.get(url.split("#")[0]) or Page(None, "")).title or label
        if passage is None and found:
            passage = text_fragment_url(main, found[2])
        parts = [label, _readable(main), f"passage: {_readable(passage)}" if passage else "", *more_urls]
        return f"[source: {' | '.join(part for part in parts if part)}]"
    return _SOURCE.sub(link, findings)


def findings_result(state: dict) -> str:
    return state.get("findings") or "NO FINDINGS: the research step produced nothing."


def build_research_workflow():
    mcp_tools = list(_mcp_tools)
    researcher = build_agent(
        name="research_agent",
        system_prompt=RESEARCHER_SYSTEM.format(
            today=date.today().isoformat(),
            web_rule=WEB_ALONGSIDE_WIKI if "tavily" in _mcp_servers else "",
            memory_rule=MEMORY_RULE,
        ),
        prompt_extra=research_prompt_extra(describe_schema()),
        tools=[*mcp_tools, search_documents, run_sql, make_load_skill_tool(RESEARCHER_SKILLS)],
        middleware=[cite_by_id],
        max_model_calls=get_settings().research_max_model_calls,
    )

    async def gather(state: ResearchState, runtime: Runtime[AgentContext]) -> dict:
        result = await researcher.ainvoke({"messages": state["messages"]}, context=runtime.context)
        messages = result["messages"]
        findings = expand_source_ids(findings_from(messages), source_index(messages))
        pages = await with_wikipedia_articles(findings, pages_from(messages))
        return {"findings": link_sources(findings, pages)}

    graph = StateGraph(ResearchState, context_schema=AgentContext)
    graph.add_node("gather", gather)
    graph.add_edge(START, "gather")
    graph.add_edge("gather", END)
    return graph.compile(name="research")


SPEC = AgentSpec(
    route="research",
    description=ROUTE_DESCRIPTION,
    labels={"en": "Research agent", "vi": "Trợ lý nghiên cứu"},
    build=build_research_workflow,
    message_names=("research_agent",),
    hints=HINTS,
    names=NAMES,
    prepare=prepare,
    silent_steps=("gather",),
    tool_description=TOOL_DESCRIPTION,
    review=REVIEW,
    sources=frozenset({"web", "external_db"}),
    joins="with_others",
    answers_directly=False,
    result=findings_result,
)
