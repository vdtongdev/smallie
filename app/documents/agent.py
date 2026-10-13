import asyncio
import re

from langchain.tools import ToolRuntime, tool
from sqlalchemy import select

from app.agent_factory import build_agent
from app.agents.base import AgentSpec, RouteInfo
from app.db import documents, get_engine
from app.documents.ingest import get_vector_store
from app.memory import AgentContext
from app.prompts import DOCS_SYSTEM

TOP_K = 6
MAX_LISTED_DOCUMENTS = 30  # file names shown to the router
MIN_STEM_CHARS = 4  # shorter file names match too many messages

ROUTE_DESCRIPTION = (
    "answers from the user's uploaded files (PDF/DOCX): what a document says, summaries, facts, "
    "policies, contracts or anything else that would be written in their files."
)
HINTS = re.compile(r"\b(documents?|files?|pdfs?|docx|uploads?|uploaded|tài liệu|tệp)\b", re.IGNORECASE)


def search_user_chunks(user_id: str, query: str, k: int = TOP_K) -> str:
    """Similarity search restricted to the user's own documents."""
    hits = get_vector_store().similarity_search(query, k=k, filter={"user_id": {"$eq": user_id}})
    if not hits:
        return "No matching passages in the user's documents."
    parts = []
    for doc in hits:
        meta = doc.metadata
        source = meta.get("filename", "unknown")
        if meta.get("page"):
            source += f", p. {meta['page']}"
        parts.append(f"[{source}]\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


@tool
async def search_documents(query: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Search the user's uploaded PDF/DOCX documents for passages relevant to the query.

    Args:
        query: A focused search phrase. Works in English or Vietnamese.
    """
    return await asyncio.to_thread(search_user_chunks, runtime.context.user_id, query)


def document_names(user_id: str) -> list[str]:
    """The user's uploaded files that are (or will soon be) searchable."""
    with get_engine().connect() as conn:
        return list(conn.execute(
            select(documents.c.filename)
            .where(documents.c.user_id == user_id, documents.c.status != "failed")
            .order_by(documents.c.created_at.desc())
        ).scalars())


async def route_info(user_id: str) -> RouteInfo | None:
    names = await asyncio.to_thread(document_names, user_id)
    if not names:
        return None  # nothing to search
    listed = ", ".join(names[:MAX_LISTED_DOCUMENTS]) + (" …" if len(names) > MAX_LISTED_DOCUMENTS else "")
    stems = [stem for stem in (name.rsplit(".", 1)[0].lower() for name in names) if len(stem) >= MIN_STEM_CHARS]
    return RouteInfo(
        prompt_note=f"The user's uploaded documents: {listed}",
        matches=lambda message: any(stem in message.lower() for stem in stems),
    )


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
)
