import asyncio
import logging
import re
import uuid

from langchain.agents.middleware import before_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select

from app.agents.base import AgentSpec, RouteInfo
from app.agents.docs.prompts import DOCS_SYSTEM, REVIEW, ROUTE_DESCRIPTION, TOOL_DESCRIPTION
from app.agents.docs import tools as docs_tools
from app.agents.docs.tools import NO_PASSAGES, search_documents
from app.agents.factory import build_agent
from app.core.db import documents, get_engine

log = logging.getLogger(__name__)

MAX_LISTED_DOCUMENTS = 30  # file names shown to the orchestrator
MAX_CARDS = 10  # newest files shown with their card summary
MIN_STEM_CHARS = 4  # shorter file names match too many messages
MIN_WORD_CHARS = 5  # words of a file name that count as mentioning it
# File-name words that don't tell one file from a question ("final", "v2"...).
GENERIC_NAME_WORDS = frozenset({"final", "draft", "copy", "version", "update", "updated", "scan", "scanned", "edited"})

HINTS = re.compile(r"\b(documents?|files?|pdfs?|docx|uploads?|uploaded|images?|photos?|pictures?|screen shots?|screenshots?|scans?|ảnh(?!\s*hưởng)|hình ảnh|ảnh chụp|tài liệu|tệp)\b", re.IGNORECASE)
NAMES = re.compile(
    r"\b(?:docs?|documents?|files?|pdfs?) agent\b|\bmy (?:docs?|documents?|files?|pdfs?|uploads?)\b|\buploaded (?:docs?|documents?|files?)\b"
    r"|\b(?:tài liệu|tệp) (?:của (?:tôi|mình)|đã tải)",
    re.IGNORECASE,
)
# Words for a personal file the user may have uploaded, without naming it.
PERSONAL_FILES = re.compile(r"\b(resumes?|résumés?|cv|curriculum vitae|hồ sơ xin việc)\b", re.IGNORECASE)


def document_names(user_id: str) -> list[tuple[str, str | None]]:
    """(file name, card summary or None) of the user's files that are (or will soon be) searchable."""
    with get_engine().connect() as conn:
        return [tuple(row) for row in conn.execute(
            select(documents.c.filename, documents.c.summary)
            .where(documents.c.user_id == user_id, documents.c.status != "failed")
            .order_by(documents.c.created_at.desc())
        )]


async def route_info(user_id: str) -> RouteInfo | None:
    files = await asyncio.to_thread(document_names, user_id)
    if not files:
        return None  # nothing to search
    names = [name for name, _ in files]
    # Summaries are joined into the notes as text, never formatted, so braces in them are safe.
    shown = [f"{name} ({summary})" if summary and i < MAX_CARDS else name
             for i, (name, summary) in enumerate(files[:MAX_LISTED_DOCUMENTS])]
    listed = ", ".join(shown) + (" …" if len(files) > MAX_LISTED_DOCUMENTS else "")
    stems = [stem for stem in (name.rsplit(".", 1)[0].lower() for name in names) if len(stem) >= MIN_STEM_CHARS]
    return RouteInfo(
        prompt_note=f"The user's uploaded documents: {listed}",
        matches=lambda message: any(stem in message.lower() for stem in stems),
        mentions=make_mentions(stems),
    )


def make_mentions(stems: list[str]):
    """A message mentions the files when it has a distinctive word of a file name ("the Almanack",
    "Naval") or asks about a resume/CV."""
    words = {word for stem in stems for word in re.findall(r"[^\W\d_]+", stem)
             if len(word) >= MIN_WORD_CHARS and word not in GENERIC_NAME_WORDS}
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(words))) + r")\b", re.IGNORECASE) if words else None
    return lambda message: bool(PERSONAL_FILES.search(message) or (pattern and pattern.search(message)))


@before_agent
async def prefetch_search(state, runtime):
    """The docs agent's first search runs here, in code, in solo and team mode: a weak model
    ignores a forced tool choice and answers from its own context. The search is put in the
    history as the model's own `search_documents` call and its result."""
    query = next((m.text for m in reversed(state["messages"]) if isinstance(m, HumanMessage) and m.name is None), "")
    if not query:
        return None
    status = "success"
    try:
        found = await asyncio.to_thread(docs_tools.search_with_graph, runtime.context.user_id, query)
    except Exception as error:  # the turn goes on; the model sees the failure like any tool error
        log.warning("Prefetch search failed", exc_info=True)
        found, status = f"Error: {error!r}"[:150], "error"
    call_id = f"prefetch_{uuid.uuid4().hex[:8]}"
    return {"messages": [
        AIMessage("", tool_calls=[{"name": search_documents.name, "args": {"query": query}, "id": call_id}]),
        ToolMessage(found, tool_call_id=call_id, name=search_documents.name, status=status),
    ]}


def build_docs_agent():
    return build_agent(name="docs_agent", system_prompt=DOCS_SYSTEM, tools=[search_documents], route="docs",
                       middleware=[prefetch_search])


SPEC = AgentSpec(
    route="docs",
    description=ROUTE_DESCRIPTION,
    labels={"en": "Documents agent", "vi": "Trợ lý tài liệu"},
    build=build_docs_agent,
    message_names=("docs_agent",),
    hints=HINTS,
    names=NAMES,
    names_via_info=True,
    source_tools=frozenset({"search_documents"}),
    route_info=route_info,
    tool_description=TOOL_DESCRIPTION,
    review=REVIEW,
    sources=frozenset({"user_files"}),
    no_result=lambda text: text == NO_PASSAGES,
)
