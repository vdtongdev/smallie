import asyncio

from langchain.tools import ToolRuntime, tool

from app.features.documents.ingest import get_vector_store
from app.core.memory import AgentContext

TOP_K = 6


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
