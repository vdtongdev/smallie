"""Upload job: index the file, then write its document card (one model call) into the graph."""

import asyncio
import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

from app.core.graph import save_card
from app.core.llm import ask_structured, get_chat_model
from app.core.usage import UsageCounter, add_usage
from app.features.documents.images import images_in, read_images
from app.features.documents.ingest import image_chunks, index_document, ingest_document
from app.features.documents.prompts import CARD_PROMPT

log = logging.getLogger(__name__)

CARD_CHARS = 6000  # the opening of the file: title, parties, dates, introduction
CARD_TEXT_CHARS = 4500  # of those, the most the text takes when the file also has image readings


class DocumentCard(BaseModel):
    doc_type: str = Field(max_length=40)
    summary: str = Field(max_length=200)
    entities: list[str] = Field(default_factory=list, max_length=10)


def card_opening(chunks: list[Document]) -> str:
    """What the card model reads: the text, then the image readings in the space the text leaves."""
    text = "\n".join(c.page_content for c in chunks if c.metadata.get("kind") != "image")
    readings = "\n".join(c.page_content for c in chunks if c.metadata.get("kind") == "image")
    if not readings:
        return text[:CARD_CHARS]
    return "\n".join(part for part in (text[:CARD_TEXT_CHARS], readings) if part)[:CARD_CHARS]


async def index_file(document_id: str, user_id: str, path: Path, filename: str, usage: UsageCounter) -> list[Document]:
    """Text chunks, image readings, then the index; returns every chunk ([] when the file failed)."""
    text_chunks = await asyncio.to_thread(ingest_document, document_id, user_id, path, filename)
    if text_chunks is None:
        return []
    try:
        found = await asyncio.to_thread(images_in, path)
    except Exception:  # noqa: BLE001 - a file whose images can't be extracted is still indexed by its text
        log.exception("Extracting images failed for %s", filename)
        found = []
    readings: list[str | None] = []
    if found:
        try:
            readings = await read_images(user_id, [jpeg for _, jpeg in found], [usage])
        except Exception:  # noqa: BLE001
            log.exception("Reading images failed for %s", filename)
            readings = [None] * len(found)
    chunks = image_chunks(readings, [page for page, _ in found], filename)
    read = sum(1 for r in readings if r and r.strip())
    return await asyncio.to_thread(index_document, document_id, user_id, text_chunks, chunks, read, len(found) - read)


async def process_document(store: BaseStore, document_id: str, user_id: str, path: Path, filename: str) -> None:
    usage = UsageCounter()
    chunks = await index_file(document_id, user_id, path, filename, usage)
    if chunks:
        await write_card(store, document_id, user_id, filename, chunks, usage)
    else:
        await add_usage(store, user_id, usage)


async def write_card(store: BaseStore, document_id: str, user_id: str, filename: str,
                     chunks: list[Document], usage: UsageCounter | None = None) -> bool:
    """Best effort: the file is already searchable, a missing card only loses the graph shortcut."""
    usage = usage or UsageCounter()
    written = False
    try:
        opening = card_opening(chunks)
        model = await asyncio.to_thread(get_chat_model, user_id)
        card = await ask_structured(
            model, DocumentCard, [SystemMessage(CARD_PROMPT), HumanMessage(f"File name: {filename}\n\n{opening}")], {"callbacks": [usage]})
        if card is not None:
            entities = [e for e in card.entities if len(e) <= 80]
            await asyncio.to_thread(save_card, user_id, document_id, f"{card.doc_type}: {card.summary}", entities)
            written = True
    except Exception:  # noqa: BLE001
        log.exception("Document card failed for %s", filename)
    try:
        await add_usage(store, user_id, usage)
    except Exception:  # noqa: BLE001
        log.exception("Saving token usage failed")
    return written
