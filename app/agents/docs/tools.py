import asyncio

from langchain.tools import ToolRuntime, tool

from app.core.graph import card_files, documents_for, has_cards
from app.core.llm import get_embeddings
from app.features.documents.ingest import get_vector_store
from app.core.memory import TOOL_SOURCE, AgentContext

TOP_K = 6
CARD_FILES = 3  # files whose card is nearest the question, when nothing names or links a file
CARD_SLOTS = 3  # of the TOP_K passages, kept for those files so a big file can't crowd them out
NO_PASSAGES = "No matching passages in the user's documents."


def search_user_chunks(user_id: str, query: str, k: int = TOP_K, document_ids: list[str] | None = None) -> str:
    """Similarity search restricted to the user's own documents (and to `document_ids` if given)."""
    where = {"user_id": {"$eq": user_id}}
    if document_ids:
        where = {"$and": [where, {"document_id": {"$in": document_ids}}]}
    return _format(get_vector_store().similarity_search(query, k=k, filter=where))


def _format(hits) -> str:
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


def search_with_graph(user_id: str, query: str, entity: str = "") -> str:
    """Passages from the files the graph links to `entity` (or to names in the query); every file
    when the graph knows none of them."""
    scope = documents_for(user_id, query, entity)
    found = search_user_chunks(user_id, query, document_ids=scope) if scope else NO_PASSAGES
    if found != NO_PASSAGES:
        return found
    if not has_cards(user_id):
        return search_user_chunks(user_id, query)
    return _search_by_cards(user_id, query)


def _search_by_cards(user_id: str, query: str) -> str:
    """Reserved slots for the files whose card is nearest, then the plain search for the rest."""
    vector = get_embeddings().embed_query(query)  # the one embedding of this search
    where = {"user_id": {"$eq": user_id}}
    store = get_vector_store()
    carded = store.similarity_search_by_vector(vector, k=TOP_K, filter={"$and": [where, {"document_id": {
        "$in": card_files(user_id, vector, CARD_FILES)}}]})
    hits, seen = [], set()
    for doc in [*carded[:CARD_SLOTS], *store.similarity_search_by_vector(vector, k=TOP_K, filter=where)]:
        key = (doc.metadata.get("document_id"), doc.page_content)
        if key not in seen and len(hits) < TOP_K:
            seen.add(key)
            hits.append(doc)
    return _format(hits)


@tool
async def search_documents(query: str, runtime: ToolRuntime[AgentContext], entity: str = "") -> str:
    """Search the user's uploaded PDF/DOCX documents for passages relevant to the query.

    Args:
        query: A focused search phrase. Works in English or Vietnamese.
        entity: The person, company, place or product the question is about, if any. The search
            then looks first in the files linked to it. Leave empty otherwise.
    """
    return await asyncio.to_thread(search_with_graph, runtime.context.user_id, query, entity)


search_documents.metadata = {TOOL_SOURCE: "user_files"}
