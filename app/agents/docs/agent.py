import asyncio
import re

from sqlalchemy import select

from app.agents.base import AgentSpec, RouteInfo
from app.agents.docs.prompts import DOCS_SYSTEM, ROUTE_DESCRIPTION
from app.agents.docs.tools import search_documents
from app.agents.factory import build_agent
from app.core.db import documents, get_engine

MAX_LISTED_DOCUMENTS = 30  # file names shown to the router
MIN_STEM_CHARS = 4  # shorter file names match too many messages

HINTS = re.compile(r"\b(documents?|files?|pdfs?|docx|uploads?|uploaded|tài liệu|tệp)\b", re.IGNORECASE)


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
