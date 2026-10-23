import asyncio
import re

from sqlalchemy import select

from app.agents.base import AgentSpec, RouteInfo
from app.agents.docs.prompts import DOCS_SYSTEM, REVIEW, ROUTE_DESCRIPTION, TOOL_DESCRIPTION
from app.agents.docs.tools import NO_PASSAGES, search_documents
from app.agents.factory import build_agent
from app.core.db import documents, get_engine

MAX_LISTED_DOCUMENTS = 30  # file names shown to the orchestrator
MAX_CARDS = 10  # newest files shown with their card summary
MIN_STEM_CHARS = 4  # shorter file names match too many messages
MIN_WORD_CHARS = 5  # words of a file name that count as mentioning it
# File-name words that don't tell one file from a question ("final", "v2"...).
GENERIC_NAME_WORDS = frozenset({"final", "draft", "copy", "version", "update", "updated", "scan", "scanned", "edited"})

HINTS = re.compile(r"\b(documents?|files?|pdfs?|docx|uploads?|uploaded|tài liệu|tệp)\b", re.IGNORECASE)
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


def build_docs_agent():
    return build_agent(name="docs_agent", system_prompt=DOCS_SYSTEM, tools=[search_documents])


SPEC = AgentSpec(
    route="docs",
    description=ROUTE_DESCRIPTION,
    labels={"en": "Documents agent", "vi": "Trợ lý tài liệu"},
    build=build_docs_agent,
    message_names=("docs_agent",),
    hints=HINTS,
    route_info=route_info,
    tool_description=TOOL_DESCRIPTION,
    review=REVIEW,
    sources=frozenset({"user_files"}),
    no_result=lambda text: text == NO_PASSAGES,
)
