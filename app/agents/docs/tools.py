import asyncio
from collections.abc import Sequence

from langchain.tools import ToolRuntime, tool

from app.core.graph import card_files, documents_for, has_cards
from app.core.llm import get_embeddings
from app.features.documents.ingest import get_vector_store
from app.core.memory import TOOL_SOURCE, AgentContext

TOP_K = 6
CARD_FILES = 3  # files whose card is nearest the question, when nothing names or links a file;
# each gets one of the TOP_K passages, so a big file's many close passages can't crowd the others out
NO_PASSAGES = "No matching passages in the user's documents."
MAX_IMAGE_CHARS = 6000  # whole-image text one search adds; cut at a part boundary, one part may pass it
IMAGE_PARTS_FETCHED = 50


def search_user_chunks(user_id: str, query: str, k: int = TOP_K, document_ids: list[str] | None = None) -> str:
    """Similarity search restricted to the user's own documents (and to `document_ids` if given)."""
    where = {"user_id": {"$eq": user_id}}
    if document_ids:
        where = {"$and": [where, {"document_id": {"$in": document_ids}}]}
    return _format(whole_images(get_vector_store().similarity_search(query, k=k, filter=where), user_id, query))


def _part_order(doc) -> int:
    if "part" in doc.metadata:
        return doc.metadata["part"]
    tail = (doc.id or "").rsplit(":", 1)[-1]  # old uploads: the chunk id's number
    return int(tail) if tail.isdigit() else 0


def whole_images(hits, user_id: str, query: str, vector=None) -> list:
    """Each image hit becomes one Document with all parts of that image in reading order (label once);
    other hits stay. Two hits on one image collapse. Added text is capped by MAX_IMAGE_CHARS per call."""
    store, budget, done, out = get_vector_store(), MAX_IMAGE_CHARS, set(), []
    for hit in hits:
        meta = hit.metadata
        if meta.get("kind") != "image" or "document_id" not in meta or "image" not in meta:
            out.append(hit)
            continue
        vector = vector or get_embeddings().embed_query(query)  # only when a hit is an image
        key = (meta["document_id"], meta["image"])
        if key in done:
            continue
        done.add(key)
        where = {"$and": [{"user_id": {"$eq": user_id}}, {"document_id": {"$eq": key[0]}},
                          {"kind": {"$eq": "image"}}, {"image": {"$eq": key[1]}}]}
        parts = sorted(store.similarity_search_by_vector(vector, k=IMAGE_PARTS_FETCHED, filter=where),
                       key=_part_order) or [hit]
        label = parts[0].page_content.split("\n", 1)[0]
        texts = []
        for part in parts:
            if texts and budget <= 0:
                break
            text = part.page_content
            if texts and text.startswith(label + "\n"):
                text = text[len(label) + 1:]
            texts.append(text)
            budget -= len(text)
        out.append(type(hit)(page_content="\n".join(texts), metadata=meta))
    return out


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


def search_with_graph(user_id: str, query: str, entity: str = "", attached: Sequence[str] = ()) -> str:
    """Passages from the `attached` files of this message when they hold any; else from the files
    the graph links to `entity` (or to names in the query); every file when the graph knows none."""
    if attached:
        found = search_user_chunks(user_id, query, document_ids=list(attached))  # filtered by user_id: other users' ids give nothing
        if found != NO_PASSAGES:
            return found
    scope = documents_for(user_id, query, entity)
    found = search_user_chunks(user_id, query, document_ids=scope) if scope else NO_PASSAGES
    if found != NO_PASSAGES:
        return found
    if not has_cards(user_id):
        return search_user_chunks(user_id, query)
    return _search_by_cards(user_id, query)


def _search_by_cards(user_id: str, query: str) -> str:
    """The best passage of each file whose card is nearest, then the plain search for the rest."""
    vector = get_embeddings().embed_query(query)  # the one embedding of this search
    where = {"user_id": {"$eq": user_id}}
    store = get_vector_store()
    carded = [hit for document_id in card_files(user_id, vector, CARD_FILES)
              for hit in store.similarity_search_by_vector(vector, k=1, filter={"$and": [where, {"document_id": {
                  "$eq": document_id}}]})]
    hits, seen = [], set()
    for doc in [*carded, *store.similarity_search_by_vector(vector, k=TOP_K, filter=where)]:
        key = (doc.metadata.get("document_id"), doc.page_content)
        if key not in seen and len(hits) < TOP_K:
            seen.add(key)
            hits.append(doc)
    return _format(whole_images(hits, user_id, query, vector))


@tool
async def search_documents(query: str, runtime: ToolRuntime[AgentContext], entity: str = "") -> str:
    """Search the user's uploaded PDF, DOCX and image documents for passages relevant to the query.

    Args:
        query: A focused search phrase. Works in English or Vietnamese.
        entity: The person, company, place or product the question is about, if any. The search
            then looks first in the files linked to it. Leave empty otherwise.
    """
    return await asyncio.to_thread(search_with_graph, runtime.context.user_id, query, entity,
                                   runtime.context.attached_documents)


search_documents.metadata = {TOOL_SOURCE: "user_files"}
