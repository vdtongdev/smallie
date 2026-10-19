"""Upload job: index the file, then write its document card (one model call) into the graph."""

import asyncio
import logging
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.graph import save_card
from app.core.llm import get_chat_model
from app.core.usage import UsageCounter, add_usage
from app.features.documents.images import IMAGE_SUFFIXES, ImageReading
from app.features.documents.ingest import ingest_document
from app.features.documents.prompts import CARD_PROMPT

log = logging.getLogger(__name__)

CARD_CHARS = 6000  # the opening of the file: title, parties, dates, introduction
MAX_ENTITY_CHARS = 80  # longer "names" are sentences the model put in the list


class DocumentCard(BaseModel):
    doc_type: str = Field(max_length=40)
    summary: str = Field(max_length=200)
    entities: list[str] = Field(default_factory=list, max_length=10)


async def process_document(store: BaseStore, document_id: str, user_id: str, path: Path, filename: str) -> None:
    usage = UsageCounter()  # the vision calls; write_card counts its own call
    chunks, readings = await asyncio.to_thread(ingest_document, document_id, user_id, path, filename, [usage])
    if chunks:
        if Path(filename).suffix.lower() in IMAGE_SUFFIXES and readings:
            await save_image_card(document_id, user_id, filename, readings[0])  # the vision call already wrote it
        else:
            await write_card(store, document_id, user_id, filename, chunks)
    try:
        await add_usage(store, user_id, usage)
    except Exception:  # noqa: BLE001
        log.exception("Saving token usage failed")


async def save_image_card(document_id: str, user_id: str, filename: str, reading: ImageReading) -> None:
    """Best effort, like write_card: the image is already searchable without its card."""
    try:
        entities = [e for e in reading.entities if len(e) <= MAX_ENTITY_CHARS]
        await asyncio.to_thread(save_card, user_id, document_id, f"{reading.doc_type}: {reading.summary}", entities)
    except Exception:  # noqa: BLE001
        log.exception("Document card failed for %s", filename)


async def write_card(store: BaseStore, document_id: str, user_id: str, filename: str,
                     chunks: list[Document]) -> bool:
    """Best effort: the file is already searchable, a missing card only loses the graph shortcut."""
    usage = UsageCounter()
    written = False
    try:
        opening = "\n".join(c.page_content for c in chunks)[:CARD_CHARS]
        model = get_chat_model(get_settings().openrouter_orchestrator_model or None)
        card = await model.with_structured_output(DocumentCard, method="function_calling").ainvoke(
            [SystemMessage(CARD_PROMPT), HumanMessage(f"File name: {filename}\n\n{opening}")],
            config={"callbacks": [usage]},
        )
        entities = [e for e in card.entities if len(e) <= MAX_ENTITY_CHARS]
        await asyncio.to_thread(save_card, user_id, document_id, f"{card.doc_type}: {card.summary}", entities)
        written = True
    except Exception:  # noqa: BLE001
        log.exception("Document card failed for %s", filename)
    try:
        await add_usage(store, user_id, usage)
    except Exception:  # noqa: BLE001
        log.exception("Saving token usage failed")
    return written
