import asyncio

from langchain.tools import ToolRuntime, tool

from app.core.graph import documents_for
from app.features.documents.ingest import get_vector_store
from app.core.memory import AgentContext

TOP_K = 6
NO_PASSAGES = "No matching passages in the user's documents."


def search_user_chunks(user_id: str, query: str, k: int = TOP_K, document_ids: list[str] | None = None,
                       kind: str | None = None) -> str:
    """Similarity search restricted to the user's own documents (and to `document_ids` and chunks of
    `kind`, e.g. "image" readings, if given)."""
    where = {"user_id": {"$eq": user_id}}
    narrower = ([{"document_id": {"$in": document_ids}}] if document_ids else []) + (
        [{"kind": {"$eq": kind}}] if kind else [])
    if narrower:
        where = {"$and": [where, *narrower]}
    hits = get_vector_store().similarity_search(query, k=k, filter=where)
    if not hits:
        return NO_PASSAGES
    parts = []
    for doc in hits:
        meta = doc.metadata
        source = meta.get("filename", "unknown")
        if meta.get("page"):
            source += f", p. {meta['page']}"
        parts.append(f"[{source}]\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


def search_with_graph(user_id: str, query: str, entity: str = "", kind: str | None = None) -> str:
    """Passages from the files the graph links to `entity` (or to names in the query); every file
    when the graph knows none of them. `kind` holds for both searches."""
    scope = documents_for(user_id, query, entity)
    found = search_user_chunks(user_id, query, document_ids=scope, kind=kind) if scope else NO_PASSAGES
    return found if found != NO_PASSAGES else search_user_chunks(user_id, query, kind=kind)


@tool
async def search_documents(query: str, runtime: ToolRuntime[AgentContext], entity: str = "") -> str:
    """Search the user's uploaded files (PDF, Word and images) for passages relevant to the query.
    An image's passage is the description and text it was read into when uploaded.

    Args:
        query: A focused search phrase. Works in English or Vietnamese.
        entity: The person, company, place or product the question is about, if any. The search
            then looks first in the files linked to it. Leave empty otherwise.
    """
    return await asyncio.to_thread(search_with_graph, runtime.context.user_id, query, entity)
